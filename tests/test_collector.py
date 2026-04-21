"""Tests for src/collector.py — _parse_entry, collect_papers."""

from __future__ import annotations

from datetime import datetime, timezone
from unittest.mock import MagicMock, patch

import pytest

from src.collector import RawPaper, _parse_entry, collect_papers
from src.config import Settings, Topic


# ── _parse_entry ─────────────────────────────────────────────────────────


class TestParseEntry:
    def test_basic_parsing(self):
        entry = MagicMock()
        entry.entry_id = "https://arxiv.org/abs/2504.12345"
        entry.title = "  A Study of Galaxies  "
        mock_a1 = MagicMock()
        mock_a1.name = "Alice"
        mock_a2 = MagicMock()
        mock_a2.name = "Bob"
        entry.authors = [mock_a1, mock_a2]
        entry.summary = "  We study galaxies.  "
        entry.published = datetime(2025, 4, 20, tzinfo=timezone.utc)
        entry.categories = ["astro-ph.GA", "astro-ph.CO"]
        entry.primary_category = "astro-ph.GA"
        entry.pdf_url = "https://arxiv.org/pdf/2504.12345"
        entry.entry_id = "https://arxiv.org/abs/2504.12345"

        paper = _parse_entry(entry)
        assert paper.arxiv_id == "2504.12345"
        assert paper.title == "A Study of Galaxies"
        assert paper.authors == ["Alice", "Bob"]
        assert paper.abstract == "We study galaxies."
        assert paper.categories == ["astro-ph.GA", "astro-ph.CO"]
        assert paper.primary_category == "astro-ph.GA"

    def test_arxiv_id_extraction(self):
        entry = MagicMock()
        entry.entry_id = "https://arxiv.org/abs/2401.99999"
        entry.title = "T"
        entry.authors = []
        entry.summary = "S"
        entry.published = datetime(2025, 1, 1, tzinfo=timezone.utc)
        entry.categories = ["x"]
        entry.primary_category = "x"
        entry.pdf_url = ""

        paper = _parse_entry(entry)
        assert paper.arxiv_id == "2401.99999"


# ── collect_papers ───────────────────────────────────────────────────────


class TestCollectPapers:
    @patch("src.collector.arxiv")
    def test_basic_fetch(self, mock_arxiv, sample_topics, sample_settings):
        mock_entry = MagicMock()
        mock_entry.published = datetime.now(timezone.utc)
        mock_entry.entry_id = "https://arxiv.org/abs/2504.10001"
        mock_entry.title = "Paper 1"
        mock_entry.authors = [MagicMock(name="Author1")]
        mock_entry.summary = "Abstract 1"
        mock_entry.categories = ["astro-ph.GA"]
        mock_entry.primary_category = "astro-ph.GA"
        mock_entry.pdf_url = "https://arxiv.org/pdf/2504.10001"

        mock_arxiv.SortCriterion = MagicMock()
        mock_arxiv.SortCriterion.SubmittedDate = "SubmittedDate"
        mock_arxiv.SortOrder = MagicMock()
        mock_arxiv.SortOrder.Descending = "Descending"

        mock_client = MagicMock()
        mock_client.results.return_value = [mock_entry]
        mock_arxiv.Client.return_value = mock_client
        mock_arxiv.Search = MagicMock(return_value=MagicMock())

        papers = collect_papers(sample_topics, sample_settings)
        assert len(papers) >= 1
        assert papers[0].arxiv_id == "2504.10001"

    @patch("src.collector.arxiv")
    def test_deduplication(self, mock_arxiv, sample_settings):
        topics = [Topic(name="T", description="d", keywords=[], categories=["astro-ph.GA"])]
        mock_entry = MagicMock()
        mock_entry.published = datetime.now(timezone.utc)
        mock_entry.entry_id = "https://arxiv.org/abs/2504.10001"
        mock_entry.title = "Paper 1"
        mock_entry.authors = [MagicMock(name="Author1")]
        mock_entry.summary = "Abstract 1"
        mock_entry.categories = ["astro-ph.GA"]
        mock_entry.primary_category = "astro-ph.GA"
        mock_entry.pdf_url = "https://arxiv.org/pdf/2504.10001"

        mock_arxiv.SortCriterion = MagicMock()
        mock_arxiv.SortCriterion.SubmittedDate = "SubmittedDate"
        mock_arxiv.SortOrder = MagicMock()
        mock_arxiv.SortOrder.Descending = "Descending"

        mock_client = MagicMock()
        # Same entry returned twice (same paper in different categories)
        mock_client.results.return_value = [mock_entry, mock_entry]
        mock_arxiv.Client.return_value = mock_client
        mock_arxiv.Search = MagicMock(return_value=MagicMock())

        papers = collect_papers(topics, sample_settings)
        # Should only have one unique paper
        assert len(papers) == 1

    @patch("src.collector.arxiv")
    def test_date_filtering(self, mock_arxiv, sample_settings):
        topics = [Topic(name="T", description="d", keywords=[], categories=["astro-ph.GA"])]

        old_entry = MagicMock()
        old_entry.published = datetime(2020, 1, 1, tzinfo=timezone.utc)
        old_entry.entry_id = "https://arxiv.org/abs/2001.00001"
        old_entry.title = "Old Paper"
        old_entry.authors = [MagicMock(name="A")]
        old_entry.summary = "Old"
        old_entry.categories = ["astro-ph.GA"]
        old_entry.primary_category = "astro-ph.GA"
        old_entry.pdf_url = ""

        recent_entry = MagicMock()
        recent_entry.published = datetime.now(timezone.utc)
        recent_entry.entry_id = "https://arxiv.org/abs/2504.20001"
        recent_entry.title = "Recent Paper"
        recent_entry.authors = [MagicMock(name="B")]
        recent_entry.summary = "Recent"
        recent_entry.categories = ["astro-ph.GA"]
        recent_entry.primary_category = "astro-ph.GA"
        recent_entry.pdf_url = ""

        mock_arxiv.SortCriterion = MagicMock()
        mock_arxiv.SortCriterion.SubmittedDate = "SubmittedDate"
        mock_arxiv.SortOrder = MagicMock()
        mock_arxiv.SortOrder.Descending = "Descending"

        mock_client = MagicMock()
        mock_client.results.return_value = [recent_entry, old_entry]
        mock_arxiv.Client.return_value = mock_client
        mock_arxiv.Search = MagicMock(return_value=MagicMock())

        papers = collect_papers(topics, sample_settings)
        # Old paper should be excluded by date filter
        assert all(p.arxiv_id == "2504.20001" for p in papers)

    @patch("src.collector.arxiv")
    def test_api_error_handling(self, mock_arxiv, sample_topics, sample_settings):
        mock_arxiv.SortCriterion = MagicMock()
        mock_arxiv.SortCriterion.SubmittedDate = "SubmittedDate"
        mock_arxiv.SortOrder = MagicMock()
        mock_arxiv.SortOrder.Descending = "Descending"
        mock_arxiv.Client.side_effect = Exception("API error")

        papers = collect_papers(sample_topics, sample_settings)
        assert papers == []
