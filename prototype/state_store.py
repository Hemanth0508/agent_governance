"""
state_store.py
Agent Governance -- Interceptor + State Store Pattern

The single source of truth for all session and constraint state.
Three tables: sessions, constraints, execution_log.
All constraint writes are append-only. No deletes. No updates.
"""

import sqlite3
import uuid
import json
from datetime import datetime, timedelta
from typing import Any, Optional


DB_PATH = "governance.db"


def get_connection() -> sqlite3.Connection:
    """Return a serializable connection to the state store."""
    conn = sqlite3.connect(DB_PATH, isolation_level="DEFERRED")
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def init_db() -> None:
    """
    Initialize the database.
    Creates all three tables and indexes if they do not exist.
    Safe to call multiple times.
    """
    conn = get_connection()
    with conn:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                session_id   TEXT PRIMARY KEY,
                principal_id TEXT NOT NULL,
                created_at   TEXT NOT NULL,
                expires_at   TEXT NOT NULL,
                active       INTEGER DEFAULT 1
            );

            CREATE TABLE IF NOT EXISTS constraints (
                id               INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id       TEXT NOT NULL,
                constraint_key   TEXT NOT NULL,
                constraint_value TEXT NOT NULL,
                set_at           TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );

            CREATE INDEX IF NOT EXISTS idx_constraints_session_key
                ON constraints(session_id, constraint_key, set_at DESC);

            CREATE TABLE IF NOT EXISTS execution_log (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                session_id TEXT NOT NULL,
                tool       TEXT NOT NULL,
                action     TEXT NOT NULL,
                result     TEXT NOT NULL,
                reason     TEXT NOT NULL,
                timestamp  TEXT NOT NULL,
                FOREIGN KEY (session_id) REFERENCES sessions(session_id)
            );

            CREATE INDEX IF NOT EXISTS idx_log_session
                ON execution_log(session_id, timestamp ASC);
        """)
    conn.close()


def create_session(principal_id: str, duration_seconds: int = 3600) -> str:
    """
    Create a new session bound to the given principal.
    The principal_id is immutable after creation.
    Returns the generated session_id.

    duration_seconds: session lifetime. Default 3600 (1 hour).
    For expiry demonstration use 2 seconds.
    """
    session_id = str(uuid.uuid4())
    now = datetime.utcnow()
    expires_at = now + timedelta(seconds=duration_seconds)

    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO sessions (session_id, principal_id, created_at, expires_at, active)
            VALUES (?, ?, ?, ?, 1)
            """,
            (
                session_id,
                principal_id,
                now.isoformat(),
                expires_at.isoformat(),
            ),
        )
    conn.close()
    return session_id


def get_session(session_id: str) -> Optional[sqlite3.Row]:
    """
    Retrieve the session record for the given session_id.
    Returns None if the session does not exist.
    """
    conn = get_connection()
    row = conn.execute(
        "SELECT * FROM sessions WHERE session_id = ?",
        (session_id,)
    ).fetchone()
    conn.close()
    return row


def set_constraint(session_id: str, key: str, value: Any) -> None:
    """
    Append a new constraint row for this session and key.
    Never updates existing rows. The new row becomes current value.
    Value is stored as JSON.
    """
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO constraints (session_id, constraint_key, constraint_value, set_at)
            VALUES (?, ?, ?, ?)
            """,
            (session_id, key, json.dumps(value), datetime.utcnow().isoformat()),
        )
    conn.close()


# Default values for constraints that may not have been set yet
CONSTRAINT_DEFAULTS = {
    "budget_spent":    0.0,
    "pii_accessed":    False,
    "reauth_verified": False,
    "budget_limit":    None,
}


def get_constraint(session_id: str, key: str) -> Any:
    """
    Return the current value of a constraint for this session.
    Current value is the most recently inserted row for this key.
    Returns defined default if no rows exist for this key.
    """
    conn = get_connection()
    row = conn.execute(
        """
        SELECT constraint_value FROM constraints
        WHERE session_id = ? AND constraint_key = ?
        ORDER BY set_at DESC
        LIMIT 1
        """,
        (session_id, key),
    ).fetchone()
    conn.close()

    if row is None:
        return CONSTRAINT_DEFAULTS.get(key, None)
    return json.loads(row["constraint_value"])


def log_execution(
    session_id: str,
    tool: str,
    action: str,
    result: str,
    reason: str,
) -> None:
    """
    Append a decision record to the execution log.
    Called on every interceptor decision -- both ALLOWED and BLOCKED.
    result must be "ALLOWED" or "BLOCKED".
    """
    conn = get_connection()
    with conn:
        conn.execute(
            """
            INSERT INTO execution_log
                (session_id, tool, action, result, reason, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                session_id,
                tool,
                action,
                result,
                reason,
                datetime.utcnow().isoformat(),
            ),
        )
    conn.close()


def get_session_log(session_id: str) -> list:
    """
    Return the complete ordered execution history for a session.
    Ordered by timestamp ascending -- earliest decision first.
    """
    conn = get_connection()
    rows = conn.execute(
        """
        SELECT tool, action, result, reason, timestamp
        FROM execution_log
        WHERE session_id = ?
        ORDER BY timestamp ASC
        """,
        (session_id,),
    ).fetchall()
    conn.close()
    return [dict(row) for row in rows]
