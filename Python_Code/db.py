# db.py
# Database connection layer for the ANPR Parking Management System.

import mysql.connector
from mysql.connector import Error

from config import DB_HOST, DB_USER, DB_PASSWORD, DB_NAME, DB_PORT


def get_conn() -> mysql.connector.MySQLConnection:
    """
    Creates and returns a verified MySQL database connection.

    Manual commit mode is used so that insert, update, and delete
    operations can be controlled safely using transactions.
    """
    try:
        conn = mysql.connector.connect(
            host=DB_HOST,
            user=DB_USER,
            password=DB_PASSWORD,
            database=DB_NAME,
            port=DB_PORT,
            connection_timeout=5,
            charset="utf8mb4",
            use_unicode=True,
            autocommit=False,
        )

        if not conn.is_connected():
            raise RuntimeError("Database connection was created but not connected.")

        return conn

    except Error as err:
        print(f"[DB] Connection error: {err}")
        raise RuntimeError(
            "Database connection failed. Please check the database configuration."
        ) from err


def get_cursor(dictionary: bool = False):
    """
    Returns a database connection and cursor.

    Args:
        dictionary: If True, query results are returned as dictionaries.

    Returns:
        tuple: Database connection and cursor.
    """
    conn = get_conn()
    cur = conn.cursor(dictionary=dictionary)
    return conn, cur


def close_db(conn=None, cur=None) -> None:
    """
    Safely closes the database cursor and connection.
    """
    try:
        if cur is not None:
            cur.close()

        if conn is not None and conn.is_connected():
            conn.close()

    except Exception as err:
        print(f"[DB] Close error: {err}")