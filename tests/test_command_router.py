"""Tests for the command router."""

from src.bot.command_router import parse_command, Command


class TestParseCommand:
    def test_scan_command(self):
        cmd = parse_command("/scan 2504.12345")
        assert cmd.name == "scan"
        assert cmd.args == "2504.12345"

    def test_scan_with_version(self):
        cmd = parse_command("/scan 2504.12345v2")
        assert cmd.name == "scan"
        assert cmd.args == "2504.12345v2"

    def test_read_command(self):
        cmd = parse_command("/read 2604.17015")
        assert cmd.name == "read"
        assert cmd.args == "2604.17015"

    def test_report_all(self):
        cmd = parse_command("/report")
        assert cmd.name == "report"
        assert cmd.args == ""

    def test_report_ga(self):
        cmd = parse_command("/report GA")
        assert cmd.name == "report"
        assert cmd.args == "GA"

    def test_report_he(self):
        cmd = parse_command("/report HE")
        assert cmd.name == "report"
        assert cmd.args == "HE"

    def test_tree(self):
        cmd = parse_command("/tree")
        assert cmd.name == "tree"

    def test_tree_with_extra_space(self):
        cmd = parse_command("/tree  ")
        assert cmd.name == "tree"

    def test_prefs(self):
        cmd = parse_command("/prefs")
        assert cmd.name == "prefs"

    def test_reset(self):
        cmd = parse_command("/reset")
        assert cmd.name == "reset"

    def test_help_slash(self):
        cmd = parse_command("/help")
        assert cmd.name == "help"

    def test_help_no_slash(self):
        cmd = parse_command("help")
        assert cmd.name == "help"

    def test_chat_fallback(self):
        cmd = parse_command("what papers about bars came out recently?")
        assert cmd.name == "chat"
        assert cmd.args == ""
        assert "bars" in cmd.raw_text

    def test_chat_multiline(self):
        cmd = parse_command("can you find papers about\nmolecular clouds")
        assert cmd.name == "chat"

    def test_case_insensitive_scan(self):
        cmd = parse_command("/SCAN 2504.12345")
        assert cmd.name == "scan"

    def test_raw_text_preserved(self):
        cmd = parse_command("/scan 2504.12345")
        assert "/scan 2504.12345" == cmd.raw_text

    def test_chat_raw_text_preserved(self):
        text = "what papers about bars came out recently?"
        cmd = parse_command(text)
        assert cmd.raw_text == text
