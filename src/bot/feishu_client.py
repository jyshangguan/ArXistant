"""Feishu API client: auth token, send message/card."""

from __future__ import annotations

import json
import logging
import time

import httpx

from ..config import Settings

logger = logging.getLogger(__name__)

FEISHU_API_BASE = "https://open.feishu.cn/open-apis"
TOKEN_EXPIRY_BUFFER = 300  # refresh 5 min before expiry


class FeishuAPIError(Exception):
    """Raised when a Feishu API call returns a non-2xx status."""
    def __init__(self, operation: str, status_code: int, body: str):
        self.operation = operation
        self.status_code = status_code
        self.body = body
        super().__init__(f"Feishu API error in {operation}: HTTP {status_code} — {body[:200]}")


class FeishuClient:
    """Low-level Feishu API wrapper using httpx."""

    def __init__(self, settings: Settings):
        self._app_id = settings.feishu_app_id
        self._app_secret = settings.feishu_app_secret
        self._bot_name = settings.feishu_bot_name
        self._http = httpx.AsyncClient(timeout=30)
        self._token: str = ""
        self._token_expires_at: float = 0

    # ── Auth ────────────────────────────────────────────────────────────

    def _check_response(self, resp, operation: str) -> dict:
        """Check HTTP response; raise FeishuAPIError on failure."""
        try:
            resp.raise_for_status()
        except httpx.HTTPStatusError:
            body = resp.text[:200]
            raise FeishuAPIError(operation, resp.status_code, body)
        return resp.json()

    async def get_token(self) -> str:
        """Get or refresh the tenant access token."""
        if self._token and time.time() < self._token_expires_at:
            return self._token

        resp = await self._http.post(
            f"{FEISHU_API_BASE}/auth/v3/tenant_access_token/internal",
            json={
                "app_id": self._app_id,
                "app_secret": self._app_secret,
            },
        )
        data = self._check_response(resp, "get_token")
        self._token = data["tenant_access_token"]
        self._token_expires_at = time.time() + data["expire"] - TOKEN_EXPIRY_BUFFER
        logger.debug("Feishu token refreshed, expires in %ds", data["expire"])
        return self._token

    # ── Send messages ───────────────────────────────────────────────────

    async def send_text(self, chat_id: str, text: str) -> dict:
        """Send a plain text message to a chat."""
        token = await self.get_token()
        resp = await self._http.post(
            f"{FEISHU_API_BASE}/im/v1/messages",
            headers={"Authorization": f"Bearer {token}"},
            params={"receive_id_type": "chat_id"},
            json={
                "receive_id": chat_id,
                "msg_type": "text",
                "content": json.dumps({"text": text}),
            },
        )
        return self._check_response(resp, "send_text")

    async def send_card(self, chat_id: str, card: dict) -> dict:
        """Send an interactive card message to a chat."""
        token = await self.get_token()
        resp = await self._http.post(
            f"{FEISHU_API_BASE}/im/v1/messages",
            headers={"Authorization": f"Bearer {token}"},
            params={"receive_id_type": "chat_id"},
            json={
                "receive_id": chat_id,
                "msg_type": "interactive",
                "content": json.dumps(card),
            },
        )
        return self._check_response(resp, "send_card")

    async def reply_card(self, message_id: str, card: dict) -> dict:
        """Reply to a message with an interactive card."""
        token = await self.get_token()
        resp = await self._http.post(
            f"{FEISHU_API_BASE}/im/v1/messages/{message_id}/reply",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "msg_type": "interactive",
                "content": json.dumps(card),
            },
        )
        return self._check_response(resp, "reply_card")

    async def reply_text(self, message_id: str, text: str) -> dict:
        """Reply to a message with plain text."""
        token = await self.get_token()
        resp = await self._http.post(
            f"{FEISHU_API_BASE}/im/v1/messages/{message_id}/reply",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "msg_type": "text",
                "content": json.dumps({"text": text}),
            },
        )
        return self._check_response(resp, "reply_text")

    async def update_card(self, message_id: str, card: dict) -> dict:
        """Update an existing interactive card message in-place."""
        token = await self.get_token()
        resp = await self._http.patch(
            f"{FEISHU_API_BASE}/im/v1/messages/{message_id}",
            headers={"Authorization": f"Bearer {token}"},
            json={
                "msg_type": "interactive",
                "content": json.dumps(card),
            },
        )
        return self._check_response(resp, "update_card")

    async def upload_image(self, image_bytes: bytes, image_type: str = "message") -> str:
        """Upload an image to Feishu and return the image_key for use in cards."""
        token = await self.get_token()
        resp = await self._http.post(
            f"{FEISHU_API_BASE}/im/v1/images",
            headers={"Authorization": f"Bearer {token}"},
            data={"image_type": image_type},
            files={"image": ("image.gif", image_bytes, "image/gif")},
        )
        data = self._check_response(resp, "upload_image")
        return data["data"]["image_key"]

    async def close(self) -> None:
        """Close the underlying HTTP client."""
        await self._http.aclose()
