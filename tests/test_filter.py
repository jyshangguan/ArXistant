"""Tests for src/filter.py — _format_topics, _format_papers, _parse_response, filter_papers."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from src.filter import (
    SYSTEM_PROMPT,
    _format_papers,
    _format_topics,
    _parse_response,
    filter_papers,
)
from src.config import Settings, Topic
from src.collector import RawPaper
from src.filter import RelevantPaper
from datetime import datetime, timezone


# ── _format_topics ───────────────────────────────────────────────────────


class TestFormatTopics:
    def test_single_topic(self, sample_topic):
        result = _format_topics([sample_topic])
        assert "1. Galactic Dynamics" in result
        assert "Description: Dynamics and structure" in result
        assert "Keywords: galactic dynamics, Milky Way, spiral arms" in result

    def test_multiple_topics(self, sample_topics):
        result = _format_topics(sample_topics)
        assert "1. Galactic Dynamics" in result
        assert "2. High-Energy Transients" in result

    def test_empty_list(self):
        assert _format_topics([]) == ""


# ── _format_papers ───────────────────────────────────────────────────────


class TestFormatPapers:
    def test_basic_formatting(self, sample_paper):
        result = _format_papers([sample_paper])
        assert "[0]" in result
        assert "Title: Dynamics of Barred Spiral Galaxies" in result
        assert "Authors: Alice Smith, Bob Jones, Carol White" in result

    def test_author_truncation_with_et_al(self, sample_paper):
        # sample_paper has 4 authors -> should show "et al."
        result = _format_papers([sample_paper])
        assert "et al. (4 authors)" in result

    def test_exactly_three_authors_no_truncation(self):
        p = RawPaper(
            arxiv_id="1", title="T",
            authors=["A", "B", "C"],
            abstract="Abs", published=datetime(2025, 1, 1, tzinfo=timezone.utc),
            categories=["x"], primary_category="x",
            pdf_url="", entry_url="",
        )
        result = _format_papers([p])
        assert "et al." not in result
        assert "A, B, C" in result

    def test_empty_list(self):
        assert _format_papers([]) == ""


# ── _parse_response ──────────────────────────────────────────────────────


class TestParseResponse:
    def test_direct_json(self):
        text = '[{"index": 0, "score": 3}]'
        result = _parse_response(text)
        assert result == [{"index": 0, "score": 3}]

    def test_code_fence_json(self):
        text = '```json\n[{"index": 0, "score": 2}]\n```'
        result = _parse_response(text)
        assert result == [{"index": 0, "score": 2}]

    def test_code_fence_no_lang_tag(self):
        text = '```\n[{"index": 1, "score": 4}]\n```'
        result = _parse_response(text)
        assert result == [{"index": 1, "score": 4}]

    def test_bracket_extraction(self):
        text = 'Here is the result: [{"index": 0, "score": 1}] and some trailing text.'
        result = _parse_response(text)
        assert result == [{"index": 0, "score": 1}]

    def test_garbage_input(self):
        assert _parse_response("this is not json at all") == []

    def test_whitespace_only(self):
        assert _parse_response("   \n\t  ") == []

    def test_malformed_json(self):
        assert _parse_response('[{invalid}]') == []

    def test_json_object_not_array(self):
        # A JSON object (not array) should be skipped
        text = '{"key": "value"}'
        assert _parse_response(text) == []


# ── filter_papers ────────────────────────────────────────────────────────


class TestFilterPapers:
    @patch("src.filter.create_client")
    @patch("src.filter.chat_completion")
    def test_threshold_filtering(self, mock_chat, mock_create, sample_papers, sample_topics, sample_settings):
        """Papers below threshold should be excluded."""
        mock_chat.return_value = (
            '[{"index": 0, "score": 5, "matched_topic": "Galactic Dynamics", "reason": "Great"}, '
            '{"index": 1, "score": 2, "matched_topic": "none", "reason": "Irrelevant"}]'
        )
        result = filter_papers(sample_papers, sample_topics, sample_settings)
        assert len(result) == 1
        assert result[0].score == 5
        assert result[0].paper.arxiv_id == "2504.12345"

    @patch("src.filter.create_client")
    @patch("src.filter.chat_completion")
    def test_none_topic_excluded(self, mock_chat, mock_create, sample_papers, sample_topics, sample_settings):
        """Papers with matched_topic='none' should be excluded even if above threshold."""
        mock_chat.return_value = '[{"index": 0, "score": 5, "matched_topic": "none", "reason": "No match"}]'
        result = filter_papers(sample_papers, sample_topics, sample_settings)
        assert len(result) == 0

    @patch("src.filter.create_client")
    @patch("src.filter.chat_completion")
    def test_empty_input(self, mock_chat, mock_create, sample_topics, sample_settings):
        result = filter_papers([], sample_topics, sample_settings)
        assert result == []
        mock_chat.assert_not_called()

    @patch("src.filter.create_client")
    @patch("src.filter.chat_completion")
    def test_llm_error_skips_batch(self, mock_chat, mock_create, sample_papers, sample_topics, sample_settings):
        """Exceptions from LLM should be caught, not raised."""
        mock_chat.side_effect = RuntimeError("API down")
        result = filter_papers(sample_papers, sample_topics, sample_settings)
        assert result == []

    @patch("src.filter.create_client")
    @patch("src.filter.chat_completion")
    def test_batch_correctness(self, mock_chat, mock_create, sample_settings):
        """Verify correct batch slicing — 4 papers, batch_size=2 → 2 calls."""
        papers = [
            RawPaper(
                arxiv_id=f"1{i}", title=f"Paper {i}", authors=["A"],
                abstract="Abstract", published=datetime(2025, 1, 1, tzinfo=timezone.utc),
                categories=["x"], primary_category="x",
                pdf_url="", entry_url="",
            )
            for i in range(4)
        ]
        topics = [Topic(name="T", description="d", keywords=[], categories=["x"])]
        mock_chat.return_value = '[{"index": 0, "score": 4, "matched_topic": "T", "reason": "ok"}]'

        sample_settings.batch_size = 2
        filter_papers(papers, topics, sample_settings)
        assert mock_chat.call_count == 2

    @patch("src.filter.create_client")
    @patch("src.filter.chat_completion")
    def test_malformed_items_skipped(self, mock_chat, mock_create, sample_papers, sample_topics, sample_settings):
        """Malformed score items should be skipped gracefully."""
        mock_chat.return_value = (
            '[{"index": 0, "score": 4, "matched_topic": "Galactic Dynamics", "reason": "good"},'
            ' {"bad_key": 0},'
            ' {"index": 1, "score": "not_a_number", "matched_topic": "T", "reason": "bad"}]'
        )
        result = filter_papers(sample_papers, sample_topics, sample_settings)
        assert len(result) == 1

    @patch("src.filter.create_client")
    @patch("src.filter.chat_completion")
    def test_out_of_range_index_skipped(self, mock_chat, mock_create, sample_papers, sample_topics, sample_settings):
        """Index out of range for the batch should be skipped."""
        mock_chat.return_value = '[{"index": 99, "score": 5, "matched_topic": "T", "reason": "out of range"}]'
        result = filter_papers(sample_papers, sample_topics, sample_settings)
        assert len(result) == 0

    @patch("src.filter.create_client")
    @patch("src.filter.chat_completion")
    def test_sorted_by_score_descending(self, mock_chat, mock_create, sample_papers, sample_topics, sample_settings):
        """Results should be sorted by score descending."""
        mock_chat.return_value = (
            '[{"index": 0, "score": 4, "matched_topic": "Galactic Dynamics", "reason": "ok"},'
            ' {"index": 1, "score": 5, "matched_topic": "High-Energy Transients", "reason": "perfect"}]'
        )
        result = filter_papers(sample_papers, sample_topics, sample_settings)
        assert result[0].score == 5
        assert result[1].score == 4
