"""Debug infrastructure: error ring buffer, per-chat verbose toggle, request IDs."""

from __future__ import annotations

import secrets
import threading
import traceback
from collections import deque
from dataclasses import dataclass, field
from datetime import datetime, timezone


@dataclass
class ErrorRecord:
    """A single recorded error."""
    request_id: str
    timestamp: datetime
    source: str          # e.g. "cmd:scan", "callback:read", "scheduler:daily_report"
    error_message: str
    traceback_text: str


# Thread-safe ring buffer (max 50 errors)
_error_buffer: deque[ErrorRecord] = deque(maxlen=50)
_buffer_lock = threading.Lock()

# Per-chat verbose toggle
_verbose_chats: set[str] = set()
_verbose_lock = threading.Lock()


def new_request_id() -> str:
    """Generate a 6-char hex request ID (e.g. 'a3f2b1')."""
    return secrets.token_hex(3)


def record_error(request_id: str, source: str, error: BaseException) -> ErrorRecord:
    """Append an error to the ring buffer and return the record."""
    tb = traceback.format_exception(type(error), error, error.__traceback__)
    record = ErrorRecord(
        request_id=request_id,
        timestamp=datetime.now(timezone.utc),
        source=source,
        error_message=str(error),
        traceback_text="".join(tb),
    )
    with _buffer_lock:
        _error_buffer.append(record)
    return record


def get_recent_errors(n: int = 10) -> list[ErrorRecord]:
    """Return the last *n* errors from the ring buffer."""
    with _buffer_lock:
        items = list(_error_buffer)
    return items[-n:]


def is_verbose(chat_id: str) -> bool:
    """Check whether verbose (full traceback) mode is enabled for a chat."""
    with _verbose_lock:
        return chat_id in _verbose_chats


def clear_errors() -> None:
    """Clear the error buffer (for tests)."""
    with _buffer_lock:
        _error_buffer.clear()



def set_verbose(chat_id: str, enabled: bool) -> bool:
    """Enable or disable verbose mode for a chat. Returns the new state."""
    with _verbose_lock:
        if enabled:
            _verbose_chats.add(chat_id)
        else:
            _verbose_chats.discard(chat_id)
        return enabled
