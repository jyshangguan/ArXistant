"""Thin wrapper around the openai package for any OpenAI-compatible API."""

from __future__ import annotations

import logging
import httpx

from openai import OpenAI

from .config import Settings

logger = logging.getLogger(__name__)

# Per-request timeout. 120s is generous for LLM completions.
_LLM_TIMEOUT = httpx.Timeout(120.0, connect=10.0)


def create_client(settings: Settings) -> OpenAI:
    """Create an OpenAI client configured for the target provider.

    Disables the built-in retry so that retry logic is controlled entirely
    by callers (scan_paper / read_paper / conversation).
    """
    client = OpenAI(
        api_key=settings.llm_api_key,
        base_url=settings.llm_base_url,
        timeout=_LLM_TIMEOUT,
        max_retries=0,
    )
    return client


def chat_completion(
    client: OpenAI,
    model: str,
    system_prompt: str,
    user_prompt: str,
    temperature: float = 0.1,
) -> str:
    """Send a chat completion request and return the assistant's text response."""
    logger.debug("Sending chat completion request (model=%s)", model)
    response = client.chat.completions.create(
        model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": user_prompt},
        ],
        temperature=temperature,
    )
    content = response.choices[0].message.content or ""
    logger.debug("Received response (%d chars)", len(content))
    return content


def chat_completion_messages(
    client: OpenAI,
    model: str,
    messages: list[dict],
    temperature: float = 0.1,
) -> str:
    """Send a chat completion request with a full message list and return the assistant's text.

    Used by the conversation engine for multi-turn dialogue.
    """
    logger.debug("Sending chat completion request with %d messages (model=%s)", len(messages), model)
    response = client.chat.completions.create(
        model=model,
        messages=messages,
        temperature=temperature,
    )
    content = response.choices[0].message.content or ""
    logger.debug("Received response (%d chars)", len(content))
    return content
