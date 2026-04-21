"""FastAPI app: webhook endpoint, lifespan (scheduler + DB)."""

from __future__ import annotations

import json
import logging
import sqlite3
from contextlib import asynccontextmanager
from datetime import datetime, timezone

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from ..config import load_settings
from ..storage import init_db
from .feishu_client import FeishuClient
from .command_router import parse_command

logger = logging.getLogger(__name__)

# Module-level state — set during lifespan
_feishu: FeishuClient | None = None
_db_conn: sqlite3.Connection | None = None
_settings = None


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


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Startup: init DB, Feishu client, scheduler. Shutdown: clean up."""
    global _feishu, _db_conn, _settings

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

    # Start scheduler
    from .scheduler import start_scheduler, stop_scheduler
    start_scheduler(settings, conn, feishu)
    logger.info("Scheduler started")

    yield

    # Shutdown
    stop_scheduler()
    await feishu.close()
    conn.close()
    logger.info("ArXistant bot service stopped")


app = FastAPI(
    title="ArXistant Bot",
    lifespan=lifespan,
)


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.post("/{webhook_path:path}")
async def webhook(request: Request):
    """Handle incoming Feishu webhook events."""
    settings = get_app_settings()
    feishu = get_feishu()

    body = await request.body()
    body_text = body.decode("utf-8")
    payload = json.loads(body_text)

    logger.debug("Webhook payload: %s", json.dumps(payload, ensure_ascii=False)[:500])

    # Handle URL verification challenge
    if payload.get("type") == "url_verification":
        return JSONResponse(content={"challenge": payload["challenge"]})

    # Only process message events (im.message.receive_v1)
    if payload.get("header", {}).get("event_type") != "im.message.receive_v1":
        return JSONResponse(content={"code": 0})

    event = payload.get("event", {})
    message = event.get("message", {})
    sender = event.get("sender", {})
    chat_id = message.get("chat_id", "")

    # Skip messages from the bot itself
    sender_id = sender.get("sender_id", {}).get("user_id", "")
    if sender_id and sender_id == "":  # bot messages have empty or special sender
        pass  # We check message_type below

    message_type = message.get("message_type", "")
    if message_type != "text":
        logger.debug("Ignoring non-text message (type=%s)", message_type)
        return JSONResponse(content={"code": 0})

    # Parse user text content
    content_str = message.get("content", "{}")
    try:
        content = json.loads(content_str)
        user_text = content.get("text", "").strip()
    except json.JSONDecodeError:
        user_text = content_str.strip()

    if not user_text:
        return JSONResponse(content={"code": 0})

    message_id = message.get("message_id", "")
    logger.info("Received message from %s: %s", chat_id, user_text[:100])

    # Parse command
    cmd = parse_command(user_text)

    # Fire-and-forget: process asynchronously (Feishu requires 3s response)
    import asyncio
    from .command_handler import handle_command

    asyncio.create_task(
        handle_command(cmd, chat_id, message_id, user_text)
    )

    return JSONResponse(content={"code": 0})


@app.post("/{webhook_path:path}/card_callback")
async def card_callback(request: Request):
    """Handle Feishu interactive card button callbacks."""
    feishu = get_feishu()
    db = get_db()
    settings = get_app_settings()

    body = await request.body()
    payload = json.loads(body.decode("utf-8"))

    logger.debug("Card callback payload: %s", json.dumps(payload, ensure_ascii=False)[:500])

    action = payload.get("action", {})
    action_value = action.get("value", {})

    callback_type = action_value.get("type", "")
    arxiv_id = action_value.get("arxiv_id", "")
    chat_id = action_value.get("chat_id", "")

    if not chat_id or not callback_type:
        logger.warning("Invalid card callback: missing chat_id or type")
        return JSONResponse(content={"code": 0})

    import asyncio
    from .command_handler import handle_card_callback

    asyncio.create_task(
        handle_card_callback(callback_type, arxiv_id, chat_id, feishu, db, settings)
    )

    return JSONResponse(content={"code": 0})
