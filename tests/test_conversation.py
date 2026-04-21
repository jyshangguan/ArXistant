"""Tests for the conversation engine."""

import pytest

from src.bot.conversation import _extract_tool_actions, _strip_tool_commands


class TestExtractToolActions:
    def test_scan_command(self):
        text = "I'll check that paper for you.\nSCAN 2504.12345\nLet me know what you think."
        actions = _extract_tool_actions(text)
        assert len(actions) == 1
        assert actions[0]["tool"] == "scan"
        assert actions[0]["arxiv_id"] == "2504.12345"

    def test_read_command(self):
        text = "READ 2604.17015"
        actions = _extract_tool_actions(text)
        assert len(actions) == 1
        assert actions[0]["tool"] == "read"
        assert actions[0]["arxiv_id"] == "2604.17015"

    def test_multiple_commands(self):
        text = "SCAN 2504.12345\n\nREAD 2604.17015"
        actions = _extract_tool_actions(text)
        assert len(actions) == 2
        assert actions[0]["tool"] == "scan"
        assert actions[1]["tool"] == "read"

    def test_no_commands(self):
        text = "Just a regular message about papers."
        actions = _extract_tool_actions(text)
        assert len(actions) == 0

    def test_command_in_middle_of_sentence_ignored(self):
        """SCAN/READ must be on their own line."""
        text = "I think you should SCAN 2504.12345 for more details"
        actions = _extract_tool_actions(text)
        assert len(actions) == 0


class TestStripToolCommands:
    def test_strip_scan(self):
        text = "I'll check that.\nSCAN 2504.12345\nHere's what I found."
        result = _strip_tool_commands(text)
        assert "SCAN" not in result
        assert "I'll check that." in result

    def test_strip_read(self):
        text = "READ 2604.17015"
        result = _strip_tool_commands(text)
        assert result.strip() == ""

    def test_no_change_when_no_commands(self):
        text = "Just a regular response."
        result = _strip_tool_commands(text)
        assert result == text
