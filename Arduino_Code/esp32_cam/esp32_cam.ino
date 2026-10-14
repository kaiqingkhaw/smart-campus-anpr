#include "esp_camera.h"
#include <WiFi.h>
#include "board_config.h"

// -----------------------------------------------------------------------------
// Wi-Fi credentials
// -----------------------------------------------------------------------------
const char *ssid = "YOUR_WIFI_NAME";
const char *password = "YOUR_WIFI_PASSWORD";

// -----------------------------------------------------------------------------
// Forward declarations
// -----------------------------------------------------------------------------
void startCameraServer();
void setupLedFlash();

void setup() {
  Serial.begin(115200);
  Serial.setDebugOutput(true);
  Serial.println();
  Serial.println("Initialising ESP32-CAM for ANPR still-image capture...");

  // ---------------------------------------------------------------------------
  // Camera configuration structure
  // ---------------------------------------------------------------------------
  camera_config_t config;
  config.ledc_channel = LEDC_CHANNEL_0;
  config.ledc_timer = LEDC_TIMER_0;

  config.pin_d0 = Y2_GPIO_NUM;
  config.pin_d1 = Y3_GPIO_NUM;
  config.pin_d2 = Y4_GPIO_NUM;
  config.pin_d3 = Y5_GPIO_NUM;
  config.pin_d4 = Y6_GPIO_NUM;
  config.pin_d5 = Y7_GPIO_NUM;
  config.pin_d6 = Y8_GPIO_NUM;
  config.pin_d7 = Y9_GPIO_NUM;

  config.pin_xclk = XCLK_GPIO_NUM;
  config.pin_pclk = PCLK_GPIO_NUM;
  config.pin_vsync = VSYNC_GPIO_NUM;
  config.pin_href = HREF_GPIO_NUM;

  config.pin_sccb_sda = SIOD_GPIO_NUM;
  config.pin_sccb_scl = SIOC_GPIO_NUM;

  config.pin_pwdn = PWDN_GPIO_NUM;
  config.pin_reset = RESET_GPIO_NUM;

  // Standard clock frequency for ESP32-CAM modules
  config.xclk_freq_hz = 20000000;

  // JPEG mode is required for high-resolution still-image capture
  config.pixel_format = PIXFORMAT_JPEG;

  // ---------------------------------------------------------------------------
  // Frame buffer and resolution settings
  // ---------------------------------------------------------------------------
  // SXGA is chosen as the best balance between clarity and stability for ANPR.
  // Although UXGA is larger, SXGA often gives more reliable results on ESP32-CAM.
  if (psramFound()) {
    config.frame_size = FRAMESIZE_SXGA;          // 1280 x 1024
    config.jpeg_quality = 10;                    // Lower number = better image quality
    config.fb_count = 2;                         // Better frame handling
    config.grab_mode = CAMERA_GRAB_LATEST;
    config.fb_location = CAMERA_FB_IN_PSRAM;
  } else {
    config.frame_size = FRAMESIZE_SVGA;          // 800 x 600 fallback
    config.jpeg_quality = 12;
    config.fb_count = 1;
    config.grab_mode = CAMERA_GRAB_WHEN_EMPTY;
    config.fb_location = CAMERA_FB_IN_DRAM;
  }

#if defined(CAMERA_MODEL_ESP_EYE)
  pinMode(13, INPUT_PULLUP);
  pinMode(14, INPUT_PULLUP);
#endif

  // ---------------------------------------------------------------------------
  // Initialise the camera
  // ---------------------------------------------------------------------------
  esp_err_t err = esp_camera_init(&config);
  if (err != ESP_OK) {
    Serial.printf("Camera initialisation failed with error 0x%x\n", err);
    return;
  }

  Serial.println("Camera initialised successfully.");

  // ---------------------------------------------------------------------------
  // Sensor tuning
  // ---------------------------------------------------------------------------
  sensor_t *s = esp_camera_sensor_get();

  // Keep the configured resolution after initialisation
  if (psramFound()) {
    s->set_framesize(s, FRAMESIZE_SXGA);
  } else {
    s->set_framesize(s, FRAMESIZE_SVGA);
  }

  // ---------------------------------------------------------------------------
  // Image tuning for licence plate clarity
  // ---------------------------------------------------------------------------
  // These settings aim to improve OCR readability while avoiding excessive noise.
  s->set_brightness(s, 1);        // Slightly brighter image
  s->set_contrast(s, 2);          // Helps characters stand out
  s->set_saturation(s, -2);       // Reduces colour noise
  s->set_sharpness(s, 2);         // Improves edge detail
  s->set_denoise(s, 0);           // Prevents excessive smoothing
  s->set_gainceiling(s, GAINCEILING_16X);

  // Automatic image controls
  s->set_whitebal(s, 1);
  s->set_awb_gain(s, 1);
  s->set_gain_ctrl(s, 1);
  s->set_exposure_ctrl(s, 1);
  s->set_aec2(s, 0);

  // Optional adjustment for OV3660 sensor behaviour
  if (s->id.PID == OV3660_PID) {
    s->set_vflip(s, 1);
  }

  // Board-specific image orientation corrections
#if defined(CAMERA_MODEL_M5STACK_WIDE) || defined(CAMERA_MODEL_M5STACK_ESP32CAM)
  s->set_vflip(s, 1);
  s->set_hmirror(s, 1);
#endif

#if defined(CAMERA_MODEL_ESP32S3_EYE)
  s->set_vflip(s, 1);
#endif

  // ---------------------------------------------------------------------------
  // Optional flash LED setup
  // ---------------------------------------------------------------------------
#if defined(LED_GPIO_NUM)
  setupLedFlash();
#endif

  // ---------------------------------------------------------------------------
  // Connect to Wi-Fi
  // ---------------------------------------------------------------------------
  WiFi.begin(ssid, password);
  WiFi.setSleep(false);

  Serial.print("Connecting to Wi-Fi");
  while (WiFi.status() != WL_CONNECTED) {
    delay(500);
    Serial.print(".");
  }

  Serial.println();
  Serial.println("Wi-Fi connected.");
  Serial.print("IP address: ");
  Serial.println(WiFi.localIP());

  // ---------------------------------------------------------------------------
  // Start the web server
  // ---------------------------------------------------------------------------
  startCameraServer();

  Serial.println("Camera server started.");
  Serial.println("Use the following URL for full still-image capture:");
  Serial.print("http://");
  Serial.print(WiFi.localIP());
  Serial.println("/capture");
  Serial.println("For ANPR, use /capture instead of the stream preview.");
}

void loop() {
  // The web server handles image capture tasks.
  delay(10000);
}