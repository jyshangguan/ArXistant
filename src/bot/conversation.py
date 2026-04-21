"""Session-based conversation engine with LLM."""

from __future__ import annotations

import asyncio
import json
import logging
import re

import sqlite3

from ..config import Settings
from ..llm_client import create_client
from .prompts import CONVERSATION_SYSTEM_PROMPT
from .session_store import get_messages, add_message

logger = logging.getLogger(__name__)

# Patterns to detect tool-use in LLM responses
_SCAN_PATTERN = re.compile(r"^SCAN\s+(\S+)$", re.MULTILINE)
_READ_PATTERN = re.compile(r"^READ\s+(\S+)$", re.MULTILINE)


async def handle_conversation(
    chat_id: str,
    user_text: str,
    db_conn: sqlite3.Connection,
    settings: Settings,
) -> str:
    """Handle a natural language conversation turn.

    1. Load session history
    2. Build messages list
    3. Call LLM
    4. Check for tool use patterns
    5. If tool use: execute, append result, call LLM again
    6. Store assistant response
    7. Return response text
    """
    # Load history
    history = get_messages(db_conn, chat_id)

    # Build messages
    messages = [{"role": "system", "content": CONVERSATION_SYSTEM_PROMPT}]
    for msg in history:
        messages.append({"role": msg["role"], "content": msg["content"]})
    messages.append({"role": "user", "content": user_text})

    # Store user message
    add_message(db_conn, chat_id, "user", user_text)

    # Call LLM
    response_text = await _call_llm(messages, settings)

    # Check for tool use
    tool_actions = _extract_tool_actions(response_text)

    if tool_actions:
        # Execute tools and append results
        for action in tool_actions:
            tool_result = await _execute_tool(
                action["tool"], action["arxiv_id"], db_conn, settings
            )
            messages.append({"role": "assistant", "content": response_text})
            messages.append({"role": "user", "content": f"[Tool result for {action['tool']} {action['arxiv_id']}]:\n{tool_result}"})

        # Call LLM again to summarize
        response_text = await _call_llm(messages, settings)

    # Strip tool commands from final response
    response_text = _strip_tool_commands(response_text)

    # Store assistant response
    add_message(db_conn, chat_id, "assistant", response_text)

    return response_text


async def _call_llm(messages: list[dict], settings: Settings) -> str:
    """Call LLM with message list. Uses chat_completion_messages."""
    from ..llm_client import chat_completion_messages

    client = create_client(settings)
    loop = asyncio.get_event_loop()

    try:
        response = await loop.run_in_executor(
            None,
            lambda: chat_completion_messages(
                client, settings.llm_model, messages, settings.llm_temperature,
            ),
        )
        return response
    except Exception as e:
        logger.error("Conversation LLM call failed: %s", e)
        return f"Sorry, I encountered an error processing your message: {e}"


def _extract_tool_actions(text: str) -> list[dict]:
    """Extract SCAN/READ commands from LLM response."""
    actions = []

    for m in _SCAN_PATTERN.finditer(text):
        actions.append({"tool": "scan", "arxiv_id": m.group(1)})

    for m in _READ_PATTERN.finditer(text):
        actions.append({"tool": "read", "arxiv_id": m.group(1)})

    return actions


def _strip_tool_commands(text: str) -> str:
    """Remove SCAN/READ command lines from LLM response."""
    text = _SCAN_PATTERN.sub("", text)
    text = _READ_PATTERN.sub("", text)
    return text.strip()


async def _execute_tool(
    tool: str,
    arxiv_id: str,
    db_conn: sqlite3.Connection,
    settings: Settings,
) -> str:
    """Execute a scan or read tool and return a text summary of the result."""
    loop = asyncio.get_event_loop()

    try:
        if tool == "scan":
            from ..tools.scan_paper import scan_paper

            result = await loop.run_in_executor(
                None, lambda: scan_paper(arxiv_id, settings, db_conn)
            )
            return (
                f"Scan result for {arxiv_id}:\n"
                f"Title: {result.title}\n"
                f"Quality: {result.quality_score}/5\n"
                f"Reason: {result.quality_reason}\n"
                f"Recommend reading: {result.recommend_reading}\n"
                f"Tree links: {', '.join(f'{l.node_name} ({l.relevance_score}/5)' for l in result.tree_links)}"
            )
        elif tool == "read":
            from ..tools.read_paper import read_paper

            note = await loop.run_in_executor(
                None, lambda: read_paper(arxiv_id, settings, db_conn)
            )
            return (
                f"Reading notes for {arxiv_id}:\n"
                f"Title: {note.title}\n"
                f"Summary: {note.summary}\n"
                f"Key findings: {'; '.join(note.key_findings[:3])}\n"
                f"Tree connections: {', '.join(f'{tc.node_name}: {tc.connection}' for tc in note.tree_connections[:3])}"
            )
        else:
            return f"Unknown tool: {tool}"
    except Exception as e:
        logger.error("Tool execution failed (%s %s): %s", tool, arxiv_id, e)
        return f"Error executing {tool} on {arxiv_id}: {e}"
