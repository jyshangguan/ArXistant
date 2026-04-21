"""Tests for scan_paper tool."""

from __future__ import annotations

import json
from unittest.mock import MagicMock, patch

import pytest

from src.tools.scan_paper import scan_paper, _parse_scan_response
from src.tools.types import ScanResult, TreeLink


class TestParseScanResponse:
    """Tests for JSON parsing of scan_paper responses."""

    def test_valid_json(self):
        text = json.dumps({
            "quality_score": 4,
            "quality_reason": "Important result",
            "tree_links": [
                {"node_name": "Bar Formation", "relevance_score": 4, "relevance_reason": "Directly studies"}
            ],
            "recommend_reading": True,
            "rationale": "Directly relevant",
        })
        result = _parse_scan_response(text)
        assert result["quality_score"] == 4
        assert result["recommend_reading"] is True

    def test_json_in_code_fence(self):
        text = '```json\n{"quality_score": 3, "tree_links": [], "recommend_reading": false, "rationale": "not relevant"}\n```'
        result = _parse_scan_response(text)
        assert result["quality_score"] == 3

    def test_malformed_returns_empty(self):
        result = _parse_scan_response("this is not json at all")
        assert result == {}

    def test_extracts_brace_pair(self):
        text = 'Here is the analysis:\n{"quality_score": 5, "tree_links": [{"node_name": "Test", "relevance_score": 5, "relevance_reason": "r"}], "recommend_reading": true, "rationale": "breakthrough"}\nEnd.'
        result = _parse_scan_response(text)
        assert result["quality_score"] == 5


def _make_arxiv_result(title="Test Paper", authors=None, summary="Test abstract", categories=None):
    """Create a mock arxiv.Result."""
    entry = MagicMock()
    entry.title = title
    entry.authors = authors or ["Author A", "Author B"]
    entry.summary = summary
    entry.categories = categories or ["astro-ph.GA"]
    return entry


def _mock_settings():
    """Create mock settings."""
    s = MagicMock()
    s.llm_model = "test-model"
    s.llm_temperature = 0.1
    return s


@patch("src.tools.scan_paper.format_tree_for_prompt", return_value="Knowledge Tree:\n1. Galactic Dynamics")
@patch("src.tools.scan_paper.chat_completion")
@patch("src.tools.scan_paper.create_client")
def test_scan_paper_basic(mock_create_client, mock_chat, mock_tree):
    """Test that scan_paper returns a ScanResult with correct fields."""
    mock_create_client.return_value = MagicMock()

    mock_result = _make_arxiv_result(
        title="Dynamics of Barred Galaxies",
        summary="We study bars in galaxies using SDSS data.",
    )

    llm_response = json.dumps({
        "quality_score": 4,
        "quality_reason": "Important result on galactic bars",
        "tree_links": [
            {"node_name": "Bar Formation", "relevance_score": 4, "relevance_reason": "Directly studies bar dynamics"}
        ],
        "recommend_reading": True,
        "rationale": "Directly relevant to galactic dynamics",
    })
    mock_chat.return_value = llm_response

    settings = _mock_settings()
    db_conn = MagicMock()

    with patch("src.tools.scan_paper.arxiv") as mock_arxiv:
        mock_arxiv.Search.return_value.results.return_value = [mock_result]
        result = scan_paper("2504.12345", settings, db_conn)

    assert isinstance(result, ScanResult)
    assert result.arxiv_id == "2504.12345"
    assert result.title == "Dynamics of Barred Galaxies"
    assert result.quality_score == 4
    assert result.recommend_reading is True
    assert len(result.tree_links) == 1
    assert result.tree_links[0].node_name == "Bar Formation"


@patch("src.tools.scan_paper.format_tree_for_prompt", return_value="Tree")
@patch("src.tools.scan_paper.chat_completion")
@patch("src.tools.scan_paper.create_client")
def test_scan_paper_clamps_score(mock_create_client, mock_chat, mock_tree):
    """Test that quality scores are clamped to 1-5."""
    mock_create_client.return_value = MagicMock()
    mock_result = _make_arxiv_result()

    llm_response = json.dumps({
        "quality_score": 7,
        "quality_reason": "Amazing",
        "tree_links": [{"node_name": "Test", "relevance_score": 10, "relevance_reason": "r"}],
        "recommend_reading": True,
        "rationale": "test",
    })
    mock_chat.return_value = llm_response

    settings = _mock_settings()
    db_conn = MagicMock()

    with patch("src.tools.scan_paper.arxiv") as mock_arxiv:
        mock_arxiv.Search.return_value.results.return_value = [mock_result]
        result = scan_paper("2504.12345", settings, db_conn)

    assert result.quality_score == 5
    assert result.tree_links[0].relevance_score == 5


@patch("src.tools.scan_paper.format_tree_for_prompt", return_value="Tree")
def test_scan_paper_not_found(mock_tree):
    """Test that scan_paper raises ValueError if paper not found."""
    settings = _mock_settings()
    db_conn = MagicMock()

    with patch("src.tools.scan_paper.arxiv") as mock_arxiv:
        mock_arxiv.Search.return_value.results.return_value = []
        with pytest.raises(ValueError, match="not found"):
            scan_paper("2504.99999", settings, db_conn)


@patch("src.tools.scan_paper.format_tree_for_prompt", return_value="Tree")
@patch("src.tools.scan_paper.chat_completion")
@patch("src.tools.scan_paper.create_client")
def test_scan_paper_filters_low_relevance_links(mock_create_client, mock_chat, mock_tree):
    """Test that tree links with relevance < 3 are still included in ScanResult."""
    mock_create_client.return_value = MagicMock()
    mock_result = _make_arxiv_result()

    llm_response = json.dumps({
        "quality_score": 3,
        "quality_reason": "Moderate",
        "tree_links": [
            {"node_name": "Relevant Node", "relevance_score": 4, "relevance_reason": "r"},
            {"node_name": "Less Relevant", "relevance_score": 2, "relevance_reason": "r"},
        ],
        "recommend_reading": True,
        "rationale": "somewhat relevant",
    })
    mock_chat.return_value = llm_response

    settings = _mock_settings()
    db_conn = MagicMock()

    with patch("src.tools.scan_paper.arxiv") as mock_arxiv:
        mock_arxiv.Search.return_value.results.return_value = [mock_result]
        result = scan_paper("2504.12345", settings, db_conn)

    # Both links should be included (scan_paper doesn't filter by relevance threshold)
    assert len(result.tree_links) == 2
