"""Tests for src/analyze.py — AnalysisResult, parsing, analyze_papers with mocked LLM."""

from __future__ import annotations

import json
from datetime import datetime, timezone
from unittest.mock import patch, MagicMock

import pytest

from src.analyze import (
    AnalysisResult, ANALYSIS_SYSTEM_PROMPT,
    _parse_analysis_response, analyze_papers,
)
from src.collector import RawPaper


# ── Test data ─────────────────────────────────────────────────────────


def _make_paper(i: int, title: str = "Test Paper") -> RawPaper:
    return RawPaper(
        arxiv_id=f"2504.{10000 + i}",
        title=title,
        authors=[f"Author {i}"],
        abstract="A test abstract about astrophysics.",
        published=datetime(2025, 4, 20, tzinfo=timezone.utc),
        categories=["astro-ph.GA"],
        primary_category="astro-ph.GA",
        pdf_url=f"https://arxiv.org/pdf/2504.{10000 + i}",
        entry_url=f"https://arxiv.org/abs/2504.{10000 + i}",
    )


# ── _parse_analysis_response ──────────────────────────────────────────


class TestParseAnalysisResponse:
    def test_direct_json(self):
        data = {"papers": [{"index": 0, "quality_score": 3, "quality_reason": "OK",
                            "tree_links": [], "candidate_node": None}]}
        result = _parse_analysis_response(json.dumps(data))
        assert len(result) == 1
        assert result[0]["quality_score"] == 3

    def test_code_fence_json(self):
        data = {"papers": [{"index": 0, "quality_score": 4, "quality_reason": "Good",
                            "tree_links": [], "candidate_node": None}]}
        text = f"Here is my analysis:\n```json\n{json.dumps(data)}\n```"
        result = _parse_analysis_response(text)
        assert len(result) == 1
        assert result[0]["quality_score"] == 4

    def test_brace_extraction(self):
        data = {"papers": [{"index": 0, "quality_score": 2, "quality_reason": "Low",
                            "tree_links": [], "candidate_node": None}]}
        text = f"Some preamble\n{json.dumps(data)}\nSome postamble"
        result = _parse_analysis_response(text)
        assert len(result) == 1

    def test_invalid_json_returns_empty(self):
        result = _parse_analysis_response("not json at all")
        assert result == []

    def test_missing_papers_key_returns_empty(self):
        result = _parse_analysis_response(json.dumps({"other": "stuff"}))
        assert result == []

    def test_with_tree_links(self):
        data = {
            "papers": [{
                "index": 0,
                "quality_score": 4,
                "quality_reason": "High quality",
                "tree_links": [
                    {"node_name": "Bar Formation", "relevance_score": 4,
                     "relevance_reason": "Studies bars"}
                ],
                "candidate_node": None,
            }]
        }
        result = _parse_analysis_response(json.dumps(data))
        assert len(result) == 1
        assert len(result[0]["tree_links"]) == 1
        assert result[0]["tree_links"][0]["relevance_score"] == 4

    def test_with_candidate_node(self):
        data = {
            "papers": [{
                "index": 0,
                "quality_score": 4,
                "quality_reason": "Proposes new concept",
                "tree_links": [],
                "candidate_node": {
                    "name": "New Concept",
                    "description": "A new research direction",
                    "parent_node_name": "Galactic Dynamics",
                    "reason": "This paper introduces a new aspect"
                },
            }]
        }
        result = _parse_analysis_response(json.dumps(data))
        assert result[0]["candidate_node"]["name"] == "New Concept"


# ── analyze_papers ────────────────────────────────────────────────────


class TestAnalyzePapers:
    @patch("src.analyze.chat_completion")
    @patch("src.analyze.create_client")
    def test_returns_results(self, mock_create, mock_chat, sample_settings):
        mock_chat.return_value = json.dumps({
            "papers": [{
                "index": 0,
                "quality_score": 3,
                "quality_reason": "OK",
                "tree_links": [{"node_name": "Test Node", "relevance_score": 3,
                                "relevance_reason": "Related"}],
                "candidate_node": None,
            }]
        })
        mock_create.return_value = MagicMock()

        papers = [_make_paper(0)]
        results = analyze_papers(papers, "Knowledge Tree:\n1. Test Node", sample_settings)
        assert len(results) == 1
        assert results[0].quality_score == 3
        assert len(results[0].tree_links) == 1

    @patch("src.analyze.chat_completion")
    @patch("src.analyze.create_client")
    def test_empty_papers_returns_empty(self, mock_create, mock_chat, sample_settings):
        results = analyze_papers([], "tree", sample_settings)
        assert results == []

    @patch("src.analyze.chat_completion")
    @patch("src.analyze.create_client")
    def test_filters_low_relevance_links(self, mock_create, mock_chat, sample_settings):
        mock_chat.return_value = json.dumps({
            "papers": [{
                "index": 0,
                "quality_score": 3,
                "quality_reason": "OK",
                "tree_links": [
                    {"node_name": "Relevant", "relevance_score": 4, "relevance_reason": ""},
                    {"node_name": "Irrelevant", "relevance_score": 2, "relevance_reason": ""},
                ],
                "candidate_node": None,
            }]
        })
        mock_create.return_value = MagicMock()

        papers = [_make_paper(0)]
        results = analyze_papers(papers, "tree", sample_settings)
        assert len(results[0].tree_links) == 1
        assert results[0].tree_links[0]["node_name"] == "Relevant"

    @patch("src.analyze.chat_completion")
    @patch("src.analyze.create_client")
    def test_no_candidate_for_low_quality(self, mock_create, mock_chat, sample_settings):
        mock_chat.return_value = json.dumps({
            "papers": [{
                "index": 0,
                "quality_score": 2,
                "quality_reason": "Low",
                "tree_links": [],
                "candidate_node": {
                    "name": "New", "description": "d",
                    "parent_node_name": "Root", "reason": "r"
                },
            }]
        })
        mock_create.return_value = MagicMock()

        papers = [_make_paper(0)]
        results = analyze_papers(papers, "tree", sample_settings)
        assert results[0].candidate_node is None

    @patch("src.analyze.chat_completion")
    @patch("src.analyze.create_client")
    def test_llm_failure_skipped(self, mock_create, mock_chat, sample_settings):
        mock_chat.side_effect = Exception("API error")
        mock_create.return_value = MagicMock()

        papers = [_make_paper(0)]
        results = analyze_papers(papers, "tree", sample_settings)
        assert results == []

    @patch("src.analyze.chat_completion")
    @patch("src.analyze.create_client")
    def test_malformed_item_skipped(self, mock_create, mock_chat, sample_settings):
        mock_chat.return_value = json.dumps({
            "papers": [
                {"bad_key": 99},
                {"index": 0, "quality_score": 3, "quality_reason": "",
                 "tree_links": [], "candidate_node": None},
            ]
        })
        mock_create.return_value = MagicMock()

        papers = [_make_paper(0)]
        results = analyze_papers(papers, "tree", sample_settings)
        assert len(results) == 1

    @patch("src.analyze.chat_completion")
    @patch("src.analyze.create_client")
    def test_batches_correctly(self, mock_create, mock_chat, sample_settings):
        sample_settings.batch_size = 2
        # Return valid response for each call
        def make_response(*args, **kwargs):
            return json.dumps({
                "papers": [
                    {"index": 0, "quality_score": 3, "quality_reason": "",
                     "tree_links": [], "candidate_node": None},
                    {"index": 1, "quality_score": 4, "quality_reason": "",
                     "tree_links": [], "candidate_node": None},
                ]
            })
        mock_chat.side_effect = make_response
        mock_create.return_value = MagicMock()

        papers = [_make_paper(i) for i in range(4)]
        results = analyze_papers(papers, "tree", sample_settings)
        assert len(results) == 4
        assert mock_chat.call_count == 2
