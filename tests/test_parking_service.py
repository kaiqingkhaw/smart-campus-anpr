"""Transaction behavior against a small SQLite adapter; MySQL testing is separate."""

import sqlite3
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'Python_Code'))
import parking_service as service


class Cursor:
    def __init__(self, cursor):
        self.cursor = cursor

    def execute(self, sql, params):
        self.cursor.execute(sql.replace('%s', '?'), params)

    @property
    def rowcount(self):
        return self.cursor.rowcount

    def close(self):
        self.cursor.close()


class Connection:
    def __init__(self, db):
        self.db = db

    def cursor(self):
        return Cursor(self.db.cursor())

    def commit(self):
        self.db.commit()

    def rollback(self):
        self.db.rollback()

    def is_connected(self):
        return True

    def close(self):
        pass  # Keep the in-memory test database available for assertions.


@pytest.fixture
def db(monkeypatch):
    db = sqlite3.connect(':memory:')
    db.executescript('''
        CREATE TABLE parking_categories (category TEXT PRIMARY KEY, capacity INTEGER, available INTEGER);
        CREATE TABLE parking (plate TEXT PRIMARY KEY, category TEXT);
        CREATE TABLE access_logs (plate TEXT, category TEXT, status TEXT, details TEXT);
        INSERT INTO parking_categories VALUES ('Staff', 1, 1), ('Students', 2, 2);
    ''')
    monkeypatch.setattr(service, 'get_conn', lambda: Connection(db))
    yield db
    db.close()


def available(db, category='Staff'):
    return db.execute('SELECT available FROM parking_categories WHERE category=?', (category,)).fetchone()[0]


def test_last_slot_cannot_be_reserved_twice(db):
    assert service.occupy_parking_slot('Staff', 'ABC123')
    assert not service.occupy_parking_slot('Staff', 'XYZ789')
    assert available(db) == 0
    assert db.execute('SELECT COUNT(*) FROM parking').fetchone()[0] == 1


def test_duplicate_plate_rolls_back_capacity_decrement(db):
    assert service.occupy_parking_slot('Students', 'ABC123')
    assert not service.occupy_parking_slot('Students', 'ABC123')
    assert available(db, 'Students') == 1


def test_repeat_exit_cannot_increment_twice(db):
    assert service.occupy_parking_slot('Students', 'ABC123')
    assert service.occupy_parking_slot('Students', 'XYZ789')
    assert service.release_parking_slot('Students', 'ABC123', 'Exit')
    assert not service.release_parking_slot('Students', 'ABC123', 'Exit')
    assert available(db, 'Students') == 1
    assert db.execute('SELECT COUNT(*) FROM access_logs').fetchone()[0] == 1


def test_wrong_category_does_not_remove_vehicle(db):
    assert service.occupy_parking_slot('Staff', 'ABC123')
    assert not service.release_parking_slot('Students', 'ABC123')
    assert available(db) == 0
    assert db.execute('SELECT COUNT(*) FROM parking').fetchone()[0] == 1


def test_failed_exit_log_rolls_back_entire_exit(db):
    assert service.occupy_parking_slot('Staff', 'ABC123')
    db.execute('DROP TABLE access_logs')
    db.commit()
    assert not service.release_parking_slot('Staff', 'ABC123', 'Exit')
    assert available(db) == 0
    assert db.execute('SELECT COUNT(*) FROM parking').fetchone()[0] == 1


def test_unknown_category_does_not_create_parking(db):
    assert not service.occupy_parking_slot('Unknown', 'ABC123')
    assert db.execute('SELECT COUNT(*) FROM parking').fetchone()[0] == 0
