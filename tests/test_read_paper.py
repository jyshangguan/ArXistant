"""Tests for read_paper tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests as req_lib

from src.tools.read_paper import (
    read_paper,
    _parse_read_response,
    _truncate_text,
)
from src.tools.types import ReadingNote, TreeConnection

from tests.test_html_parser import _mock_response, SAMPLE_ARXIV_HTML


# ── Unit tests ──────────────────────────────────────────────────────────


class TestParseReadResponse:
    """Tests for JSON parsing of read_paper responses."""

    def test_valid_json(self):
        text = json.dumps({
            "summary": "A study of bars",
            "key_findings": ["Finding 1", "Finding 2"],
            "methodology": "SDSS data",
            "results": "Bar fraction 30%",
            "tree_connections": [{"node_name": "Bar Formation", "connection": "Directly studies"}],
            "unfamiliar_concepts": ["Fourier modes"],
        })
        result = _parse_read_response(text)
        assert result["summary"] == "A study of bars"
        assert len(result["key_findings"]) == 2

    def test_json_in_code_fence(self):
        text = '```json\n{"summary": "test", "key_findings": [], "methodology": "", "results": "", "tree_connections": [], "unfamiliar_concepts": []}\n```'
        result = _parse_read_response(text)
        assert result["summary"] == "test"

    def test_malformed_returns_empty(self):
        result = _parse_read_response("not json")
        assert result == {}

    def test_missing_fields_use_get_with_defaults(self):
        """Missing fields are handled by get() with defaults in the caller."""
        text = json.dumps({"summary": "only summary"})
        result = _parse_read_response(text)
        assert result["summary"] == "only summary"
        # _parse_read_response returns raw dict; caller uses .get() with defaults
        assert result.get("key_findings", []) == []
        assert result.get("tree_connections", []) == []


class TestTruncateText:
    """Tests for text truncation."""

    def test_short_text_unchanged(self):
        text = "hello"
        assert _truncate_text(text, 100) == "hello"

    def test_exact_length_unchanged(self):
        text = "a" * 80
        assert _truncate_text(text, 80) == text

    def test_long_text_truncated(self):
        text = "a" * 200
        result = _truncate_text(text, 100)
        assert len(result) < 200
        assert "[... truncated ...]" in result
        assert len(result) == 100 + len("\n\n[... truncated ...]")


# ── Integration tests ──────────────────────────────────────────────────


def _mock_settings():
    """Create mock settings with reading options."""
    s = MagicMock()
    s.llm_model = "test-model"
    s.llm_temperature = 0.1
    s.max_text_chars = 80000
    s.html_timeout = 5
    return s


def _mock_parsed_paper():
    """Create a mock ParsedPaper."""
    from src.tools.types import ParsedPaper
    return ParsedPaper(
        arxiv_id="2504.12345",
        title="Dynamics of Barred Spiral Galaxies",
        abstract="We study bars in galaxies.",
        sections=[
            {"number": "1", "title": "Introduction", "text": "Bars are common."},
            {"number": "2", "title": "Results", "text": "Bar fraction is 30%."},
        ],
        figures=[],
        full_text_markdown="# Dynamics of Barred Spiral Galaxies\n\n## Abstract\nWe study bars.\n\n## 1 Introduction\nBars are common.\n\n## 2 Results\nBar fraction is 30%.",
        full_text_hash="abc123",
    )


@patch("src.tools.read_paper.format_tree_for_prompt", return_value="Knowledge Tree:\n1. Galactic Dynamics")
@patch("src.tools.read_paper.chat_completion")
@patch("src.tools.read_paper.create_client")
def test_read_paper_basic(mock_create_client, mock_chat, mock_tree, db_conn):
    """Test that read_paper returns a ReadingNote and stores it in DB."""
    mock_create_client.return_value = MagicMock()

    llm_response = json.dumps({
        "summary": "A comprehensive study of barred galaxies.",
        "key_findings": ["Bar fraction is 30%", "Bars correlate with mass"],
        "methodology": "SDSS DR18 data, Fourier decomposition",
        "results": "Bar fraction increases from 15% to 45% with stellar mass",
        "tree_connections": [
            {"node_name": "Bar Formation", "connection": "Directly studies bar fraction"}
        ],
        "unfamiliar_concepts": ["Fourier modes", "m = 2 mode"],
    })
    mock_chat.return_value = llm_response

    parsed = _mock_parsed_paper()
    settings = _mock_settings()

    with patch("src.tools.read_paper.fetch_and_parse", return_value=parsed):
        result = read_paper("2504.12345", settings, db_conn)

    assert isinstance(result, ReadingNote)
    assert result.arxiv_id == "2504.12345"
    assert result.title == "Dynamics of Barred Spiral Galaxies"
    assert result.summary == "A comprehensive study of barred galaxies."
    assert len(result.key_findings) == 2
    assert result.cached is False

    # Verify stored in DB
    from src.storage import get_reading_note
    stored = get_reading_note(db_conn, "2504.12345")
    assert stored is not None
    assert stored["summary"] == result.summary
    assert stored["full_text_hash"] == "abc123"


@patch("src.tools.read_paper.format_tree_for_prompt", return_value="Tree")
@patch("src.tools.read_paper.chat_completion")
@patch("src.tools.read_paper.create_client")
def test_read_paper_caching(mock_create_client, mock_chat, mock_tree, db_conn):
    """Test that reading the same paper version returns cached result."""
    mock_create_client.return_value = MagicMock()

    llm_response = json.dumps({
        "summary": "Cached summary",
        "key_findings": [],
        "methodology": "",
        "results": "",
        "tree_connections": [],
        "unfamiliar_concepts": [],
    })
    mock_chat.return_value = llm_response

    parsed = _mock_parsed_paper()
    settings = _mock_settings()

    # First call - should call LLM and store
    with patch("src.tools.read_paper.fetch_and_parse", return_value=parsed):
        result1 = read_paper("2504.12345", settings, db_conn)

    assert result1.cached is False

    # Second call - should return cached
    with patch("src.tools.read_paper.fetch_and_parse", return_value=parsed):
        result2 = read_paper("2504.12345", settings, db_conn)

    assert result2.cached is True
    assert result2.summary == "Cached summary"

    # LLM should only have been called once
    assert mock_chat.call_count == 1


@patch("src.tools.read_paper.format_tree_for_prompt", return_value="Tree")
@patch("src.tools.read_paper.chat_completion")
@patch("src.tools.read_paper.create_client")
def test_read_paper_cache_invalidated_on_hash_change(
    mock_create_client, mock_chat, mock_tree, db_conn
):
    """Test that a changed full_text_hash triggers re-analysis."""
    mock_create_client.return_value = MagicMock()

    llm_response = json.dumps({
        "summary": "New summary",
        "key_findings": [],
        "methodology": "",
        "results": "",
        "tree_connections": [],
        "unfamiliar_concepts": [],
    })
    mock_chat.return_value = llm_response

    parsed_v1 = _mock_parsed_paper()
    settings = _mock_settings()

    # First call
    with patch("src.tools.read_paper.fetch_and_parse", return_value=parsed_v1):
        result1 = read_paper("2504.12345", settings, db_conn)

    assert mock_chat.call_count == 1

    # Paper updated with new hash
    parsed_v2 = _mock_parsed_paper()
    parsed_v2.full_text_hash = "xyz789"

    with patch("src.tools.read_paper.fetch_and_parse", return_value=parsed_v2):
        result2 = read_paper("2504.12345", settings, db_conn)

    assert result2.cached is False
    assert mock_chat.call_count == 2  # LLM called again


@patch("src.tools.read_paper.format_tree_for_prompt", return_value="Tree")
def test_read_paper_llm_failure_raises(mock_tree, db_conn):
    """Test that RuntimeError is raised when LLM call fails."""
    settings = _mock_settings()
    parsed = _mock_parsed_paper()

    with patch("src.tools.read_paper.fetch_and_parse", return_value=parsed), \
         patch("src.tools.read_paper.create_client") as mock_cc, \
         patch("src.tools.read_paper.chat_completion", side_effect=Exception("API error")):
        mock_cc.return_value = MagicMock()
        with pytest.raises(RuntimeError, match="Failed to get LLM response"):
            read_paper("2504.12345", settings, db_conn)
