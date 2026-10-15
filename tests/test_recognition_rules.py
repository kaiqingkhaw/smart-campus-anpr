"""Decision tests without loading camera, detector or OCR dependencies."""
import ast
from pathlib import Path
import sys

import pytest

CODE = Path(__file__).resolve().parents[1] / 'Python_Code'
sys.path.insert(0, str(CODE))
from plate_utils import is_usable_recognition, normalise_plate


@pytest.mark.parametrize('text,score,expected', [
    ('ABC123', 0.29, False), ('ABC123', 0.30, True),
    ('ABC123', 1.0, True), ('ABC123', 1.1, False),
    ('', 0.9, False), ('---', 0.9, False),
    ('ABC123', float('nan'), False), ('ABC123', float('inf'), False),
])
def test_acceptance_threshold(text, score, expected):
    assert is_usable_recognition(text, score) is expected


def load_function(name, dependencies, filename='anpr_full_system.py'):
    """Execute the actual decision function with controlled external dependencies."""
    tree = ast.parse((CODE / filename).read_text(encoding='utf-8-sig'))
    node = next(n for n in tree.body if isinstance(n, ast.FunctionDef) and n.name == name)
    future = ast.ImportFrom(module='__future__', names=[ast.alias(name='annotations')], level=0)
    module = ast.fix_missing_locations(ast.Module(body=[future, node], type_ignores=[]))
    namespace = dict(dependencies)
    exec(compile(module, str(CODE / 'anpr_full_system.py'), 'exec'), namespace)
    return namespace[name]


def test_exact_plate_takes_priority_over_ocr_alternatives():
    calls = []
    def fetch(sql, params):
        calls.append(params[0])
        return {'plate': params[0], 'is_active': 1}
    lookup = load_function('find_authorised_vehicle', {
        'normalise_plate': normalise_plate,
        'generate_plate_variants': lambda text: ['AB0123', 'ABO123'],
        'db_fetch_one': fetch,
    })
    assert lookup('ABO123')['plate'] == 'ABO123'
    assert calls == ['ABO123']


def test_low_confidence_does_not_lookup_or_reserve():
    def unexpected(*args):
        raise AssertionError('Low-confidence recognition must not query the registry')
    decide = load_function('decide_access', {
        'normalise_plate': normalise_plate,
        'is_usable_recognition': is_usable_recognition,
        'OCR_MIN_CONF': 0.30,
        'find_authorised_vehicle': unexpected,
    })
    assert decide('ABC123', 0.29)[1] == 'DENIED'


def test_live_bridge_rejects_low_confidence_before_lookup():
    import os
    from datetime import datetime
    from threading import Lock
    from types import SimpleNamespace
    sent = []
    lookups = []
    detection = load_function('_run_detection', {
        'os': os, 'datetime': datetime, 'CAPTURE_DIR': '.',
        'time': SimpleNamespace(time=lambda: 0, sleep=lambda _: None),
        'cv2': SimpleNamespace(imread=lambda _: object()),
        '_grab_image': lambda _: True,
        'build_roi_candidates': lambda *a, **kw: [],
        '_choose_fast_ocr_from_rois': lambda _: ('ABC123', 0.29),
        'normalise_plate': normalise_plate,
        'is_usable_recognition': is_usable_recognition, 'OCR_MIN_CONF': 0.30,
        'find_authorised_vehicle': lambda plate: lookups.append(plate),
        'log_access': lambda *a: None, '_save_response_time': lambda *a: None,
        '_result_frame': lambda *a: None, '_lock': Lock(), '_state': {},
        'RESULT_HOLD_SECS': 1, 'SEND_DELAY': 0,
        '_build_msg': lambda plate, status, category: status,
        '_send': lambda ser, message: sent.append(message),
    }, 'realtime_anpr_bridge.py')
    detection(None, None)
    assert lookups == []
    assert sent == ['DENIED']
