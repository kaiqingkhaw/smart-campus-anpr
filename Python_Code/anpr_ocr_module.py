# anpr_ocr_module.py
# Licence plate OCR helper (Malaysia-focused).
#
# Design goals:
# - Stable OCR under mild tilt, blur and uneven lighting
# - Reject noisy strings (reduce false matches)
# - Correct common OCR confusions only when it improves validity
#
# Usage:
#   text, conf = extract_plate_text(plate_roi)

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import List, Optional, Tuple

import cv2
import numpy as np
import easyocr

from plate_utils import normalise_plate

# Build the heavyweight reader only when OCR is first requested. This keeps
# dashboard startup and unit tests fast while still reusing one reader.
_READER = None


def get_reader():
    global _READER
    if _READER is None:
        _READER = easyocr.Reader(["en"], gpu=False)
    return _READER

# Plates are alphanumeric only
_ALLOWLIST = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789"

# Practical Malaysian plate format:
#   1–3 letters + 1–4 digits + optional 0–2 trailing letters
# Examples: WXY1234, JQK7788, VAA5566, ABC123, W1A, B1234CD
_PLATE_REGEX = re.compile(r"^[A-Z]{1,3}\d{1,4}[A-Z]{0,2}$")

# Typical OCR confusions
_TO_DIGIT = {"O": "0", "I": "1", "L": "1", "Z": "2", "S": "5", "B": "8", "G": "6"}
_TO_LETTER = {"0": "O", "1": "I", "2": "Z", "5": "S", "8": "B", "6": "G"}


def is_plate_like(text: str, min_len: int = 5) -> bool:
    """Light validation used for ranking OCR candidates."""
    t = normalise_plate(text)
    return len(t) >= min_len and bool(_PLATE_REGEX.match(t))


def _split_alpha_digit_tail(plate: str) -> Tuple[str, str, str]:
    """
    Split into (leading letters, digit block, trailing letters).
    This matches typical Malaysian plates where digits are grouped in the middle.
    """
    p = normalise_plate(plate)
    m = re.match(r"^([A-Z]{0,3})(\d{0,4})([A-Z]{0,2})$", p)
    if not m:
        return "", "", ""
    return m.group(1), m.group(2), m.group(3)


def fix_common_confusions(plate: str) -> str:
    """
    Safe confusion correction:
    - Build a corrected candidate using expected letter/digit positions
    - Only apply the correction if it produces a valid Malaysian plate pattern
    - Otherwise return the original normalised OCR string
    """
    p = normalise_plate(plate)
    if not p:
        return ""

    # Already valid -> no change
    if _PLATE_REGEX.match(p):
        return p

    a, d, t = _split_alpha_digit_tail(p)
    if not (a or d or t):
        return p

    a_fixed = "".join(_TO_LETTER.get(ch, ch) for ch in a)
    t_fixed = "".join(_TO_LETTER.get(ch, ch) for ch in t)
    d_fixed = "".join(_TO_DIGIT.get(ch, ch) for ch in d)

    candidate = normalise_plate(f"{a_fixed}{d_fixed}{t_fixed}")

    # Only accept correction if it becomes valid
    if _PLATE_REGEX.match(candidate):
        return candidate

    return p


def generate_plate_variants(plate: str) -> List[str]:
    """
    Generate a small set of plausible variants for database lookup.
    Only returns variants that match the Malaysian plate pattern to avoid false matches.
    """
    p = normalise_plate(plate)
    if not p:
        return []

    variants = {p, fix_common_confusions(p)}

    # Small, controlled swaps (avoid combinational explosion)
    for a, b in [("O", "0"), ("I", "1"), ("Z", "2"), ("S", "5"), ("B", "8"), ("G", "6")]:
        variants.add(p.replace(a, b))
        variants.add(p.replace(b, a))

    out = []
    for v in variants:
        v = normalise_plate(v)
        if _PLATE_REGEX.match(v):
            out.append(v)
    return sorted(set(out))


# -----------------------------
# Pre-processing
# -----------------------------
@dataclass
class _Candidate:
    text: str
    score: float
    raw_conf: float
    variant: str


def _to_gray(img: np.ndarray) -> Optional[np.ndarray]:
    if img is None:
        return None
    if len(img.shape) == 2:
        return img
    return cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)


def _resize(gray: np.ndarray, scale: float = 2.5) -> np.ndarray:
    return cv2.resize(gray, None, fx=scale, fy=scale, interpolation=cv2.INTER_CUBIC)


def _clahe(gray: np.ndarray) -> np.ndarray:
    clahe = cv2.createCLAHE(clipLimit=2.5, tileGridSize=(8, 8))
    return clahe.apply(gray)


def _sharpen(gray: np.ndarray) -> np.ndarray:
    blur = cv2.GaussianBlur(gray, (0, 0), 1.0)
    return cv2.addWeighted(gray, 1.5, blur, -0.5, 0)


def _morph_close(bin_img: np.ndarray) -> np.ndarray:
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (2, 2))
    return cv2.morphologyEx(bin_img, cv2.MORPH_CLOSE, kernel, iterations=1)


def _auto_deskew(gray: np.ndarray) -> np.ndarray:
    """
    Deskew using edge orientation (mild correction only).
    Helps when plate is slightly tilted.
    """
    g = gray.copy()
    edges = cv2.Canny(cv2.GaussianBlur(g, (3, 3), 0), 50, 150)

    coords = np.column_stack(np.where(edges > 0))
    if coords.shape[0] < 50:
        return gray

    rect = cv2.minAreaRect(coords.astype(np.float32))
    angle = rect[-1]
    if angle < -45:
        angle = 90 + angle

    # Keep it conservative
    if abs(angle) < 1.0 or abs(angle) > 25.0:
        return gray

    h, w = g.shape[:2]
    M = cv2.getRotationMatrix2D((w / 2, h / 2), angle, 1.0)
    return cv2.warpAffine(g, M, (w, h), flags=cv2.INTER_CUBIC, borderMode=cv2.BORDER_REPLICATE)


def _prep_variants(plate_roi: np.ndarray) -> List[Tuple[str, np.ndarray]]:
    """
    Produce multiple processed images. Different thresholding works better
    under different lighting / background conditions.
    """
    gray = _to_gray(plate_roi)
    if gray is None:
        return []

    gray = _resize(gray, 2.5)
    gray = _auto_deskew(gray)

    # Denoise but keep edges
    den = cv2.bilateralFilter(gray, 9, 75, 75)
    base = _sharpen(_clahe(den))

    variants: List[Tuple[str, np.ndarray]] = []

    # Adaptive threshold for uneven lighting
    ada = cv2.adaptiveThreshold(
        base, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C,
        cv2.THRESH_BINARY, 31, 8
    )
    variants.append(("adaptive", _morph_close(ada)))

    # Otsu for clean plates
    _, otsu = cv2.threshold(base, 0, 255, cv2.THRESH_BINARY + cv2.THRESH_OTSU)
    variants.append(("otsu", _morph_close(otsu)))

    # Inverted Otsu sometimes reads better depending on contrast
    _, otsu_inv = cv2.threshold(base, 0, 255, cv2.THRESH_BINARY_INV + cv2.THRESH_OTSU)
    variants.append(("otsu_inv", _morph_close(otsu_inv)))

    # Enhanced grayscale (let EasyOCR decide)
    variants.append(("enhanced_gray", base))

    return variants


def _run_easyocr(img: np.ndarray) -> List[Tuple[str, float]]:
    results = get_reader().readtext(img, allowlist=_ALLOWLIST, paragraph=False)
    return [(text, float(prob)) for (_bbox, text, prob) in results]


def extract_plate_text(plate_roi, min_len: int = 5) -> Tuple[str, float]:
    """
    OCR a plate ROI and return (best_plate, confidence).

    Selection:
    - Prefer candidates that match Malaysian plate pattern
    - Otherwise fall back to best confidence with minimum length
    """
    if isinstance(plate_roi, str):
        plate_roi = cv2.imread(plate_roi)

    if plate_roi is None:
        return "", 0.0

    best_text = ""
    best_raw_conf = 0.0
    best_score = -1.0

    for vname, processed in _prep_variants(plate_roi):
        for raw_text, raw_conf in _run_easyocr(processed):
            cleaned = normalise_plate(raw_text)
            if len(cleaned) < min_len:
                continue

            fixed = fix_common_confusions(cleaned)

            # Small boost if it matches Malaysian plate pattern
            valid = is_plate_like(fixed, min_len=min_len)
            score = raw_conf + (0.12 if valid else 0.0)

            if score > best_score:
                best_score = score
                best_text = fixed
                best_raw_conf = raw_conf

    return best_text, float(best_raw_conf)
