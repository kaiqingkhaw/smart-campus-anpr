# realtime_anpr_bridge.py
# Connects Arduino (serial) to the ANPR pipeline.
#
# Flow:
#   Arduino detects car -> sends CAR_DETECTED over serial
#   This script captures image from ESP32-CAM
#   Runs YOLO + OCR to read the plate
#   Checks database for entry / exit / denied
#   Shows live preview window
#   Sends result back to Arduino
#
# Arduino expects one of these messages:
#   GRANTED|<PLATE>|<CATEGORY>      -- new vehicle entering
#   EXIT_GRANTED|<PLATE>|<CATEGORY> -- vehicle that was parked is leaving
#   DENIED                          -- plate not in database
#   PARKING_FULL|<CATEGORY>         -- registered but no slots left

from __future__ import annotations

import os
import csv
import time
import threading
from datetime import datetime

import cv2
import numpy as np
import requests
import serial
from ultralytics import YOLO

from anpr_full_system import (
    build_roi_candidates,
    log_access,
    release_parking_slot,
    is_currently_parked,
    find_authorised_vehicle,
    occupy_parking_slot,
    get_parking_state,
    get_model,
)
from anpr_ocr_module import extract_plate_text, normalise_plate
from db import get_conn

# -------------------------------------------------------------------
# Hardware settings -- edit these to match your setup
# -------------------------------------------------------------------

SERIAL_PORT = os.getenv("SERIAL_PORT", "COM5")
BAUD_RATE   = int(os.getenv("BAUD_RATE", "9600"))
ESP32_IP    = os.getenv("ESP32_IP", "192.168.1.160")

CAPTURE_URL     = f"http://{ESP32_IP}/capture"
CAPTURE_DIR     = "captures"
CODE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CODE_DIR, ".."))

MODEL_PATH = os.path.join(CODE_DIR, "best.pt")
RESPONSE_TIME_CSV = os.path.join(PROJECT_ROOT, "Results", "response_time_log.csv")

SERIAL_TIMEOUT  = 1
HTTP_TIMEOUT    = 5
CAPTURE_RETRIES = 1
STARTUP_DELAY   = 5.0   # seconds to wait after opening serial (Arduino resets)
LOOP_DELAY      = 0.05
SEND_DELAY      = 0.5   # short pause before writing to serial
REALTIME_MAX_ROIS = 2    # fewer OCR attempts = faster gate response

# -------------------------------------------------------------------
# Preview window settings
# -------------------------------------------------------------------

WINDOW_NAME      = "ANPR - Live Detection"
PREVIEW_WIDTH    = 900
IDLE_FPS         = 10
RESULT_HOLD_SECS = 5.0  # how long result stays on screen before going back to idle

# BGR colours for OpenCV
GREEN  = (0,  220,  80)
RED    = (0,   60, 220)
ORANGE = (0,  160, 255)
YELLOW = (0,  220, 220)
WHITE  = (255, 255, 255)
BLACK  = (0,   0,   0)
DARK   = (20,  20,  20)
PANEL  = (35,  35,  35)
FONT   = cv2.FONT_HERSHEY_SIMPLEX

# -------------------------------------------------------------------
# Shared state between main loop and detection thread
# Using a simple dict + one lock is safer than multiple globals
# -------------------------------------------------------------------

_state = {
    "frame":      None,   # annotated frame to display (None = show idle/spinner)
    "expiry":     0.0,    # time.time() when result should fade back to idle
    "processing": False,  # True while detection is running
}
_lock = threading.Lock()


# -------------------------------------------------------------------
# Database helper for exit events
# -------------------------------------------------------------------

def record_exit(plate: str, category: str) -> bool:
    # Keep exit slot updates in the shared ANPR database helper.
    return release_parking_slot(category, plate)


# -------------------------------------------------------------------
# Serial helper
# -------------------------------------------------------------------

def _send(ser: serial.Serial, msg: str) -> None:
    # Write one line to Arduino and flush immediately.
    # The \n at the end is what triggers Serial.readStringUntil('\n') on Arduino.
    line = msg.strip() + "\n"
    ser.write(line.encode("utf-8"))
    ser.flush()
    print(f"[-> Arduino] {msg}")


# -------------------------------------------------------------------
# Build the message string Arduino expects
# -------------------------------------------------------------------

def _build_msg(plate: str, status: str, category: str) -> str:
    p = plate    if plate    else "UNKNOWN"
    c = category if category else "User"

    if status == "GRANTED":
        return f"GRANTED|{p}|{c}"
    if status == "EXIT_GRANTED":
        return f"EXIT_GRANTED|{p}|{c}"
    if status == "PARKING_FULL":
        return f"PARKING_FULL|{c}"
    return "DENIED"


def _save_response_time(plate: str, status: str, category: str, response_time: float) -> None:
    os.makedirs(os.path.dirname(RESPONSE_TIME_CSV), exist_ok=True)
    file_exists = os.path.exists(RESPONSE_TIME_CSV)

    with open(RESPONSE_TIME_CSV, "a", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(
            f,
            fieldnames=["Timestamp", "Plate", "Status", "Category", "Response Time (s)", "Scenario Notes"],
        )
        if not file_exists:
            writer.writeheader()
        writer.writerow({
            "Timestamp": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            "Plate": plate or "UNKNOWN",
            "Status": status,
            "Category": category or "",
            "Response Time (s)": f"{response_time:.2f}",
            "Scenario Notes": "",
        })


# -------------------------------------------------------------------
# OpenCV frame builders
# -------------------------------------------------------------------

def _status_colours(status: str):
    if status == "GRANTED":      return GREEN,  (0, 100, 40)
    if status == "EXIT_GRANTED": return YELLOW, (60, 100, 0)
    if status == "DENIED":       return RED,    (30, 20, 120)
    return ORANGE, (0, 80, 120)  # PARKING_FULL


def _draw_rounded_rect(img, x1, y1, x2, y2, colour, r=10):
    cv2.rectangle(img, (x1 + r, y1), (x2 - r, y2), colour, -1)
    cv2.rectangle(img, (x1, y1 + r), (x2, y2 - r), colour, -1)
    for px, py in [(x1+r, y1+r), (x2-r, y1+r), (x1+r, y2-r), (x2-r, y2-r)]:
        cv2.circle(img, (px, py), r, colour, -1)


def _idle_frame() -> np.ndarray:
    h, w  = 540, PREVIEW_WIDTH
    frame = np.full((h, w, 3), DARK, dtype=np.uint8)

    # Header
    cv2.rectangle(frame, (0, 0), (w, 56), PANEL, -1)
    cv2.putText(frame, "ANPR PARKING  |  LIVE MONITOR",
                (20, 38), FONT, 0.75, WHITE, 2, cv2.LINE_AA)
    dot = GREEN if int(time.time() * 2) % 2 == 0 else (0, 80, 30)
    cv2.circle(frame, (w - 30, 28), 10, dot, -1)

    # Centre text
    ts = datetime.now().strftime("%Y-%m-%d   %H:%M:%S")
    cv2.putText(frame, ts,
                (w//2 - 155, h//2 - 30), FONT, 0.8, (150, 150, 150), 1, cv2.LINE_AA)
    cv2.putText(frame, "Waiting for vehicle...",
                (w//2 - 183, h//2 + 18), FONT, 1.0, (200, 200, 200), 2, cv2.LINE_AA)

    # Footer
    cv2.rectangle(frame, (0, h - 36), (w, h), PANEL, -1)
    cv2.putText(frame, f"Camera: {ESP32_IP}   |   Serial: {SERIAL_PORT}",
                (16, h - 12), FONT, 0.52, (120, 120, 120), 1, cv2.LINE_AA)
    return frame


def _spinner_frame() -> np.ndarray:
    h, w  = 540, PREVIEW_WIDTH
    frame = np.full((h, w, 3), DARK, dtype=np.uint8)

    cv2.rectangle(frame, (0, 0), (w, 56), PANEL, -1)
    cv2.putText(frame, "ANPR PARKING  |  LIVE MONITOR",
                (20, 38), FONT, 0.75, WHITE, 2, cv2.LINE_AA)

    # Spinning dots animation
    cx, cy = w // 2, h // 2 - 20
    tick   = int(time.time() * 6) % 8
    for i in range(8):
        angle  = np.deg2rad(i * 45)
        dx, dy = int(cx + 40 * np.cos(angle)), int(cy + 40 * np.sin(angle))
        alpha  = 255 if i == tick else max(40, 255 - abs(i - tick) * 50)
        cv2.circle(frame, (dx, dy), 8, (alpha, alpha, alpha), -1)

    cv2.putText(frame, "Processing...",
                (w//2 - 125, h//2 + 60), FONT, 1.0, YELLOW, 2, cv2.LINE_AA)
    cv2.putText(frame, "Running YOLO + OCR",
                (w//2 - 138, h//2 + 100), FONT, 0.60, (140, 140, 140), 1, cv2.LINE_AA)
    return frame


def _result_frame(raw_img, model, plate, status, category, conf) -> np.ndarray:
    h0, w0 = raw_img.shape[:2]
    scale  = PREVIEW_WIDTH / w0
    frame  = cv2.resize(raw_img, (PREVIEW_WIDTH, int(h0 * scale)))
    h, w   = frame.shape[:2]

    box_col, badge_col = _status_colours(status)

    # Draw YOLO bounding boxes
    for r in model(raw_img, verbose=False):
        for box in r.boxes:
            c = float(box.conf[0]) if hasattr(box, "conf") else 1.0
            if c < 0.20:
                continue
            x1, y1, x2, y2 = [int(v * scale) for v in map(float, box.xyxy[0])]
            cv2.rectangle(frame, (x1, y1), (x2, y2), box_col, 3)
            tag = f"{c:.0%}"
            (tw, th), _ = cv2.getTextSize(tag, FONT, 0.55, 2)
            cv2.rectangle(frame, (x1, y1 - th - 10), (x1 + tw + 10, y1), box_col, -1)
            cv2.putText(frame, tag, (x1 + 5, y1 - 4), FONT, 0.55, BLACK, 2, cv2.LINE_AA)

    # Header bar
    cv2.rectangle(frame, (0, 0), (w, 58), PANEL, -1)
    cv2.putText(frame, "ANPR PARKING  |  LIVE MONITOR",
                (16, 38), FONT, 0.75, WHITE, 2, cv2.LINE_AA)
    cv2.putText(frame, datetime.now().strftime("%H:%M:%S"),
                (16, 54), FONT, 0.45, (140, 140, 140), 1, cv2.LINE_AA)
    if conf > 0:
        cv2.putText(frame, f"OCR: {conf:.0%}",
                    (w - 160, 38), FONT, 0.65, (180, 180, 180), 2, cv2.LINE_AA)

    # Bottom result badge
    by = max(h - 100, 58)
    _draw_rounded_rect(frame, 0, by, w, h, badge_col, r=0)

    plate_txt = plate if plate and plate != "UNKNOWN" else "UNREADABLE"
    cv2.putText(frame, plate_txt, (24, by + 52), FONT, 1.6, WHITE, 3, cv2.LINE_AA)

    labels = {
        "GRANTED":      "ACCESS GRANTED",
        "EXIT_GRANTED": "EXIT GRANTED",
        "DENIED":       "ACCESS DENIED",
        "PARKING_FULL": "PARKING FULL",
    }
    cv2.putText(frame, labels.get(status, status),
                (24, by + 90), FONT, 0.70, WHITE, 2, cv2.LINE_AA)

    # Category pill on right side
    if category:
        txt = category.upper()
        (cw2, ch2), _ = cv2.getTextSize(txt, FONT, 0.65, 2)
        px = w - cw2 - 36;  py = by + 55
        _draw_rounded_rect(frame, px - 12, py - ch2 - 8, px + cw2 + 12, py + 8, WHITE, r=8)
        cv2.putText(frame, txt, (px, py), FONT, 0.65, badge_col, 2, cv2.LINE_AA)

    return frame


def _choose_fast_ocr_from_rois(rois: list[np.ndarray]) -> tuple[str, float]:
    # Realtime mode: try the most likely crops first and stop once confidence is good.
    best_text, best_conf = "", 0.0
    for roi in rois[:REALTIME_MAX_ROIS]:
        text, score = extract_plate_text(roi)
        if text and score > best_conf:
            best_text, best_conf = text, score
        if best_conf >= 0.65:
            break
    return best_text, best_conf


# -------------------------------------------------------------------
# ESP32-CAM capture
# -------------------------------------------------------------------

def _grab_image(path: str) -> bool:
    for attempt in range(1, CAPTURE_RETRIES + 1):
        try:
            print(f"[CAM] Capturing (attempt {attempt})...")
            resp = requests.get(CAPTURE_URL, timeout=HTTP_TIMEOUT)
            resp.raise_for_status()
            if "image" not in resp.headers.get("Content-Type", "").lower():
                time.sleep(1)
                continue
            with open(path, "wb") as f:
                f.write(resp.content)
            if os.path.exists(path) and os.path.getsize(path) > 0:
                print(f"[CAM] Saved -> {path}")
                return True
        except Exception as exc:
            print(f"[CAM] Error: {exc}")
        time.sleep(1)
    return False


# -------------------------------------------------------------------
# Full detection cycle -- runs in a background thread
# -------------------------------------------------------------------

def _run_detection(ser: serial.Serial, model: YOLO) -> None:
    start_time = time.time()

    # Safe defaults -- these are always sent to Arduino even if something crashes
    plate    = "UNKNOWN"
    status   = "DENIED"
    category = ""
    conf     = 0.0

    try:
        # --- capture image ---
        path = os.path.join(CAPTURE_DIR, datetime.now().strftime("capture_%Y%m%d_%H%M%S.jpg"))

        if not _grab_image(path):
            print("[ERROR] Capture failed.")
            return  # finally block will still send DENIED

        raw = cv2.imread(path)
        if raw is None:
            print("[ERROR] Cannot decode image.")
            return

        # --- YOLO + OCR ---
        rois              = build_roi_candidates(raw, model, conf_thres=0.25)
        ocr_text, conf    = _choose_fast_ocr_from_rois(rois)
        plate             = normalise_plate(ocr_text) if ocr_text else ""

        # --- database lookup ---
        if plate:
            vehicle = find_authorised_vehicle(plate)

            if vehicle and int(vehicle["is_active"]) == 1:
                norm     = vehicle["plate"]
                category = vehicle["category"]
                plate    = norm

                if is_currently_parked(norm):
                    # Vehicle was inside -- this is an exit
                    ok      = record_exit(norm, category)
                    status  = "EXIT_GRANTED" if ok else "DENIED"
                else:
                    # Vehicle was outside -- this is an entry
                    ok = occupy_parking_slot(category, norm)
                    if ok:
                        status = "GRANTED"
                    else:
                        state  = get_parking_state(category)
                        status = "PARKING_FULL" if (state and int(state["available"] or 0) <= 0) else "DENIED"

            elif vehicle:
                status   = "DENIED"
                category = vehicle["category"]
                plate    = vehicle["plate"]
            else:
                status = "DENIED"
        else:
            status = "DENIED"

        response_time = time.time() - start_time

        # --- write to access log ---
        log_status = "GRANTED" if status == "EXIT_GRANTED" else status
        log_detail = (
            f"Exit via gate sensor | Response time: {response_time:.2f}s"
            if status == "EXIT_GRANTED"
            else f"{status} - {category or 'unknown'} | Response time: {response_time:.2f}s"
        )
        log_access(plate or "UNKNOWN", log_status, log_detail)
        _save_response_time(plate or "UNKNOWN", status, category, response_time)

        print(
            f"[RESULT] Plate={plate} | Status={status} | Category={category} | "
            f"Conf={conf:.2f} | Response Time={response_time:.2f}s"
        )

        # --- update the preview window ---
        annotated = _result_frame(raw, model, plate or "UNKNOWN", status, category, conf)
        with _lock:
            _state["frame"]  = annotated
            _state["expiry"] = time.time() + RESULT_HOLD_SECS

    except Exception as exc:
        print(f"[ERROR] Detection pipeline crashed: {exc}")
        import traceback
        traceback.print_exc()

    finally:
        # This ALWAYS runs -- Arduino always gets a response
        time.sleep(SEND_DELAY)
        msg = _build_msg(plate, status, category)
        _send(ser, msg)

        # Release processing lock so next vehicle can trigger
        with _lock:
            _state["processing"] = False


# -------------------------------------------------------------------
# Main loop
# -------------------------------------------------------------------

def _fetch_slot_counts():
    # Query the database for current available slots.
    # Returns (staff, students, visitors) as integers.
    try:
        conn = get_conn()
        cur  = conn.cursor(dictionary=True)
        cur.execute(
            "SELECT category, available FROM parking_categories "
            "WHERE category IN ('Staff','Students','Visitors')"
        )
        rows = {r["category"]: int(r["available"]) for r in cur.fetchall()}
        cur.close(); conn.close()
        return (
            rows.get("Staff",    0),
            rows.get("Students", 0),
            rows.get("Visitors", 0),
        )
    except Exception as exc:
        print(f"[DB] Slot fetch failed: {exc}")
        return (0, 0, 0)


def main() -> None:
    os.makedirs(CAPTURE_DIR, exist_ok=True)

    # Load YOLO once -- stays in memory the whole session
    # This is what fixes the slow detection (no more reloading every time)
    print("[INFO] Loading YOLO model...")
    model = get_model(MODEL_PATH)
    print("[INFO] Model loaded.\n")

    # Open serial port
    try:
        print(f"[INFO] Opening serial port {SERIAL_PORT}...")
        ser = serial.Serial(SERIAL_PORT, BAUD_RATE, timeout=SERIAL_TIMEOUT)
        print(f"[INFO] Waiting {STARTUP_DELAY}s for Arduino to finish resetting...")
        time.sleep(STARTUP_DELAY)
        print("[INFO] Serial ready.\n")
    except Exception as exc:
        print(f"[FATAL] Cannot open serial port: {exc}")
        return

    # Create preview window
    cv2.namedWindow(WINDOW_NAME, cv2.WINDOW_NORMAL)
    cv2.resizeWindow(WINDOW_NAME, PREVIEW_WIDTH, 540)
    print("[INFO] System running. Press Q in the preview window to stop.\n")

    last_slot_send = 0   # track when we last pushed slot counts to Arduino

    try:
        while True:

            # Reset display to idle once result hold time expires
            with _lock:
                if _state["expiry"] > 0 and time.time() > _state["expiry"]:
                    _state["frame"]  = None
                    _state["expiry"] = 0.0

            # Send slot counts to Arduino every 5 seconds so LCD stays updated.
            # Only send when idle so we don't interrupt a detection in progress.
            with _lock:
                currently_processing = _state["processing"]

            if not currently_processing and (time.time() - last_slot_send) > 5.0:
                staff, students, visitors = _fetch_slot_counts()
                slot_msg = f"SLOTS|{staff}|{students}|{visitors}"
                _send(ser, slot_msg)
                last_slot_send = time.time()

            # Read serial -- only if not currently processing
            with _lock:
                currently_processing = _state["processing"]

            if not currently_processing and ser.in_waiting > 0:
                line = ser.readline().decode("utf-8", errors="ignore").strip()

                if line:
                    print(f"[Arduino] {line}")

                    # Ignore the distance readings Arduino sends every 200ms
                    if line.startswith("Distance:"):
                        pass

                    elif line == "CAR_DETECTED":
                        with _lock:
                            _state["processing"] = True
                            _state["frame"]      = None  # show spinner while processing

                        # Run detection in background so the preview window stays responsive
                        t = threading.Thread(
                            target=_run_detection,
                            args=(ser, model),
                            daemon=True,
                        )
                        t.start()

            # Pick the right frame to show
            with _lock:
                frame      = _state["frame"]
                processing = _state["processing"]

            if processing and frame is None:
                display = _spinner_frame()
            elif frame is not None:
                display = frame.copy()
            else:
                display = _idle_frame()

            cv2.imshow(WINDOW_NAME, display)

            # Q or window close = quit
            key = cv2.waitKey(1000 // IDLE_FPS) & 0xFF
            if key in (ord("q"), 27):
                break
            if cv2.getWindowProperty(WINDOW_NAME, cv2.WND_PROP_VISIBLE) < 1:
                break

            time.sleep(LOOP_DELAY)

    except KeyboardInterrupt:
        print("\n[INFO] Stopped by user.")
    finally:
        cv2.destroyAllWindows()
        try:
            if ser.is_open:
                ser.close()
                print("[INFO] Serial port closed.")
        except Exception:
            pass
        print("[INFO] Done.")


if __name__ == "__main__":
    main()
