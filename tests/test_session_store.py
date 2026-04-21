"""Tests for the session store."""

import pytest

from src.storage import init_db
from src.bot.session_store import (
    ensure_session,
    add_message,
    get_messages,
    clear_session,
)


@pytest.fixture
def db_conn():
    conn = init_db(":memory:")
    yield conn
    conn.close()


class TestEnsureSession:
    def test_creates_session(self, db_conn):
        ensure_session(db_conn, "chat_123")
        row = db_conn.execute(
            "SELECT chat_id FROM chat_sessions WHERE chat_id = ?",
            ("chat_123",),
        ).fetchone()
        assert row is not None

    def test_idempotent(self, db_conn):
        ensure_session(db_conn, "chat_123")
        ensure_session(db_conn, "chat_123")
        row = db_conn.execute(
            "SELECT COUNT(*) FROM chat_sessions WHERE chat_id = ?",
            ("chat_123",),
        ).fetchone()
        assert row[0] == 1


class TestAddAndGetMessages:
    def test_add_and_get_single_message(self, db_conn):
        add_message(db_conn, "chat_1", "user", "Hello")
        msgs = get_messages(db_conn, "chat_1")
        assert len(msgs) == 1
        assert msgs[0]["role"] == "user"
        assert msgs[0]["content"] == "Hello"

    def test_multiple_messages_ordered(self, db_conn):
        add_message(db_conn, "chat_1", "user", "First")
        add_message(db_conn, "chat_1", "assistant", "Second")
        add_message(db_conn, "chat_1", "user", "Third")

        msgs = get_messages(db_conn, "chat_1")
        assert len(msgs) == 3
        assert msgs[0]["content"] == "First"
        assert msgs[1]["content"] == "Second"
        assert msgs[2]["content"] == "Third"

    def test_separate_chats(self, db_conn):
        add_message(db_conn, "chat_a", "user", "Hello A")
        add_message(db_conn, "chat_b", "user", "Hello B")

        msgs_a = get_messages(db_conn, "chat_a")
        msgs_b = get_messages(db_conn, "chat_b")
        assert len(msgs_a) == 1
        assert len(msgs_b) == 1
        assert msgs_a[0]["content"] == "Hello A"
        assert msgs_b[0]["content"] == "Hello B"

    def test_get_with_limit(self, db_conn):
        for i in range(10):
            add_message(db_conn, "chat_1", "user", f"msg_{i}")

        msgs = get_messages(db_conn, "chat_1", limit=5)
        assert len(msgs) == 5
        # Should get the last 5 messages
        assert msgs[0]["content"] == "msg_5"
        assert msgs[-1]["content"] == "msg_9"

    def test_trimming(self, db_conn):
        """Messages should be trimmed to session_max_messages."""
        # Add many messages (default max is 20, but for test we add more)
        for i in range(25):
            add_message(db_conn, "chat_trim", "user", f"msg_{i}")

        msgs = get_messages(db_conn, "chat_trim")
        # Should have at most 20 messages
        assert len(msgs) <= 20


class TestClearSession:
    def test_clear_all_messages(self, db_conn):
        for i in range(5):
            add_message(db_conn, "chat_1", "user", f"msg_{i}")

        deleted = clear_session(db_conn, "chat_1")
        assert deleted == 5

        msgs = get_messages(db_conn, "chat_1")
        assert len(msgs) == 0

    def test_clear_empty_session(self, db_conn):
        deleted = clear_session(db_conn, "chat_empty")
        assert deleted == 0

    def test_session_still_exists_after_clear(self, db_conn):
        add_message(db_conn, "chat_1", "user", "msg")
        clear_session(db_conn, "chat_1")

        row = db_conn.execute(
            "SELECT chat_id FROM chat_sessions WHERE chat_id = ?",
            ("chat_1",),
        ).fetchone()
        assert row is not None
