"""Bot entry point: WebSocket long connection mode via lark-oapi SDK."""

from __future__ import annotations

import asyncio
import json
import logging
import signal
import sqlite3
import threading

import lark_oapi as lark
from lark_oapi.api.im.v1 import *

from ..config import load_settings
from ..storage import init_db
from .feishu_client import FeishuClient
from .command_router import parse_command

logger = logging.getLogger(__name__)

# Module-level state — set during main()
_feishu: FeishuClient | None = None
_db_conn: sqlite3.Connection | None = None
_settings = None
_main_loop: asyncio.AbstractEventLoop | None = None


def get_feishu() -> FeishuClient:
    if _feishu is None:
        raise RuntimeError("Feishu client not initialized")
    return _feishu


def get_db() -> sqlite3.Connection:
    if _db_conn is None:
        raise RuntimeError("Database not initialized")
    return _db_conn


def get_app_settings():
    if _settings is None:
        raise RuntimeError("Settings not initialized")
    return _settings


# ── SDK event handlers (sync, called in WSClient thread) ────────────────


def _handle_message(data: P2ImMessageReceiveV1) -> None:
    """Handle im.message.receive_v1 event from SDK."""
    event = data.event
    if event is None:
        return

    message = event.message
    sender = event.sender
    if message is None or sender is None:
        return

    message_id = message.message_id
    chat_id = message.chat_id
    content = message.content
    message_type = message.message_type

    if sender.sender_id is None:
        return
    user_id = sender.sender_id.user_id

    # Skip non-text messages
    if message_type != "text":
        logger.debug("Ignoring non-text message (type=%s)", message_type)
        return

    # Parse user text from JSON content
    try:
        content_dict = json.loads(content) if content else {}
        user_text = content_dict.get("text", "").strip()
    except (json.JSONDecodeError, TypeError):
        user_text = (content or "").strip()

    if not user_text:
        return

    logger.info("Received message from %s: %s", chat_id, user_text[:100])

    cmd = parse_command(user_text)

    # Bridge to async handler in main thread
    if _main_loop is not None:
        asyncio.run_coroutine_threadsafe(
            _async_handle_command(cmd, chat_id, message_id, user_text),
            _main_loop,
        )


def _handle_card_action(data: P2CardActionTrigger) -> None:
    """Handle card action trigger (button click) from SDK."""
    event = data.event
    if event is None:
        return

    action = event.action
    if action is None:
        return

    action_value = action.value
    if not isinstance(action_value, dict):
        action_value = {}

    callback_type = action_value.get("type", "")
    arxiv_id = action_value.get("arxiv_id", "")
    chat_id = action_value.get("chat_id", "")

    if not chat_id or not callback_type:
        logger.warning("Invalid card callback: missing chat_id or type")
        return

    logger.info("Card action: type=%s, arxiv_id=%s, chat_id=%s", callback_type, arxiv_id, chat_id)

    if _main_loop is not None:
        asyncio.run_coroutine_threadsafe(
            _async_handle_card_callback(callback_type, arxiv_id, chat_id),
            _main_loop,
        )


# ── Async wrappers (run in main thread's event loop) ────────────────────


async def _async_handle_command(cmd, chat_id: str, message_id: str, raw_text: str) -> None:
    from .command_handler import handle_command
    await handle_command(cmd, chat_id, message_id, raw_text)


async def _async_handle_card_callback(callback_type: str, arxiv_id: str, chat_id: str) -> None:
    from .command_handler import handle_card_callback
    await handle_card_callback(callback_type, arxiv_id, chat_id, get_feishu(), get_db(), get_app_settings())


# ── Main entry point ────────────────────────────────────────────────────


def _run_ws_client(app_id: str, app_secret: str, encrypt_key: str = "", verification_token: str = "") -> None:
    """Run lark.ws.Client in the WSClient daemon thread."""
    cli = lark.ws.Client(
        app_id=app_id,
        app_secret=app_secret,
        event_handler=lark.EventDispatcherHandler("")
            .register_p2_im_message_receive_v1(_handle_message)
            .register_p2_card_action_trigger(_handle_card_action),
        log_level=lark.LogLevel.DEBUG,
    )
    logger.info("Starting WebSocket client...")
    cli.start()


def main() -> None:
    """Initialize services and start the bot."""
    global _feishu, _db_conn, _settings, _main_loop

    settings = load_settings()
    _settings = settings
    logger.info("Initializing ArXistant bot service...")

    # Init database
    conn = init_db(settings.db_path)
    _db_conn = conn
    logger.info("Database initialized at %s", settings.db_path)

    # Init Feishu client
    feishu = FeishuClient(settings)
    _feishu = feishu
    logger.info("Feishu client initialized")

    # Create main thread event loop
    loop = asyncio.new_event_loop()
    _main_loop = loop
    asyncio.set_event_loop(loop)

    # Start scheduler in the asyncio loop
    from .scheduler import start_scheduler, stop_scheduler
    loop.call_soon_threadsafe(start_scheduler, settings, conn, feishu)
    logger.info("Scheduler started")

    # Start WSClient in a daemon thread
    ws_thread = threading.Thread(
        target=_run_ws_client,
        args=(settings.feishu_app_id, settings.feishu_app_secret,
              settings.feishu_encrypt_key, settings.feishu_verification_token),
        daemon=True,
        name="ws-client",
    )
    ws_thread.start()
    logger.info("WebSocket client thread started")

    # Shutdown handler
    def _shutdown(signum, frame):
        logger.info("Received signal %s, shutting down...", signum)
        stop_scheduler()
        loop.call_soon_threadsafe(loop.stop)

    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)

    # Run the asyncio loop (blocks until stop())
    try:
        loop.run_forever()
    finally:
        loop.run_until_complete(feishu.close())
        conn.close()
        logger.info("ArXistant bot service stopped")


if __name__ == "__main__":
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    )
    main()
