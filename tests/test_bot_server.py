"""Tests for the bot server event handler bridge functions."""

import asyncio
import json
import pytest
from unittest.mock import patch, MagicMock, AsyncMock


# ── Helpers to build mock SDK event objects ──────────────────────────────


def _make_message_event(
    message_id: str = "msg_001",
    chat_id: str = "oc_test_chat",
    content: str = '{"text": "/help"}',
    message_type: str = "text",
    user_id: str = "user_001",
):
    """Build a mock P2ImMessageReceiveV1 SDK event object."""
    data = MagicMock()
    data.event.message.message_id = message_id
    data.event.message.chat_id = chat_id
    data.event.message.content = content
    data.event.message.message_type = message_type
    data.event.sender.sender_id.user_id = user_id
    return data


def _make_card_action_event(
    callback_type: str = "read",
    arxiv_id: str = "2504.12345",
    chat_id: str = "oc_test_chat",
):
    """Build a mock P2CardActionTrigger SDK event object."""
    data = MagicMock()
    data.event.action.value = {"type": callback_type, "arxiv_id": arxiv_id, "chat_id": chat_id}
    return data


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def bot_settings():
    from src.config import Settings
    return Settings(
        feishu_app_id="test_app_id",
        feishu_app_secret="test_secret",
        feishu_bot_name="ArXistant",
        target_chat_id="test_chat",
        db_path="data/arxistant.db",
        llm_api_key="test-key",
    )


# ── Tests for _handle_message ────────────────────────────────────────────


class TestHandleMessage:
    """Test the message event handler bridge function."""

    def test_text_message_parses_command(self):
        from src.bot.server import _handle_message
        from src.bot.command_router import parse_command

        data = _make_message_event(content='{"text": "/help"}')
        cmd = parse_command("/help")

        with patch("src.bot.server._main_loop", new_callable=MagicMock) as mock_loop:
            future = asyncio.Future()
            future.set_result(None)
            mock_loop.call_soon_threadsafe = MagicMock()
            # Just verify the handler doesn't crash and calls run_coroutine_threadsafe
            with patch("src.bot.server.asyncio.run_coroutine_threadsafe", return_value=future) as mock_run:
                _handle_message(data)
                mock_run.assert_called_once()
                call_args = mock_run.call_args
                coro = call_args[0][0]
                # The coroutine args should include the command
                assert call_args[0][1] is mock_loop

    def test_non_text_message_ignored(self):
        from src.bot.server import _handle_message

        data = _make_message_event(message_type="image")

        with patch("src.bot.server._main_loop", None):
            with patch("src.bot.server.asyncio.run_coroutine_threadsafe") as mock_run:
                _handle_message(data)
                mock_run.assert_not_called()

    def test_empty_text_ignored(self):
        from src.bot.server import _handle_message

        data = _make_message_event(content='{"text": ""}')

        with patch("src.bot.server._main_loop", None):
            with patch("src.bot.server.asyncio.run_coroutine_threadsafe") as mock_run:
                _handle_message(data)
                mock_run.assert_not_called()

    def test_none_event_ignored(self):
        from src.bot.server import _handle_message

        data = MagicMock()
        data.event = None

        with patch("src.bot.server.asyncio.run_coroutine_threadsafe") as mock_run:
            _handle_message(data)
            mock_run.assert_not_called()


# ── Tests for _handle_card_action ────────────────────────────────────────


class TestHandleCardAction:
    """Test the card action event handler bridge function."""

    def test_valid_card_action(self):
        from src.bot.server import _handle_card_action

        data = _make_card_action_event(callback_type="read", arxiv_id="2504.12345")

        mock_loop = MagicMock()
        future = asyncio.Future()
        future.set_result(None)

        with patch("src.bot.server._main_loop", mock_loop):
            with patch("src.bot.server.asyncio.run_coroutine_threadsafe", return_value=future) as mock_run:
                _handle_card_action(data)
                mock_run.assert_called_once()
                call_args = mock_run.call_args
                coro = call_args[0][0]
                assert call_args[0][1] is mock_loop

    def test_missing_chat_id_ignored(self):
        from src.bot.server import _handle_card_action

        data = _make_card_action_event()
        data.event.action.value = {"type": "read", "arxiv_id": "2504.12345"}

        with patch("src.bot.server.asyncio.run_coroutine_threadsafe") as mock_run:
            _handle_card_action(data)
            mock_run.assert_not_called()

    def test_missing_type_ignored(self):
        from src.bot.server import _handle_card_action

        data = _make_card_action_event()
        data.event.action.value = {"arxiv_id": "2504.12345", "chat_id": "oc_test"}

        with patch("src.bot.server.asyncio.run_coroutine_threadsafe") as mock_run:
            _handle_card_action(data)
            mock_run.assert_not_called()

    def test_none_event_ignored(self):
        from src.bot.server import _handle_card_action

        data = MagicMock()
        data.event = None

        with patch("src.bot.server.asyncio.run_coroutine_threadsafe") as mock_run:
            _handle_card_action(data)
            mock_run.assert_not_called()

    def test_non_dict_action_value_handled(self):
        from src.bot.server import _handle_card_action

        data = _make_card_action_event()
        data.event.action.value = "not a dict"

        with patch("src.bot.server.asyncio.run_coroutine_threadsafe") as mock_run:
            _handle_card_action(data)
            mock_run.assert_not_called()


# ── Tests for module-level accessors ────────────────────────────────────


class TestModuleAccessors:
    """Test get_feishu, get_db, get_app_settings raise before init."""

    def test_get_feishu_raises_before_init(self):
        from src.bot import server
        # Save and restore
        orig = server._feishu
        server._feishu = None
        try:
            with pytest.raises(RuntimeError, match="Feishu client not initialized"):
                server.get_feishu()
        finally:
            server._feishu = orig

    def test_get_db_raises_before_init(self):
        from src.bot import server
        orig = server._db_conn
        server._db_conn = None
        try:
            with pytest.raises(RuntimeError, match="Database not initialized"):
                server.get_db()
        finally:
            server._db_conn = orig

    def test_get_app_settings_raises_before_init(self):
        from src.bot import server
        orig = server._settings
        server._settings = None
        try:
            with pytest.raises(RuntimeError, match="Settings not initialized"):
                server.get_app_settings()
        finally:
            server._settings = orig


# ── Test command routing still works ─────────────────────────────────────


class TestCommandRouting:
    """Verify command_router integration is intact."""

    def test_parse_help_command(self):
        from src.bot.command_router import parse_command
        cmd = parse_command("/help")
        assert cmd.name == "help"

    def test_parse_scan_command(self):
        from src.bot.command_router import parse_command
        cmd = parse_command("/scan 2504.12345")
        assert cmd.name == "scan"
        assert cmd.args == "2504.12345"

    def test_parse_natural_language(self):
        from src.bot.command_router import parse_command
        cmd = parse_command("最近有什么新论文")
        assert cmd.name == "chat"
