#include <Servo.h>
#include <Wire.h>
#include <LiquidCrystal_I2C.h>

Servo gateServo;
LiquidCrystal_I2C lcd(0x27, 16, 2);

// --------------------------------------------------
// Pin configuration
// --------------------------------------------------
const int SERVO_PIN  = 9;
const int TRIG_PIN   = 10;
const int ECHO_PIN   = 11;

const int RED_PIN    = 2;
const int YELLOW_PIN = 3;
const int GREEN_PIN  = 4;

// --------------------------------------------------
// Gate angle and detection settings
// --------------------------------------------------
const int CLOSED_ANGLE    = 0;
const int OPEN_ANGLE      = 85;
const int DETECT_DISTANCE = 30;  // cm — anything closer triggers detection

// --------------------------------------------------
// Timing constants (milliseconds)
// --------------------------------------------------
const unsigned long HOLD_TIME   = 2500;  // how long gate stays open
const unsigned long DENIED_TIME = 3000;  // how long denied/full msg stays up

// --------------------------------------------------
// System state flags
// --------------------------------------------------
bool vehicleDetected     = false;
bool waitingVerification = false;
bool gateOpen            = false;
bool deniedShown         = false;

// --------------------------------------------------
// Misc variables
// --------------------------------------------------
int distance = 999;

unsigned long gateOpenTime = 0;
unsigned long deniedTime   = 0;


// --------------------------------------------------
// Clear leftover bytes in serial before we listen
// for a new response from Python
// --------------------------------------------------
void clearSerialBuffer() {
  while (Serial.available() > 0) {
    Serial.read();
  }
}

// --------------------------------------------------
// Turn off all three LEDs at once
// --------------------------------------------------
void allLightsOff() {
  digitalWrite(RED_PIN,    LOW);
  digitalWrite(YELLOW_PIN, LOW);
  digitalWrite(GREEN_PIN,  LOW);
}

// --------------------------------------------------
// Truncate text to 16 chars so it fits on one LCD row
// --------------------------------------------------
String fitToLcd(String text) {
  text.trim();
  if (text.length() > 16) {
    return text.substring(0, 16);
  }
  return text;
}

// --------------------------------------------------
// Default idle screen — shown at startup and after
// each access attempt is fully handled
// --------------------------------------------------
void showReadyScreen() {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("System Ready");
  lcd.setCursor(0, 1);
  lcd.print("Waiting...");
  allLightsOff();
}

// --------------------------------------------------
// Shown while waiting for Python ANPR result
// Yellow light = processing in progress
// --------------------------------------------------
void showCheckingState() {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Vehicle Found");
  lcd.setCursor(0, 1);
  lcd.print("Checking...");

  digitalWrite(RED_PIN,    LOW);
  digitalWrite(YELLOW_PIN, HIGH);
  digitalWrite(GREEN_PIN,  LOW);
}

// --------------------------------------------------
// Shown when entry access is granted
// Displays plate on row 1, category on row 2
// Green light = access allowed
// --------------------------------------------------
void showGrantedState(String plate, String category) {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(fitToLcd(plate));
  lcd.setCursor(0, 1);
  lcd.print(fitToLcd(category));

  digitalWrite(RED_PIN,    LOW);
  digitalWrite(YELLOW_PIN, LOW);
  digitalWrite(GREEN_PIN,  HIGH);
}

// --------------------------------------------------
// Shown when a vehicle is exiting
// Displays plate on row 1, goodbye message on row 2
// Green light = exit allowed
// --------------------------------------------------
void showExitState(String plate) {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print(fitToLcd(plate));
  lcd.setCursor(0, 1);
  lcd.print("Goodbye!");

  digitalWrite(RED_PIN,    LOW);
  digitalWrite(YELLOW_PIN, LOW);
  digitalWrite(GREEN_PIN,  HIGH);
}

// --------------------------------------------------
// Shown when plate is not found in the database
// Red light = access blocked
// --------------------------------------------------
void showDeniedState() {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Access Denied");
  lcd.setCursor(0, 1);
  lcd.print("Gate Closed");

  digitalWrite(RED_PIN,    HIGH);
  digitalWrite(YELLOW_PIN, LOW);
  digitalWrite(GREEN_PIN,  LOW);
}

// --------------------------------------------------
// Shown when plate is valid but no parking slots left
// Kept separate from denied so driver knows the reason
// Red light = cannot enter
// --------------------------------------------------
void showParkingFullState(String category) {
  lcd.clear();
  lcd.setCursor(0, 0);
  lcd.print("Parking Full");
  lcd.setCursor(0, 1);
  lcd.print(fitToLcd(category));

  digitalWrite(RED_PIN,    HIGH);
  digitalWrite(YELLOW_PIN, LOW);
  digitalWrite(GREEN_PIN,  LOW);
}


// --------------------------------------------------
// Ultrasonic distance reading
// Returns 999 if no echo received (out of range)
// --------------------------------------------------
int readDistanceCm() {
  digitalWrite(TRIG_PIN, LOW);
  delayMicroseconds(3);
  digitalWrite(TRIG_PIN, HIGH);
  delayMicroseconds(10);
  digitalWrite(TRIG_PIN, LOW);

  long pulse = pulseIn(ECHO_PIN, HIGH, 30000);

  if (pulse == 0) {
    return 999;  // no echo — treat as nothing detected
  }

  return pulse * 0.034 / 2;
}


// --------------------------------------------------
// Open gate and set the hold timer
// Used by both entry and exit so kept as a helper
// --------------------------------------------------
void openGate() {
  gateServo.write(OPEN_ANGLE);
  gateOpen            = true;
  waitingVerification = false;
  gateOpenTime        = millis();
}


// --------------------------------------------------
// Setup
// --------------------------------------------------
void setup() {
  Serial.begin(9600);
  Serial.setTimeout(5000);  // give Python enough time to finish ANPR processing

  gateServo.attach(SERVO_PIN);
  gateServo.write(CLOSED_ANGLE);  // always start with gate closed on power up

  pinMode(TRIG_PIN, OUTPUT);
  pinMode(ECHO_PIN, INPUT);

  pinMode(RED_PIN,    OUTPUT);
  pinMode(YELLOW_PIN, OUTPUT);
  pinMode(GREEN_PIN,  OUTPUT);

  lcd.init();
  lcd.backlight();

  showReadyScreen();
}


// --------------------------------------------------
// Main loop
// --------------------------------------------------
void loop() {
  distance = readDistanceCm();

  // Only print distance when idle — avoids flooding the serial
  // buffer while we are waiting for a response from Python
  if (!waitingVerification) {
    Serial.print("Distance: ");
    Serial.println(distance);
  }

  // --------------------------------------------------
  // Step 1: vehicle just entered detection range
  // All flags must be clear before we trigger again
  // --------------------------------------------------
  if (distance < DETECT_DISTANCE
      && !vehicleDetected
      && !waitingVerification
      && !gateOpen
      && !deniedShown) {

    vehicleDetected     = true;
    waitingVerification = true;

    clearSerialBuffer();   // dump any stale bytes before listening
    showCheckingState();

    // Python will check the database to decide if this is
    // an entry or exit based on current parking records
    Serial.println("CAR_DETECTED");
  }

  // --------------------------------------------------
  // Step 2: wait for Python to send back a decision
  //
  // Expected formats:
  //   GRANTED|ABC1234|Staff        → new vehicle entering
  //   EXIT_GRANTED|ABC1234|Staff   → registered vehicle exiting
  //   DENIED                       → plate not in database
  //   PARKING_FULL|Visitors        → authorised but no slots left
  // --------------------------------------------------
  if (waitingVerification && Serial.available() > 0) {
    String command = Serial.readStringUntil('\n');
    command.trim();

    // Echo back what was received — helpful during testing
    Serial.print("Received: [");
    Serial.print(command);
    Serial.println("]");

    // --- Entry access granted ---
    if (command.startsWith("GRANTED|")) {
      // Pull plate number and category from the pipe-delimited string
      int firstSep  = command.indexOf('|');
      int secondSep = command.indexOf('|', firstSep + 1);

      String plate    = "APPROVED";
      String category = "User";

      if (firstSep != -1 && secondSep != -1) {
        plate    = command.substring(firstSep + 1, secondSep);
        category = command.substring(secondSep + 1);
      }

      openGate();
      showGrantedState(plate, category);
    }

    // --- Exit granted — vehicle currently parked is leaving ---
    else if (command.startsWith("EXIT_GRANTED|")) {
      // Python already updated exited_at and parking count
      // Arduino just needs to open the gate and say goodbye
      int firstSep  = command.indexOf('|');
      int secondSep = command.indexOf('|', firstSep + 1);

      String plate = "EXIT OK";

      if (firstSep != -1) {
        // Use plate if available, category not needed for display
        plate = (secondSep != -1)
                ? command.substring(firstSep + 1, secondSep)
                : command.substring(firstSep + 1);
      }

      openGate();
      showExitState(plate);
    }

    // --- Access denied — plate not found in database ---
    else if (command == "DENIED") {
      gateServo.write(CLOSED_ANGLE);
      gateOpen            = false;
      waitingVerification = false;
      deniedShown         = true;
      deniedTime          = millis();

      showDeniedState();
    }

    // --- Parking full — plate is registered but no slots available ---
    else if (command.startsWith("PARKING_FULL|")) {
      int sep         = command.indexOf('|');
      String category = (sep != -1) ? command.substring(sep + 1) : "Parking Full";

      gateServo.write(CLOSED_ANGLE);
      gateOpen            = false;
      waitingVerification = false;
      deniedShown         = true;
      deniedTime          = millis();

      showParkingFullState(category);
    }
  }

  // --------------------------------------------------
  // Step 3: gate has been open long enough — close it
  // Reset detection so the next vehicle can be handled
  // --------------------------------------------------
  if (gateOpen && (millis() - gateOpenTime >= HOLD_TIME)) {
    gateServo.write(CLOSED_ANGLE);
    gateOpen        = false;
    vehicleDetected = false;

    showReadyScreen();
  }

  // --------------------------------------------------
  // Step 4: denied or parking full message timer expired
  // Return to idle so system is ready for the next car
  // --------------------------------------------------
  if (deniedShown && (millis() - deniedTime >= DENIED_TIME)) {
    deniedShown     = false;
    vehicleDetected = false;

    showReadyScreen();
  }

  delay(200);
}