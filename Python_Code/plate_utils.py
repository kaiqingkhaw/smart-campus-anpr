"""Lightweight number-plate helpers shared by the web and OCR layers."""

import re
import math


def normalise_plate(text: str) -> str:
    """Uppercase and remove all non-alphanumeric characters."""
    if not text:
        return ""
    return re.sub(r"[^A-Z0-9]", "", text.upper())


def is_usable_recognition(text: str, confidence: float, threshold: float = 0.30) -> bool:
    """Require plate text and a finite OCR score within the accepted range."""
    return bool(normalise_plate(text)) and math.isfinite(confidence) and threshold <= confidence <= 1.0
