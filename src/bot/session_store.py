"""SQLite-backed session message history (last N messages per chat)."""

from __future__ import annotations

import logging
import sqlite3
from datetime import datetime, timezone

logger = logging.getLogger(__name__)


def ensure_session(conn: sqlite3.Connection, chat_id: str) -> None:
    """Ensure a session exists for this chat_id."""
    conn.execute(
        """INSERT OR IGNORE INTO chat_sessions (chat_id, created_at, updated_at)
           VALUES (?, datetime('now'), datetime('now'))""",
        (chat_id,),
    )
    conn.commit()


def add_message(
    conn: sqlite3.Connection,
    chat_id: str,
    role: str,
    content: str,
) -> None:
    """Add a message to the session. Trims to max messages."""
    ensure_session(conn, chat_id)

    # Get max from settings or use default
    max_msgs = _get_max_messages(conn)

    conn.execute(
        """INSERT INTO session_messages (chat_id, role, content)
           VALUES (?, ?, ?)""",
        (chat_id, role, content),
    )

    # Trim old messages (keep last max_msgs per chat)
    conn.execute(
        """DELETE FROM session_messages WHERE chat_id = ?
           AND id NOT IN (
               SELECT id FROM session_messages
               WHERE chat_id = ?
               ORDER BY id DESC LIMIT ?
           )""",
        (chat_id, chat_id, max_msgs),
    )

    # Update session timestamp
    conn.execute(
        "UPDATE chat_sessions SET updated_at = datetime('now') WHERE chat_id = ?",
        (chat_id,),
    )
    conn.commit()


def get_messages(
    conn: sqlite3.Connection,
    chat_id: str,
    limit: int | None = None,
) -> list[dict]:
    """Get messages for a chat session, newest last."""
    ensure_session(conn, chat_id)

    if limit is not None:
        rows = conn.execute(
            """SELECT role, content FROM session_messages
               WHERE chat_id = ?
               ORDER BY id DESC LIMIT ?""",
            (chat_id, limit),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in reversed(rows)]
    else:
        rows = conn.execute(
            """SELECT role, content FROM session_messages
               WHERE chat_id = ?
               ORDER BY id""",
            (chat_id,),
        ).fetchall()
        return [{"role": r["role"], "content": r["content"]} for r in rows]


def clear_session(conn: sqlite3.Connection, chat_id: str) -> int:
    """Clear all messages for a session. Returns number of messages deleted."""
    ensure_session(conn, chat_id)
    cur = conn.execute(
        "DELETE FROM session_messages WHERE chat_id = ?",
        (chat_id,),
    )
    conn.execute(
        "UPDATE chat_sessions SET updated_at = datetime('now') WHERE chat_id = ?",
        (chat_id,),
    )
    conn.commit()
    return cur.rowcount


def _get_max_messages(conn: sqlite3.Connection) -> int:
    """Get session_max_messages from settings. Falls back to 20."""
    try:
        from ..config import load_settings
        settings = load_settings()
        return settings.session_max_messages
    except Exception:
        return 20
