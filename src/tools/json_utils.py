"""Shared JSON parsing utilities for LLM responses."""

from __future__ import annotations

import json
import logging
import re

logger = logging.getLogger(__name__)


def sanitize_json_escapes(s: str) -> str:
    """Fix invalid JSON escape sequences (e.g. LaTeX \\lambda, \\odot) by doubling the backslash."""
    return re.sub(
        r"\\(?![\\\"/bfnrtu])",
        r"\\\\",
        s,
    )


def parse_llm_json(text: str, *, expected_root: str | None = None) -> dict | list:
    """Extract JSON from an LLM response.

    Tries, in order:
    1. Direct ``json.loads``
    2. Code-fence extraction (`````json ... `````)
    3. Brace extraction with LaTeX-escape sanitization

    Args:
        text: Raw LLM response text.
        expected_root: If provided, return ``result[expected_root]`` instead of
            the full dict (useful when the LLM wraps output in ``{"points": [...]}``).

    Returns:
        Parsed dict or list.  Returns ``{}`` on all parse failures.
    """
    text = text.strip()

    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return _unwrap(result, expected_root)
    except json.JSONDecodeError:
        pass

    # Try code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence_match:
        try:
            result = json.loads(fence_match.group(1).strip())
            if isinstance(result, dict):
                return _unwrap(result, expected_root)
        except json.JSONDecodeError:
            pass

    # Try brace extraction with escape sanitization
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            result = json.loads(brace_match.group(0))
            if isinstance(result, dict):
                return _unwrap(result, expected_root)
        except json.JSONDecodeError:
            sanitized = sanitize_json_escapes(brace_match.group(0))
            try:
                result = json.loads(sanitized)
                if isinstance(result, dict):
                    return _unwrap(result, expected_root)
            except json.JSONDecodeError:
                pass

    logger.warning("Could not parse LLM response as JSON")
    return {}


def _unwrap(result: dict, expected_root: str | None) -> dict | list:
    """If *expected_root* is set, return ``result[expected_root]`` (with fallback)."""
    if expected_root is None:
        return result
    return result.get(expected_root, {})
