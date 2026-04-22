"""Tests for the debug infrastructure module."""

from __future__ import annotations

import re

import pytest

from src.bot.debug import (
    ErrorRecord,
    clear_errors,
    get_recent_errors,
    is_verbose,
    new_request_id,
    record_error,
    set_verbose,
)


@pytest.fixture(autouse=True)
def _clear_state():
    """Reset the error buffer before each test."""
    clear_errors()
    yield


class TestNewRequestId:
    def test_format_is_6_char_hex(self):
        for _ in range(100):
            rid = new_request_id()
            assert re.fullmatch(r"[0-9a-f]{6}", rid), f"Bad format: {rid}"

    def test_uniqueness(self):
        ids = {new_request_id() for _ in range(200)}
        assert len(ids) == 200


class TestRecordError:
    def test_record_and_retrieve(self):
        err = ValueError("test error")
        rec = record_error("abc123", "cmd:scan", err)

        assert isinstance(rec, ErrorRecord)
        assert rec.request_id == "abc123"
        assert rec.source == "cmd:scan"
        assert rec.error_message == "test error"
        assert "ValueError" in rec.traceback_text
        assert rec.timestamp is not None

    def test_retrieval_returns_most_recent(self):
        record_error("a", "src1", ValueError("first"))
        record_error("b", "src2", TypeError("second"))
        record_error("c", "src3", RuntimeError("third"))

        errors = get_recent_errors(2)
        assert len(errors) == 2
        assert errors[0].request_id == "b"
        assert errors[1].request_id == "c"


class TestRingBuffer:
    def test_maxlen_50(self):
        for i in range(60):
            record_error(f"id{i:03d}", "test", ValueError(f"err {i}"))

        errors = get_recent_errors(100)
        assert len(errors) == 50
        # First error kept should be id010 (oldest surviving)
        assert errors[0].request_id == "id010"
        assert errors[-1].request_id == "id059"


class TestVerboseToggle:
    def test_default_is_not_verbose(self):
        assert not is_verbose("__test_chat_1__")

    def test_set_verbose_on(self):
        set_verbose("__test_chat_2__", True)
        assert is_verbose("__test_chat_2__")
        set_verbose("__test_chat_2__", False)

    def test_set_verbose_off(self):
        set_verbose("__test_chat_3__", True)
        assert is_verbose("__test_chat_3__")
        set_verbose("__test_chat_3__", False)
        assert not is_verbose("__test_chat_3__")

    def test_independence_between_chats(self):
        set_verbose("__test_chat_4a__", True)
        assert not is_verbose("__test_chat_4b__")
        set_verbose("__test_chat_4a__", False)


class TestGetRecentErrors:
    def test_limits(self):
        for i in range(20):
            record_error(f"l{i}", "test", ValueError(f"e{i}"))

        assert len(get_recent_errors(3)) == 3
        assert len(get_recent_errors(5)) == 5
        assert len(get_recent_errors(20)) == 20
        assert len(get_recent_errors(100)) == 20  # only 20 available
