"""Tests for the Feishu card builder."""

import pytest

from src.tools.types import ScanResult, TreeLink, ReadingNote, TreeConnection
from src.bot.card_builder import (
    build_scan_result_card,
    build_reading_note_card,
    build_report_card,
    build_tree_card,
    build_prefs_card,
    build_help_card,
    _error_card,
)
from src.storage import TreeNode


class TestBuildScanResultCard:
    def test_basic_scan_card(self):
        result = ScanResult(
            arxiv_id="2504.12345",
            title="Test Paper Title",
            quality_score=4,
            quality_reason="Important result about bars",
            tree_links=[
                TreeLink(node_name="Bar Formation", relevance_score=4, relevance_reason="Directly relevant"),
            ],
            recommend_reading=True,
            rationale="This paper presents new findings",
        )
        card = build_scan_result_card(result)

        assert card["header"]["template"] == "blue"
        assert "2504.12345" not in card["header"]["title"]["content"]
        assert card["config"]["wide_screen_mode"] is True
        # Should have elements
        assert len(card["elements"]) > 0

    def test_low_quality_uses_yellow(self):
        result = ScanResult(
            arxiv_id="2504.12345",
            title="Low Quality Paper",
            quality_score=2,
        )
        card = build_scan_result_card(result)
        assert card["header"]["template"] == "yellow"

    def test_medium_quality_uses_green(self):
        result = ScanResult(
            arxiv_id="2504.12345",
            title="Medium Quality Paper",
            quality_score=3,
        )
        card = build_scan_result_card(result)
        assert card["header"]["template"] == "green"

    def test_high_quality_uses_blue(self):
        result = ScanResult(
            arxiv_id="2504.12345",
            title="High Quality Paper",
            quality_score=5,
        )
        card = build_scan_result_card(result)
        assert card["header"]["template"] == "blue"

    def test_invalid_input_returns_error(self):
        card = build_scan_result_card("not a scan result")
        assert card["header"]["template"] == "red"

    def test_card_has_action_buttons(self):
        result = ScanResult(
            arxiv_id="2504.12345",
            title="Test",
            quality_score=4,
        )
        card = build_scan_result_card(result)
        # Find action element
        actions = [e for e in card["elements"] if e.get("tag") == "action"]
        assert len(actions) > 0


class TestBuildReadingNoteCard:
    def test_basic_reading_card(self):
        note = ReadingNote(
            arxiv_id="2504.12345",
            title="Test Paper Title",
            summary="This paper studies bar formation in galaxies.",
            key_findings=["Finding 1", "Finding 2"],
            methodology="N-body simulation",
            results="Bars form within 2 Gyr",
            tree_connections=[
                TreeConnection(node_name="Bar Formation", connection="Directly about bar formation"),
            ],
            unfamiliar_concepts=["concept A", "concept B"],
            cached=False,
        )
        card = build_reading_note_card(note)

        assert card["header"]["template"] == "blue"
        assert len(card["elements"]) > 0

    def test_cached_note(self):
        note = ReadingNote(
            arxiv_id="2504.12345",
            title="Cached Paper",
            cached=True,
        )
        card = build_reading_note_card(note)
        # Should have a note element indicating cache
        notes = [e for e in card["elements"] if e.get("tag") == "note"]
        assert len(notes) > 0

    def test_invalid_input(self):
        card = build_reading_note_card("not a note")
        assert card["header"]["template"] == "red"


class TestBuildReportCard:
    def test_basic_report_card(self):
        papers = {
            "Galactic Dynamics (GA)": [
                {
                    "arxiv_id": "2504.12345",
                    "title": "Paper About Bars",
                    "quality_score": 4,
                    "quality_reason": "Important",
                    "tree_links": [{"node_name": "Bars", "relevance_score": 4}],
                    "sort_key": 5.0,
                },
            ],
        }
        card = build_report_card(papers, total_scanned=100, total_relevant=5, categories=["astro-ph.GA"])
        assert card["header"]["template"] == "blue"
        assert "Daily Report" in card["header"]["title"]["content"]

    def test_empty_report(self):
        card = build_report_card({}, total_scanned=50, total_relevant=0, categories=[])
        assert card["header"]["template"] == "blue"

    def test_report_limits_papers_per_category(self):
        papers = {
            "HE": [
                {
                    "arxiv_id": f"2504.{i:05d}",
                    "title": f"Paper {i}",
                    "quality_score": 4,
                    "quality_reason": "Relevant",
                    "tree_links": [],
                    "sort_key": 4.0 + i,
                }
                for i in range(15)  # 15 papers, should be capped at 10
            ],
        }
        card = build_report_card(papers, total_scanned=100, total_relevant=15, categories=["astro-ph.HE"])
        # Count paper divs (not hr or action elements)
        paper_divs = [
            e for e in card["elements"]
            if e.get("tag") == "div" and "Paper" in e.get("text", {}).get("content", "")
        ]
        assert len(paper_divs) == 10


class TestBuildTreeCard:
    def test_basic_tree(self):
        nodes = [
            TreeNode(id=1, name="Galactic Dynamics", description="Dynamics of galaxies",
                     parent_id=None, level=0, status="active", source="user", categories="astro-ph.GA"),
            TreeNode(id=2, name="Bar Formation", description="Formation of bars",
                     parent_id=1, level=1, status="active", source="user", categories="astro-ph.GA"),
        ]
        card = build_tree_card(nodes, {1: [nodes[1]]})
        assert card["header"]["template"] == "turquoise"
        assert len(card["elements"]) >= 2

    def test_empty_tree(self):
        card = build_tree_card([], {})
        assert "No tree nodes" in card["elements"][0]["text"]["content"]


class TestBuildPrefsCard:
    def test_with_prefs(self):
        prefs = [
            {"node_name": "Bar Formation", "weight": 3.0, "interaction_count": 5},
            {"node_name": "GRBs", "weight": 1.0, "interaction_count": 1},
        ]
        card = build_prefs_card(prefs)
        assert card["header"]["template"] == "purple"
        assert len(card["elements"]) >= 3  # explanation + hr + prefs

    def test_empty_prefs(self):
        card = build_prefs_card([])
        assert "No preferences" in card["elements"][-1]["text"]["content"]


class TestBuildHelpCard:
    def test_help_card(self):
        card = build_help_card()
        assert card["header"]["template"] == "indigo"
        assert len(card["elements"]) > 5


class TestErrorCard:
    def test_error_card(self):
        card = _error_card("Something went wrong")
        assert card["header"]["template"] == "red"
        assert "Something went wrong" in card["elements"][0]["text"]["content"]
