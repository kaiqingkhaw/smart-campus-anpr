# init_db.py
# Creates the database, all required tables, and initial demo data.
# Command: python init_db.py

import os
import mysql.connector
from mysql.connector import Error
from werkzeug.security import generate_password_hash

from config import (
    DB_HOST,
    DB_PORT,
    DB_USER,
    DB_PASSWORD,
    DB_NAME,
)
from db import get_conn


PARKING_CATEGORIES = [
    ("Staff", 2, 2),
    ("Students", 2, 2),
    ("Visitors", 2, 2),
]


DEMO_VEHICLES = [
    ("WXY1234", "Staff Example 1", "Staff", 1),
    ("WXY1235", "Staff Example 2", "Staff", 1),
    ("WXY1236", "Staff Example 3", "Staff", 1),

    ("JQK7788", "Student Example 1", "Students", 1),
    ("JQK7789", "Student Example 2", "Students", 1),
    ("JQK7790", "Student Example 3", "Students", 1),

    ("VAA5566", "Visitor Example 1", "Visitors", 1),
    ("VAA5567", "Visitor Example 2", "Visitors", 1),
    ("VAA5568", "Visitor Example 3", "Visitors", 1),
]


SAMPLE_LOGS = [
    ("WXY1234", "Staff", "GRANTED", "Staff Entry"),
    ("ABC4321", "Visitors", "DENIED", "Unauthorised Vehicle"),
    ("JQK7788", "Students", "GRANTED", "Student Entry"),
    ("PNB9001", "Visitors", "PARKING_FULL", "No Parking Available"),
    ("VAA5566", "Visitors", "GRANTED", "Visitor Entry"),
]


ADMIN_USERNAME = os.getenv("ADMIN_USERNAME", "admin")
ADMIN_PASSWORD = os.getenv("ADMIN_PASSWORD", "")


def create_database() -> None:
    """
    Creates the configured MySQL database if it does not already exist.

    This connection does not select DB_NAME because the database may not
    exist yet when the project is first installed on a new computer.
    """
    conn = None
    cur = None

    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            port=DB_PORT,
            connection_timeout=5,
            charset="utf8mb4",
            use_unicode=True,
            autocommit=False,
        )

        if not conn.is_connected():
            raise RuntimeError(
                "MySQL connection was created but is not connected."
            )

        cur = conn.cursor()

        cur.execute(
            f"""
            CREATE DATABASE IF NOT EXISTS `{DB_NAME}`
            CHARACTER SET utf8mb4
            COLLATE utf8mb4_unicode_ci
            """
        )

        conn.commit()
        print(f"Database '{DB_NAME}' is ready.")

    except Error as err:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass

        print(f"[init_db] Unable to create database: {err}")

        raise RuntimeError(
            "Database creation failed. Please ensure that MySQL is "
            "running and the settings in config.py are correct."
        ) from err

    finally:
        close_resource(cur)
        close_resource(conn)


def run() -> None:
    """
    Creates the database structure and inserts initial demo records.

    All table creation and initial data operations are handled in one
    transaction so that changes can be rolled back if an error occurs.
    """
    if len(ADMIN_PASSWORD) < 12:
        raise RuntimeError(
            "Set ADMIN_PASSWORD to at least 12 characters before initialising the database."
        )

    conn = None
    cur = None

    try:
        # Create anpr_db first before db.get_conn() attempts to use it.
        create_database()

        conn = get_conn()
        cur = conn.cursor()

        create_tables(cur)
        seed_parking_categories(cur)
        seed_authorised_vehicles(cur)
        sync_parking_availability(cur)
        seed_admin(cur)
        seed_sample_logs(cur)

        conn.commit()

        print("=" * 60)
        print("Database initialised successfully.")
        print(f"Admin username: {ADMIN_USERNAME}")
        print("Admin password: configured securely via environment")
        print("=" * 60)

    except Exception as exc:
        if conn is not None:
            try:
                conn.rollback()
            except Exception:
                pass

        print(
            "[init_db] Database initialisation failed. "
            f"Changes rolled back: {exc}"
        )
        raise

    finally:
        close_resource(cur)
        close_resource(conn)


def create_tables(cur) -> None:
    """
    Creates all database tables required by the system.
    """
    cur.execute("""
        CREATE TABLE IF NOT EXISTS admins (
            id INT AUTO_INCREMENT PRIMARY KEY,
            username VARCHAR(50) NOT NULL UNIQUE,
            password_hash VARCHAR(255) NOT NULL,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS parking_categories (
            id INT AUTO_INCREMENT PRIMARY KEY,
            category VARCHAR(30) NOT NULL UNIQUE,
            capacity INT NOT NULL,
            available INT NOT NULL,
            CHECK (capacity >= 0),
            CHECK (available >= 0),
            CHECK (available <= capacity)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS authorised_vehicles (
            id INT AUTO_INCREMENT PRIMARY KEY,
            plate VARCHAR(20) NOT NULL UNIQUE,
            owner_name VARCHAR(80) NOT NULL,
            category VARCHAR(30) NOT NULL,
            is_active TINYINT(1) NOT NULL DEFAULT 1,
            created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

            CONSTRAINT fk_authorised_category
                FOREIGN KEY (category)
                REFERENCES parking_categories(category)
                ON UPDATE CASCADE
                ON DELETE RESTRICT,

            INDEX idx_authorised_plate (plate),
            INDEX idx_authorised_category (category),
            INDEX idx_authorised_active (is_active)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS parking (
            id INT AUTO_INCREMENT PRIMARY KEY,
            plate VARCHAR(20) NOT NULL UNIQUE,
            category VARCHAR(30) NOT NULL,
            parked_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
            exited_at TIMESTAMP NULL DEFAULT NULL,

            CONSTRAINT fk_parking_category
                FOREIGN KEY (category)
                REFERENCES parking_categories(category)
                ON UPDATE CASCADE
                ON DELETE RESTRICT,

            INDEX idx_parking_plate (plate),
            INDEX idx_parking_category (category),
            INDEX idx_parking_parked_at (parked_at),
            INDEX idx_parking_exited_at (exited_at)
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS access_logs (
            id INT AUTO_INCREMENT PRIMARY KEY,
            plate VARCHAR(20) NOT NULL,
            category VARCHAR(30) NULL,
            status ENUM(
                'GRANTED',
                'DENIED',
                'PARKING_FULL'
            ) NOT NULL,
            details VARCHAR(120) NULL DEFAULT NULL,
            timestamp TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,

            CONSTRAINT fk_access_category
                FOREIGN KEY (category)
                REFERENCES parking_categories(category)
                ON UPDATE CASCADE
                ON DELETE SET NULL,

            INDEX idx_logs_plate (plate),
            INDEX idx_logs_category (category),
            INDEX idx_logs_status (status),
            INDEX idx_logs_time (timestamp)
        )
    """)


def seed_parking_categories(cur) -> None:
    """
    Inserts or updates parking category capacity and availability data.
    """
    for category, capacity, available in PARKING_CATEGORIES:
        safe_available = min(available, capacity)

        cur.execute("""
            INSERT INTO parking_categories (
                category,
                capacity,
                available
            )
            VALUES (%s, %s, %s)
            ON DUPLICATE KEY UPDATE
                capacity = VALUES(capacity),
                available = LEAST(
                    VALUES(available),
                    VALUES(capacity)
                )
        """, (
            category,
            capacity,
            safe_available,
        ))


def seed_authorised_vehicles(cur) -> None:
    """
    Inserts or updates demo authorised vehicle records.
    """
    for plate, owner_name, category, is_active in DEMO_VEHICLES:
        cur.execute("""
            INSERT INTO authorised_vehicles (
                plate,
                owner_name,
                category,
                is_active
            )
            VALUES (%s, %s, %s, %s)
            ON DUPLICATE KEY UPDATE
                owner_name = VALUES(owner_name),
                category = VALUES(category),
                is_active = VALUES(is_active)
        """, (
            plate,
            owner_name,
            category,
            is_active,
        ))


def sync_parking_availability(cur) -> None:
    """
    Recalculates available parking slots based on vehicles that are
    currently parked.
    """
    cur.execute("""
        UPDATE parking_categories pc
        SET available = GREATEST(
            pc.capacity - (
                SELECT COUNT(*)
                FROM parking p
                WHERE p.category = pc.category
                  AND p.exited_at IS NULL
            ),
            0
        )
    """)


def seed_admin(cur) -> None:
    """
    Creates or updates the default administrator account using a
    securely hashed password.
    """
    password_hash = generate_password_hash(ADMIN_PASSWORD)

    cur.execute("""
        INSERT INTO admins (
            username,
            password_hash
        )
        VALUES (%s, %s)
        ON DUPLICATE KEY UPDATE
            password_hash = VALUES(password_hash)
    """, (
        ADMIN_USERNAME,
        password_hash,
    ))


def seed_sample_logs(cur) -> None:
    """
    Inserts sample access logs only when the access_logs table is empty.
    """
    cur.execute("SELECT COUNT(*) FROM access_logs")
    result = cur.fetchone()

    log_count = result[0] if result is not None else 0

    if log_count == 0:
        cur.executemany("""
            INSERT INTO access_logs (
                plate,
                category,
                status,
                details
            )
            VALUES (%s, %s, %s, %s)
        """, SAMPLE_LOGS)


def close_resource(resource) -> None:
    """
    Safely closes a database cursor or connection.
    """
    try:
        if resource is not None:
            resource.close()
    except Exception:
        pass


if __name__ == "__main__":
    run()
