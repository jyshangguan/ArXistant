"""Parse incoming Feishu message text into commands."""

from __future__ import annotations

import re
from dataclasses import dataclass


@dataclass
class Command:
    """Parsed command from user message."""
    name: str           # e.g. "scan", "read", "report", "tree", "help", "prefs", "reset"
    args: str           # arguments after the command
    raw_text: str       # original user message


# Patterns: (name, regex)
_PATTERNS = [
    ("scan",   r"^/scan\s+(.+)$"),
    ("read",   r"^/read\s+(.+)$"),
    ("report", r"^/report\s*(.*)$"),
    ("fetch",  r"^/fetch\s*(.*)$"),
    ("tree",   r"^/tree\s*$"),
    ("build",  r"^/build\s*(.*)$"),
    ("prefs",  r"^/prefs\s*$"),
    ("reset",  r"^/reset\s*$"),
    ("debug",  r"^/debug\s*(.*)$"),
    ("help",   r"^(?:/help|help)\s*$"),
]


def parse_command(text: str) -> Command:
    """Parse user text into a Command.

    If no command pattern matches, returns Command(name="chat", ...).
    """
    text = text.strip()

    for name, pattern in _PATTERNS:
        m = re.match(pattern, text, re.IGNORECASE)
        if m:
            return Command(name=name, args=m.group(1).strip() if m.lastindex else "", raw_text=text)

    return Command(name="chat", args="", raw_text=text)
