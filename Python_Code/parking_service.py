"""Shared parking mutations, committed only when every step succeeds."""

import logging

from db import close_db, get_conn

logger = logging.getLogger(__name__)


def occupy_parking_slot(category: str, plate: str) -> bool:
    """Reserve once; a full category or duplicate plate leaves counts unchanged."""
    conn = cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        # The conditional UPDATE locks and tests the current row in one statement.
        cur.execute(
            "UPDATE parking_categories SET available = available - 1 "
            "WHERE category = %s AND available > 0",
            (category,),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return False
        # A plain INSERT relies on the unique plate constraint. An upsert could
        # decrement capacity twice for the same vehicle.
        cur.execute("INSERT INTO parking (plate, category) VALUES (%s, %s)", (plate, category))
        conn.commit()
        return True
    except Exception:
        if conn is not None:
            conn.rollback()
        logger.exception("Parking reservation failed")
        return False
    finally:
        close_db(conn, cur)


def release_parking_slot(category: str, plate: str, details: str | None = None) -> bool:
    """Release exactly one matching parked record, with an optional atomic log."""
    conn = cur = None
    try:
        conn = get_conn()
        cur = conn.cursor()
        cur.execute("DELETE FROM parking WHERE plate = %s AND category = %s", (plate, category))
        if cur.rowcount != 1:
            conn.rollback()
            return False
        cur.execute(
            "UPDATE parking_categories SET available = available + 1 "
            "WHERE category = %s AND available < capacity",
            (category,),
        )
        if cur.rowcount != 1:
            conn.rollback()
            return False
        if details is not None:
            cur.execute(
                "INSERT INTO access_logs (plate, category, status, details) VALUES (%s, %s, %s, %s)",
                (plate, category, "GRANTED", details),
            )
        conn.commit()
        return True
    except Exception:
        if conn is not None:
            conn.rollback()
        logger.exception("Parking release failed")
        return False
    finally:
        close_db(conn, cur)
