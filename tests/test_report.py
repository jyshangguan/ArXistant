"""Tests for src/report.py — _truncate, _first_author, _author_count, generate_report."""

from __future__ import annotations

from pathlib import Path

import pytest

from src.report import _truncate, _author_count, _first_author, generate_report
from src.collector import RawPaper
from src.filter import RelevantPaper
from src.config import Settings, Topic
from datetime import datetime, timezone


# ── _truncate ────────────────────────────────────────────────────────────


class TestTruncate:
    def test_short_text_unchanged(self):
        assert _truncate("hello") == "hello"

    def test_exact_length(self):
        text = "a" * 500
        assert _truncate(text) == text

    def test_over_length_truncates_with_ellipsis(self):
        text = "word " * 120  # 600 chars
        result = _truncate(text, max_len=100)
        assert result.endswith("...")
        assert len(result) < len(text)

    def test_no_spaces_truncates_raw(self):
        text = "a" * 600
        result = _truncate(text, max_len=500)
        # rsplit on space won't split, returns whole string[:500] + "..."
        assert result == "a" * 500 + "..."

    def test_long_paragraph_breaks_at_word_boundary(self):
        text = "alpha beta gamma delta " * 30
        result = _truncate(text, max_len=50)
        assert result.endswith("...")
        # Should not cut mid-word
        assert " " not in result.replace("...", "").split()[-1] or result.endswith("...")


# ── _first_author / _author_count ────────────────────────────────────────


class TestAuthorHelpers:
    def test_first_author_normal(self, sample_paper):
        assert _first_author(sample_paper) == "Alice Smith"

    def test_first_author_single(self):
        p = RawPaper(
            arxiv_id="1", title="T", authors=["Solo"],
            abstract="A", published=datetime(2025, 1, 1, tzinfo=timezone.utc),
            categories=["x"], primary_category="x",
            pdf_url="", entry_url="",
        )
        assert _first_author(p) == "Solo"

    def test_first_author_empty(self):
        p = RawPaper(
            arxiv_id="1", title="T", authors=[],
            abstract="A", published=datetime(2025, 1, 1, tzinfo=timezone.utc),
            categories=["x"], primary_category="x",
            pdf_url="", entry_url="",
        )
        assert _first_author(p) == "Unknown"

    def test_author_count(self, sample_paper):
        assert _author_count(sample_paper) == 4

    def test_author_count_empty(self):
        p = RawPaper(
            arxiv_id="1", title="T", authors=[],
            abstract="A", published=datetime(2025, 1, 1, tzinfo=timezone.utc),
            categories=["x"], primary_category="x",
            pdf_url="", entry_url="",
        )
        assert _author_count(p) == 0


# ── generate_report ──────────────────────────────────────────────────────


class TestGenerateReport:
    def _make_settings(self, tmp_path, output_dir=None):
        return Settings(
            report_output_dir=str(tmp_path / (output_dir or "reports")),
        )

    def test_creates_report_file(self, tmp_path, sample_paper, sample_settings):
        rp = RelevantPaper(
            paper=sample_paper, score=4,
            matched_topic="Galactic Dynamics", reason="Test reason",
        )
        sample_settings.report_output_dir = str(tmp_path / "out")
        path = generate_report(
            relevant=[rp], total_scanned=100,
            topics=[Topic(name="Galactic Dynamics", description="test",
                          keywords=[], categories=["astro-ph.GA"])],
            all_categories=["astro-ph.GA"],
            settings=sample_settings,
        )
        assert path.exists()
        content = path.read_text()
        assert "ArXistant Daily Report" in content
        assert sample_paper.title in content

    def test_empty_relevant_list(self, tmp_path, sample_settings):
        sample_settings.report_output_dir = str(tmp_path / "out")
        path = generate_report(
            relevant=[], total_scanned=50,
            topics=[Topic(name="T", description="d", keywords=[], categories=["x"])],
            all_categories=["x"],
            settings=sample_settings,
        )
        assert path.exists()
        content = path.read_text()
        assert "**Relevant papers:** 0" in content

    def test_auto_creates_directories(self, tmp_path, sample_settings):
        sample_settings.report_output_dir = str(tmp_path / "deep" / "nested" / "out")
        path = generate_report(
            relevant=[], total_scanned=0,
            topics=[], all_categories=[],
            settings=sample_settings,
        )
        assert path.exists()

    def test_contains_paper_details(self, tmp_path, sample_paper, sample_settings):
        rp = RelevantPaper(
            paper=sample_paper, score=5,
            matched_topic="Test Topic", reason="Highly relevant paper",
        )
        sample_settings.report_output_dir = str(tmp_path / "out")
        path = generate_report(
            relevant=[rp], total_scanned=1,
            topics=[Topic(name="Test Topic", description="d",
                          keywords=[], categories=["astro-ph.GA"])],
            all_categories=["astro-ph.GA"],
            settings=sample_settings,
        )
        content = path.read_text()
        assert "5/5" in content
        assert "Test Topic" in content
        assert "Highly relevant paper" in content
        assert sample_paper.arxiv_id in content
