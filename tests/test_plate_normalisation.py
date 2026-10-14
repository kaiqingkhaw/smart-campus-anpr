"""Fast unit tests for the core Malaysian plate text normalisation."""

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "Python_Code"))

from plate_utils import normalise_plate


def test_normalise_plate_removes_noise_and_uppercases():
    assert normalise_plate(" wxy-1234 ") == "WXY1234"


def test_normalise_plate_handles_empty_input():
    assert normalise_plate("") == ""
