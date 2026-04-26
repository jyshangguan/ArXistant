"""Tests for read_paper tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest
import requests as req_lib

from src.tools.read_paper import (
    read_paper,
    _truncate_text,
    _select_executive_sections,
    _section_priority,
)
from src.tools.json_utils import parse_llm_json as _parse_read_response
from src.tools.types import ReadingNote, TreeConnection

from tests.test_html_parser import _mock_response, SAMPLE_ARXIV_HTML


# ── Unit tests ──────────────────────────────────────────────────────────


class TestParseReadResponse:
    """Tests for JSON parsing of read_paper responses."""

    def test_valid_json(self):
        text = json.dumps({
            "background": "A study of bars",
            "key_findings": ["Finding 1", "Finding 2"],
            "evaluation": "Solid work",
            "tree_connections": [{"node_name": "Bar Formation", "connection": "Directly studies"}],
        })
        result = _parse_read_response(text)
        assert result["background"] == "A study of bars"
        assert len(result["key_findings"]) == 2

    def test_json_in_code_fence(self):
        text = '```json\n{"background": "test", "key_findings": [], "evaluation": "", "tree_connections": []}\n```'
        result = _parse_read_response(text)
        assert result["background"] == "test"

    def test_malformed_returns_empty(self):
        result = _parse_read_response("not json")
        assert result == {}

    def test_missing_fields_use_get_with_defaults(self):
        """Missing fields are handled by get() with defaults in the caller."""
        text = json.dumps({"background": "only background"})
        result = _parse_read_response(text)
        assert result["background"] == "only background"
        # _parse_read_response returns raw dict; caller uses .get() with defaults
        assert result.get("key_findings", []) == []

    def test_latex_escapes_in_fenced_json(self):
        """LLM often includes LaTeX (\\odot, \\alpha) which are invalid JSON escapes."""
        text = (
            '```json\n'
            '{"background": "the $M_\\odot$ cloud", '
            '"key_findings": ["$\\alpha \\approx -0.71$"], '
            '"evaluation": "good", '
            '"tree_connections": []}\n```'
        )
        result = _parse_read_response(text)
        assert result["background"] == "the $M_\\odot$ cloud"
        assert len(result["key_findings"]) == 1
        assert "\\alpha" in result["key_findings"][0]

    def test_latex_escapes_in_bare_json(self):
        """LaTeX escapes without code fences also get sanitized."""
        text = '{"background": "use \\lambda for wavelength", "key_findings": [], "evaluation": "ok", "tree_connections": []}'
        result = _parse_read_response(text)
        assert "lambda" in result["background"]
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


class TestSectionPriority:
    """Tests for _section_priority."""

    def test_introduction_high_priority(self):
        assert _section_priority("Introduction") == 0

    def test_conclusion_high_priority(self):
        assert _section_priority("Conclusion") == 1
        assert _section_priority("Conclusions") == 1

    def test_results_priority(self):
        assert _section_priority("Results") == 3
        assert _section_priority("Main Results") == 3

    def test_discussion_priority(self):
        assert _section_priority("Discussion") == 6

    def test_unknown_low_priority(self):
        assert _section_priority("Appendix A") == 99
        assert _section_priority("Acknowledgements") == 99

    def test_case_insensitive(self):
        assert _section_priority("INTRODUCTION") == 0
        assert _section_priority("conclusion") == 1


class TestSelectExecutiveSections:
    """Tests for _select_executive_sections."""

    def test_abstract_always_included(self):
        sections = [
            {"number": "1", "title": "Appendix", "text": "x" * 50000},
        ]
        result = _select_executive_sections(sections, "The abstract.", 100)
        assert "Abstract" in result
        assert "The abstract." in result

    def test_priority_order_respected(self):
        sections = [
            {"number": "1", "title": "Appendix", "text": "appendix content"},
            {"number": "2", "title": "Results", "text": "results content"},
            {"number": "3", "title": "Introduction", "text": "intro content"},
        ]
        result = _select_executive_sections(sections, "Abstract.", 500)
        # Introduction should appear before Results, both before Appendix
        intro_pos = result.index("Introduction")
        results_pos = result.index("Results")
        appendix_pos = result.index("Appendix")
        assert intro_pos < results_pos < appendix_pos

    def test_budget_respected(self):
        sections = [
            {"number": "1", "title": "Introduction", "text": "a" * 1000},
            {"number": "2", "title": "Results", "text": "b" * 1000},
        ]
        result = _select_executive_sections(sections, "", 200)
        assert len(result) <= 200

    def test_empty_sections_returns_abstract_only(self):
        result = _select_executive_sections([], "Abstract text.", 1000)
        assert "Abstract text." in result

    def test_all_sections_fit_within_budget(self):
        sections = [
            {"number": "1", "title": "Introduction", "text": "intro"},
            {"number": "2", "title": "Conclusion", "text": "conclusion"},
        ]
        result = _select_executive_sections(sections, "Abs", 10000)
        assert "intro" in result
        assert "conclusion" in result


# ── Integration tests ──────────────────────────────────────────────────


def _mock_settings():
    """Create mock settings with reading options."""
    s = MagicMock()
    s.llm_model = "test-model"
    s.llm_temperature = 0.1
    s.max_text_chars = 80000
    s.executive_read_max_chars = 30000
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


def _mock_llm_response():
    """Return a standard executive-format LLM JSON response."""
    return json.dumps({
        "background": "A comprehensive study of barred galaxies.",
        "key_findings": ["Bar fraction is 30%", "Bars correlate with mass"],
        "evaluation": "Solid contribution using a large dataset.",
        "tree_connections": [
            {"node_name": "Bar Formation", "connection": "Directly studies bar fraction"}
        ],
    })


@patch("src.tools.read_paper.format_tree_for_prompt", return_value="Knowledge Tree:\n1. Galactic Dynamics")
@patch("src.tools.read_paper.chat_completion")
@patch("src.tools.read_paper.create_client")
def test_read_paper_basic(mock_create_client, mock_chat, mock_tree, db_conn):
    """Test that read_paper returns a ReadingNote and stores it in DB."""
    mock_create_client.return_value = MagicMock()
    mock_chat.return_value = _mock_llm_response()

    parsed = _mock_parsed_paper()
    settings = _mock_settings()

    with patch("src.tools.read_paper.fetch_and_parse", return_value=parsed), \
         patch("src.tools.read_paper.get_paper", return_value=None):
        result = read_paper("2504.12345", settings, db_conn)

    assert isinstance(result, ReadingNote)
    assert result.arxiv_id == "2504.12345"
    assert result.title == "Dynamics of Barred Spiral Galaxies"
    assert result.background == "A comprehensive study of barred galaxies."
    assert len(result.key_findings) == 2
    assert result.evaluation == "Solid contribution using a large dataset."
    assert result.cached is False

    # Verify stored in DB
    from src.storage import get_reading_note
    stored = get_reading_note(db_conn, "2504.12345")
    assert stored is not None
    assert stored["summary"] == result.background  # DB 'summary' stores 'background'
    assert stored["methodology"] == result.evaluation  # DB 'methodology' stores 'evaluation'
    assert stored["full_text_hash"].startswith("v2:")


@patch("src.tools.read_paper.format_tree_for_prompt", return_value="Tree")
@patch("src.tools.read_paper.chat_completion")
@patch("src.tools.read_paper.create_client")
def test_read_paper_caching(mock_create_client, mock_chat, mock_tree, db_conn):
    """Test that reading the same paper version returns cached result."""
    mock_create_client.return_value = MagicMock()
    mock_chat.return_value = _mock_llm_response()

    parsed = _mock_parsed_paper()
    settings = _mock_settings()

    # First call - should call LLM and store
    with patch("src.tools.read_paper.fetch_and_parse", return_value=parsed), \
         patch("src.tools.read_paper.get_paper", return_value=None):
        result1 = read_paper("2504.12345", settings, db_conn)

    assert result1.cached is False

    # Second call - should return cached
    with patch("src.tools.read_paper.fetch_and_parse", return_value=parsed), \
         patch("src.tools.read_paper.get_paper", return_value=None):
        result2 = read_paper("2504.12345", settings, db_conn)

    assert result2.cached is True
    assert result2.background == "A comprehensive study of barred galaxies."

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
    mock_chat.return_value = _mock_llm_response()

    parsed_v1 = _mock_parsed_paper()
    settings = _mock_settings()

    # First call
    with patch("src.tools.read_paper.fetch_and_parse", return_value=parsed_v1), \
         patch("src.tools.read_paper.get_paper", return_value=None):
        result1 = read_paper("2504.12345", settings, db_conn)

    assert mock_chat.call_count == 1

    # Paper updated with new hash
    parsed_v2 = _mock_parsed_paper()
    parsed_v2.full_text_hash = "xyz789"

    with patch("src.tools.read_paper.fetch_and_parse", return_value=parsed_v2), \
         patch("src.tools.read_paper.get_paper", return_value=None):
        result2 = read_paper("2504.12345", settings, db_conn)

    assert result2.cached is False
    assert mock_chat.call_count == 2  # LLM called again


@patch("src.tools.read_paper.format_tree_for_prompt", return_value="Tree")
def test_read_paper_llm_failure_raises(mock_tree, db_conn):
    """Test that RuntimeError is raised when LLM call fails."""
    settings = _mock_settings()
    parsed = _mock_parsed_paper()

    with patch("src.tools.read_paper.fetch_and_parse", return_value=parsed), \
         patch("src.tools.read_paper.get_paper", return_value=None), \
         patch("src.tools.read_paper.create_client") as mock_cc, \
         patch("src.tools.read_paper.chat_completion", side_effect=Exception("API error")):
        mock_cc.return_value = MagicMock()
        with pytest.raises(RuntimeError, match="Failed to get LLM response"):
            read_paper("2504.12345", settings, db_conn)


@patch("src.tools.read_paper.format_tree_for_prompt", return_value="Tree")
@patch("src.tools.read_paper.chat_completion")
@patch("src.tools.read_paper.create_client")
def test_read_paper_authors_from_db(mock_create_client, mock_chat, mock_tree, db_conn):
    """Test that authors are loaded from the papers table."""
    mock_create_client.return_value = MagicMock()
    mock_chat.return_value = _mock_llm_response()

    parsed = _mock_parsed_paper()
    settings = _mock_settings()

    mock_paper = MagicMock()
    mock_paper.authors = "Smith J, Doe A, Lee B"

    with patch("src.tools.read_paper.fetch_and_parse", return_value=parsed), \
         patch("src.tools.read_paper.get_paper", return_value=mock_paper):
        result = read_paper("2504.12345", settings, db_conn)

    assert result.authors == "Smith J, Doe A, Lee B"


@patch("src.tools.read_paper.format_tree_for_prompt", return_value="Tree")
@patch("src.tools.read_paper.chat_completion")
@patch("src.tools.read_paper.create_client")
def test_read_paper_key_findings_capped_at_3(mock_create_client, mock_chat, mock_tree, db_conn):
    """Test that key findings are capped at 3."""
    mock_create_client.return_value = MagicMock()
    mock_chat.return_value = json.dumps({
        "background": "test",
        "key_findings": ["F1", "F2", "F3", "F4", "F5"],
        "evaluation": "ok",
        "tree_connections": [],
    })

    parsed = _mock_parsed_paper()
    settings = _mock_settings()

    with patch("src.tools.read_paper.fetch_and_parse", return_value=parsed), \
         patch("src.tools.read_paper.get_paper", return_value=None):
        result = read_paper("2504.12345", settings, db_conn)

    assert len(result.key_findings) == 3
    assert result.key_findings == ["F1", "F2", "F3"]
