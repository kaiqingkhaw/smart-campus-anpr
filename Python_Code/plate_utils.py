"""Lightweight number-plate helpers shared by the web and OCR layers."""

import re


def normalise_plate(text: str) -> str:
    """Uppercase and remove all non-alphanumeric characters."""
    if not text:
        return ""
    return re.sub(r"[^A-Z0-9]", "", text.upper())
