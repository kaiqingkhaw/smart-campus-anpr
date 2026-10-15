# anpr_full_system.py
# Full ANPR pipeline: YOLO detects the vehicle, EasyOCR reads the plate,
# then we check the database and update parking records.

from __future__ import annotations

import argparse
import os
from typing import Optional, Tuple, List

import cv2
import numpy as np
from ultralytics import YOLO

from db import get_conn
from parking_service import occupy_parking_slot, release_parking_slot
from anpr_ocr_module import extract_plate_text, normalise_plate, generate_plate_variants
from plate_utils import is_usable_recognition

CODE_DIR = os.path.abspath(os.path.dirname(__file__))
PROJECT_ROOT = os.path.abspath(os.path.join(CODE_DIR, ".."))

MODEL_PATH = os.path.join(CODE_DIR, "best.pt")
DEFAULT_DATASET_DIR = os.path.join(PROJECT_ROOT, "OCR_Test_Images")

YOLO_CONF_THRES = 0.25   # ignore YOLO detections below this confidence
OCR_MIN_CONF    = 0.30   # ignore OCR results below this confidence
DEBUG_ROI_DIR   = "debug_rois"

# -------------------------------------------------------------------
# Model cache — load weights once, reuse forever.
# Reuse loaded detector weights across image requests.
# -------------------------------------------------------------------
_MODEL_CACHE: dict[str, YOLO] = {}


def get_model(model_path: str = MODEL_PATH) -> YOLO:
    if model_path not in _MODEL_CACHE:
        print(f"[YOLO] Loading {model_path}...")
        _MODEL_CACHE[model_path] = YOLO(model_path)
        print("[YOLO] Model ready.")
    return _MODEL_CACHE[model_path]


# -------------------------------------------------------------------
# Database helpers
# -------------------------------------------------------------------

def _db_close(resource) -> None:
    try:
        if resource is not None:
            resource.close()
    except Exception:
        pass


def db_fetch_one(sql: str, params: tuple = ()) -> Optional[dict]:
    conn = get_conn()
    cur  = None
    try:
        cur = conn.cursor(dictionary=True)
        cur.execute(sql, params)
        return cur.fetchone()
    finally:
        _db_close(cur)
        _db_close(conn)


def db_execute_transaction(statements: List[tuple[str, tuple]]) -> bool:
    # Run multiple SQL statements as one atomic block.
    # If any one fails, all are rolled back.
    conn = get_conn()
    cur  = None
    try:
        cur = conn.cursor()
        for sql, params in statements:
            cur.execute(sql, params)
        conn.commit()
        return True
    except Exception as e:
        try:
            conn.rollback()
        except Exception:
            pass
        print(f"[DB] Transaction failed: {e}")
        return False
    finally:
        _db_close(cur)
        _db_close(conn)


def db_execute(sql: str, params: tuple = ()) -> None:
    conn = get_conn()
    cur  = None
    try:
        cur = conn.cursor()
        cur.execute(sql, params)
        conn.commit()
    finally:
        _db_close(cur)
        _db_close(conn)


def log_access(plate: str, status: str, details: str) -> None:
    db_execute(
        "INSERT INTO access_logs (plate, status, details) VALUES (%s, %s, %s)",
        (plate, status, details),
    )


def find_authorised_vehicle(plate_text: str) -> Optional[dict]:
    # Try the raw OCR text first, then try common OCR-confusion variants
    # e.g. O vs 0, I vs 1, S vs 5 etc.
    plate_text = normalise_plate(plate_text)
    if not plate_text:
        return None
    candidates = [plate_text] + [v for v in generate_plate_variants(plate_text) if v != plate_text]
    for candidate in candidates:
        row = db_fetch_one(
            "SELECT id, plate, category, is_active, owner_name "
            "FROM authorised_vehicles WHERE plate = %s LIMIT 1",
            (candidate,),
        )
        if row:
            return row
    return None


def get_parking_state(category: str) -> Optional[dict]:
    return db_fetch_one(
        "SELECT category, capacity, available "
        "FROM parking_categories WHERE category = %s LIMIT 1",
        (category,),
    )


def is_currently_parked(plate: str) -> bool:
    # Used to tell entry from exit — if the plate is in the parking
    # table with no exit time, the vehicle is still inside.
    row = db_fetch_one(
        "SELECT plate FROM parking WHERE plate = %s LIMIT 1",
        (plate,),
    )
    return row is not None


# -------------------------------------------------------------------
# Image loading
# -------------------------------------------------------------------

def load_images_from_folder(folder_path: str) -> List[tuple[str, np.ndarray]]:
    items: List[tuple[str, np.ndarray]] = []
    if not os.path.isdir(folder_path):
        return items
    for name in sorted(os.listdir(folder_path)):
        if name.lower().endswith((".jpg", ".jpeg", ".png", ".bmp", ".webp")):
            path = os.path.join(folder_path, name)
            img  = cv2.imread(path)
            if img is not None:
                items.append((name, img))
    return items


# -------------------------------------------------------------------
# Bounding box helpers
# -------------------------------------------------------------------

def _clamp_box(x1, y1, x2, y2, w, h):
    # Keep coordinates inside the image boundaries.
    return (
        max(0, min(x1, w - 1)), max(0, min(y1, h - 1)),
        max(0, min(x2, w)),     max(0, min(y2, h)),
    )


def _expand_box(x1, y1, x2, y2, w, h, margin=0.06):
    # Grow the YOLO box slightly so we don't cut off plate edges.
    bw, bh = x2 - x1, y2 - y1
    mx, my = int(bw * margin), int(bh * margin)
    return _clamp_box(x1 - mx, y1 - my, x2 + mx, y2 + my, w=w, h=h)


def _crop(image, box_xyxy):
    h, w = image.shape[:2]
    x1, y1, x2, y2 = _clamp_box(*box_xyxy, w=w, h=h)
    roi = image[y1:y2, x1:x2]
    return roi if roi.size else image


# -------------------------------------------------------------------
# Plate region extraction
# -------------------------------------------------------------------

def crop_plate_band(vehicle_roi: np.ndarray) -> np.ndarray:
    # Quick heuristic: the plate lives in the lower-centre of the vehicle.
    ch, cw = vehicle_roi.shape[:2]
    roi = vehicle_roi[int(ch * 0.58):int(ch * 0.93),
                      int(cw * 0.18):int(cw * 0.82)]
    return roi if roi.size else vehicle_roi


def crop_plate_candidate_by_contours(bgr_roi: np.ndarray) -> Optional[np.ndarray]:
    # Try to find a rectangle with plate-like aspect ratio using edge detection.
    if bgr_roi is None or bgr_roi.size == 0:
        return None

    gray = cv2.cvtColor(bgr_roi, cv2.COLOR_BGR2GRAY) if len(bgr_roi.shape) == 3 else bgr_roi.copy()
    gray  = cv2.bilateralFilter(gray, 7, 60, 60)
    edges = cv2.Canny(gray, 60, 160)
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 3))
    edges  = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel, iterations=1)

    contours, _ = cv2.findContours(edges, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_SIMPLE)
    if not contours:
        return None

    h, w = gray.shape[:2]
    best, best_score = None, 0.0

    for c in contours:
        cx, cy, cw, ch = cv2.boundingRect(c)
        area = cw * ch
        if area < 800 or cw < 40 or ch < 12:
            continue
        aspect = cw / max(ch, 1)
        if not (2.0 <= aspect <= 8.0):   # plates are wider than they are tall
            continue
        rel_area = area / (w * h)
        if not (0.02 <= rel_area <= 0.60):
            continue
        score = rel_area * min(aspect / 3.0, 1.0)
        if score > best_score:
            best_score = score
            pad  = 4
            best = bgr_roi[max(0, cy - pad):min(h, cy + ch + pad),
                           max(0, cx - pad):min(w, cx + cw + pad)]

    return best if (best is not None and best.size) else None


# -------------------------------------------------------------------
# YOLO detection
# -------------------------------------------------------------------

def detect_best_vehicle_box(
    model: YOLO,
    image: np.ndarray,
    conf_thres: float,
) -> Optional[Tuple[int, int, int, int]]:
    # Run YOLO and return the largest high-confidence detection box.
    results   = model(image, verbose=False)
    best_box  = None
    best_area = 0

    for r in results:
        for box in r.boxes:
            conf = float(box.conf[0]) if hasattr(box, "conf") else 1.0
            if conf < conf_thres:
                continue
            x1, y1, x2, y2 = map(int, box.xyxy[0])
            area = max(0, x2 - x1) * max(0, y2 - y1)
            if area > best_area:
                best_area = area
                best_box  = (x1, y1, x2, y2)

    return best_box


# -------------------------------------------------------------------
# OCR over multiple ROI candidates
# -------------------------------------------------------------------

def choose_best_ocr_from_rois(rois: List[np.ndarray]) -> Tuple[str, float, int]:
    # Run OCR on every candidate crop and keep the most confident result.
    best_text, best_conf, best_idx = "", 0.0, -1
    for idx, roi in enumerate(rois):
        text, conf = extract_plate_text(roi)
        if text and conf > best_conf:
            best_text = text
            best_conf = conf
            best_idx  = idx
    return best_text, best_conf, best_idx


def build_roi_candidates(
    image: np.ndarray,
    model: YOLO,
    conf_thres: float,
) -> List[np.ndarray]:
    # Build several candidate crops so OCR has the best chance of
    # getting a clean read regardless of angle or framing.
    h, w     = image.shape[:2]
    best_box = detect_best_vehicle_box(model, image, conf_thres=conf_thres)
    rois: List[np.ndarray] = []

    if best_box:
        x1, y1, x2, y2 = _expand_box(*best_box, w=w, h=h, margin=0.06)
        vehicle_roi = _crop(image, (x1, y1, x2, y2))

        band = crop_plate_band(vehicle_roi)
        rois.append(band)                                        # lower band

        c1 = crop_plate_candidate_by_contours(band)
        if c1 is not None:
            rois.append(c1)                                      # contour from band

        c2 = crop_plate_candidate_by_contours(vehicle_roi)
        if c2 is not None:
            rois.append(c2)                                      # contour from full ROI

        rois.append(vehicle_roi)                                 # full vehicle ROI
    else:
        # YOLO found nothing — fall back to a rough centre crop
        full_band = image[int(h * 0.60):int(h * 0.92),
                          int(w * 0.20):int(w * 0.80)]
        rois.append(full_band)
        cp = crop_plate_candidate_by_contours(full_band)
        if cp is not None:
            rois.append(cp)

    return rois


# -------------------------------------------------------------------
# Access decision
# -------------------------------------------------------------------

def decide_access(ocr_text: str, ocr_conf: float) -> Tuple[str, str, str]:
    # Returns (plate, status, details).
    # status is one of: GRANTED, DENIED, PARKING_FULL
    plate = normalise_plate(ocr_text) if ocr_text else ""

    if not is_usable_recognition(plate, ocr_conf, OCR_MIN_CONF):
        return ("UNKNOWN", "DENIED", "OCR failed / unreadable plate")

    vehicle = find_authorised_vehicle(plate)
    if not vehicle:
        return (plate, "DENIED", "Unauthorised vehicle")

    if int(vehicle["is_active"]) != 1:
        return (vehicle["plate"], "DENIED", "Vehicle is inactive")

    category = vehicle["category"]

    if is_currently_parked(vehicle["plate"]):
        if release_parking_slot(category, vehicle["plate"]):
            return (vehicle["plate"], "GRANTED", f"{category} exit recorded")
        return (vehicle["plate"], "DENIED", "Exit failed due to database error")

    if occupy_parking_slot(category, vehicle["plate"]):
        return (vehicle["plate"], "GRANTED", f"{category} entry granted")

    return (vehicle["plate"], "PARKING_FULL", f"{category} parking full")


# -------------------------------------------------------------------
# Main process function
# -------------------------------------------------------------------

def process_image(
    model: YOLO,
    name: str,
    image: np.ndarray,
    show: bool = False,
    save_rois: bool = False,
    conf_thres: float = YOLO_CONF_THRES,
) -> Tuple[str, str, str]:
    roi_candidates       = build_roi_candidates(image, model, conf_thres)
    text, conf, best_idx = choose_best_ocr_from_rois(roi_candidates)
    final_plate, status, details = decide_access(text, conf)

    log_access(final_plate, status, details)
    print(f"[{name}] Plate={final_plate} | Conf={conf:.2f} | Status={status} | {details}")

    if save_rois:
        os.makedirs(DEBUG_ROI_DIR, exist_ok=True)
        stem = os.path.splitext(name)[0]
        for i, roi in enumerate(roi_candidates):
            cv2.imwrite(os.path.join(DEBUG_ROI_DIR, f"{stem}_roi_{i}.jpg"), roi)

    if show and roi_candidates:
        disp = roi_candidates[best_idx] if 0 <= best_idx < len(roi_candidates) else roi_candidates[0]
        cv2.imshow("Best Plate ROI", disp)
        cv2.imshow("Frame", image)
        cv2.waitKey(500)

    return final_plate, status, details


def process_single_image_file(
    image_path: str,
    model_path: str = MODEL_PATH,
    conf_thres: float = YOLO_CONF_THRES,
    show: bool = False,
    save_rois: bool = False,
    model: Optional[YOLO] = None,
) -> Tuple[str, str, str]:
    # Pass in a pre-loaded model to avoid reloading on every call.
    if not os.path.exists(image_path):
        return ("UNKNOWN", "DENIED", f"Image not found: {image_path}")
    image = cv2.imread(image_path)
    if image is None:
        return ("UNKNOWN", "DENIED", "Could not load image")
    m = model if model is not None else get_model(model_path)
    return process_image(m, os.path.basename(image_path), image, show, save_rois, conf_thres)


def process_single_image_array(
    image: np.ndarray,
    model_path: str = MODEL_PATH,
    conf_thres: float = YOLO_CONF_THRES,
    image_name: str = "captured_frame.jpg",
    show: bool = False,
    save_rois: bool = False,
    model: Optional[YOLO] = None,
) -> Tuple[str, str, str]:
    if image is None or image.size == 0:
        return ("UNKNOWN", "DENIED", "Empty image frame")
    m = model if model is not None else get_model(model_path)
    return process_image(m, image_name, image, show, save_rois, conf_thres)


# -------------------------------------------------------------------
# CLI batch mode  (python anpr_full_system.py --dataset ./dataset)
# -------------------------------------------------------------------

def main() -> None:
    parser = argparse.ArgumentParser(description="ANPR batch pipeline")
    parser.add_argument("--dataset",   default=DEFAULT_DATASET_DIR)
    parser.add_argument("--show",      action="store_true")
    parser.add_argument("--save-rois", action="store_true")
    parser.add_argument("--model",     default=MODEL_PATH)
    parser.add_argument("--conf",      type=float, default=YOLO_CONF_THRES)
    args = parser.parse_args()

    if not os.path.exists(args.dataset):
        raise FileNotFoundError(f"Dataset folder not found: {args.dataset}")

    images = load_images_from_folder(args.dataset)
    if not images:
        print("No images found.")
        return

    model = get_model(args.model)   # load once, reuse for all images

    for name, img in images:
        process_image(model, name, img, args.show, args.save_rois, args.conf)

    if args.show:
        cv2.destroyAllWindows()

    print("\nDone.")


if __name__ == "__main__":
    main()