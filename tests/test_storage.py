"""Tests for src/storage.py — DB init, CRUD for papers/tree/links/candidates, YAML I/O."""

from __future__ import annotations

from datetime import datetime, timezone

import pytest
import yaml

from src.storage import (
    init_db, StoredPaper, TreeNode, PaperTreeLink, CandidateNode,
    insert_tree_node, get_tree_node_by_name, get_tree_node_by_id,
    get_all_tree_nodes, get_tree_children, count_tree_nodes,
    paper_exists, insert_paper, insert_papers_batch, get_paper,
    get_unanalyzed_papers, get_analyzed_papers, count_papers,
    update_paper_analysis,
    upsert_paper_tree_link, get_links_for_paper, get_papers_for_node,
    insert_candidate, get_pending_candidates, get_candidate_by_id,
    confirm_candidate, reject_candidate,
    write_candidates_yaml, read_candidates_yaml, process_candidate_review,
)


# ── DB init ───────────────────────────────────────────────────────────


class TestInitDb:
    def test_creates_tables(self, tmp_path):
        db = tmp_path / "test.db"
        conn = init_db(db)
        tables = [r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        ).fetchall()]
        assert "papers" in tables
        assert "knowledge_tree" in tables
        assert "paper_tree_links" in tables
        assert "candidate_nodes" in tables
        assert "schema_version" in tables
        conn.close()

    def test_sets_schema_version(self, tmp_path):
        db = tmp_path / "test.db"
        conn = init_db(db)
        row = conn.execute("SELECT version FROM schema_version").fetchone()
        assert row[0] == 1
        conn.close()

    def test_in_memory_db(self):
        conn = init_db(":memory:")
        assert count_tree_nodes(conn) == 0
        conn.close()


# ── Tree node CRUD ────────────────────────────────────────────────────


class TestTreeNodeCrud:
    def test_insert_root(self, db_conn):
        nid = insert_tree_node(db_conn, "Root", "A root node")
        assert nid == 1
        assert count_tree_nodes(db_conn) == 1

    def test_insert_child(self, db_conn):
        parent_id = insert_tree_node(db_conn, "Root", "root")
        child_id = insert_tree_node(db_conn, "Child", "child",
                                     parent_id=parent_id, level=1)
        assert child_id == 2
        node = get_tree_node_by_id(db_conn, child_id)
        assert node.parent_id == parent_id
        assert node.level == 1

    def test_get_by_name(self, db_conn):
        insert_tree_node(db_conn, "TestNode", "desc")
        node = get_tree_node_by_name(db_conn, "TestNode")
        assert node is not None
        assert node.name == "TestNode"
        assert node.description == "desc"

    def test_get_by_name_not_found(self, db_conn):
        assert get_tree_node_by_name(db_conn, "NoSuch") is None

    def test_get_all_tree_nodes(self, db_conn):
        insert_tree_node(db_conn, "A", "")
        insert_tree_node(db_conn, "B", "")
        nodes = get_all_tree_nodes(db_conn)
        assert len(nodes) == 2

    def test_get_tree_children_root(self, db_conn):
        insert_tree_node(db_conn, "A", "")
        insert_tree_node(db_conn, "B", "")
        children = get_tree_children(db_conn, None)
        assert len(children) == 2

    def test_get_tree_children_of_parent(self, db_conn):
        pid = insert_tree_node(db_conn, "Parent", "")
        insert_tree_node(db_conn, "Kid1", "", parent_id=pid, level=1)
        insert_tree_node(db_conn, "Kid2", "", parent_id=pid, level=1)
        children = get_tree_children(db_conn, pid)
        assert len(children) == 2

    def test_categories_stored(self, db_conn):
        insert_tree_node(db_conn, "N", "", categories="astro-ph.GA,astro-ph.HE")
        node = get_tree_node_by_name(db_conn, "N")
        assert "astro-ph.GA" in node.categories
        assert "astro-ph.HE" in node.categories


# ── Paper CRUD ────────────────────────────────────────────────────────


SAMPLE_PAPER_DICT = {
    "arxiv_id": "2504.11111",
    "title": "Test Paper",
    "authors": "Alice\nBob",
    "abstract": "An abstract about galaxies.",
    "published": "2025-04-20T00:00:00+00:00",
    "categories": "astro-ph.GA",
    "primary_category": "astro-ph.GA",
    "pdf_url": "https://arxiv.org/pdf/2504.11111",
    "entry_url": "https://arxiv.org/abs/2504.11111",
}


class TestPaperCrud:
    def test_insert_and_get(self, db_conn):
        insert_paper(db_conn, SAMPLE_PAPER_DICT)
        paper = get_paper(db_conn, "2504.11111")
        assert paper is not None
        assert paper.title == "Test Paper"
        assert paper.quality_score is None

    def test_paper_exists(self, db_conn):
        assert not paper_exists(db_conn, "2504.11111")
        insert_paper(db_conn, SAMPLE_PAPER_DICT)
        assert paper_exists(db_conn, "2504.11111")

    def test_duplicate_insert_ignored(self, db_conn):
        insert_paper(db_conn, SAMPLE_PAPER_DICT)
        insert_paper(db_conn, SAMPLE_PAPER_DICT)
        assert count_papers(db_conn) == 1

    def test_insert_batch(self, db_conn):
        p2 = {**SAMPLE_PAPER_DICT, "arxiv_id": "2504.22222", "title": "Paper 2"}
        count = insert_papers_batch(db_conn, [SAMPLE_PAPER_DICT, p2])
        assert count == 2
        assert count_papers(db_conn) == 2

    def test_insert_batch_dedup(self, db_conn):
        count = insert_papers_batch(db_conn, [SAMPLE_PAPER_DICT, SAMPLE_PAPER_DICT])
        assert count == 1

    def test_get_unanalyzed(self, db_conn):
        insert_paper(db_conn, SAMPLE_PAPER_DICT)
        unanalyzed = get_unanalyzed_papers(db_conn)
        assert len(unanalyzed) == 1

    def test_get_unanalyzed_after_analysis(self, db_conn):
        insert_paper(db_conn, SAMPLE_PAPER_DICT)
        update_paper_analysis(db_conn, "2504.11111", 4, "Good paper")
        unanalyzed = get_unanalyzed_papers(db_conn)
        assert len(unanalyzed) == 0

    def test_update_paper_analysis(self, db_conn):
        insert_paper(db_conn, SAMPLE_PAPER_DICT)
        update_paper_analysis(db_conn, "2504.11111", 5, "Excellent")
        paper = get_paper(db_conn, "2504.11111")
        assert paper.quality_score == 5
        assert paper.quality_reason == "Excellent"
        assert paper.last_analyzed_at is not None

    def test_get_analyzed_papers(self, db_conn):
        insert_paper(db_conn, SAMPLE_PAPER_DICT)
        update_paper_analysis(db_conn, "2504.11111", 3)
        analyzed = get_analyzed_papers(db_conn)
        assert len(analyzed) == 1

    def test_get_analyzed_papers_min_quality(self, db_conn):
        insert_paper(db_conn, SAMPLE_PAPER_DICT)
        update_paper_analysis(db_conn, "2504.11111", 2)
        p2 = {**SAMPLE_PAPER_DICT, "arxiv_id": "2504.22222", "title": "Paper 2"}
        insert_paper(db_conn, p2)
        update_paper_analysis(db_conn, "2504.22222", 4)
        analyzed = get_analyzed_papers(db_conn, min_quality=3)
        assert len(analyzed) == 1
        assert analyzed[0].arxiv_id == "2504.22222"


# ── Paper-tree links ──────────────────────────────────────────────────


class TestPaperTreeLinks:
    def test_upsert_link(self, db_conn):
        nid = insert_tree_node(db_conn, "Test", "")
        insert_paper(db_conn, SAMPLE_PAPER_DICT)
        upsert_paper_tree_link(db_conn, "2504.11111", nid, 4, "Relevant")
        links = get_links_for_paper(db_conn, "2504.11111")
        assert len(links) == 1
        assert links[0]["relevance_score"] == 4

    def test_update_link(self, db_conn):
        nid = insert_tree_node(db_conn, "Test", "")
        insert_paper(db_conn, SAMPLE_PAPER_DICT)
        upsert_paper_tree_link(db_conn, "2504.11111", nid, 3, "OK")
        upsert_paper_tree_link(db_conn, "2504.11111", nid, 5, "Updated")
        links = get_links_for_paper(db_conn, "2504.11111")
        assert len(links) == 1
        assert links[0]["relevance_score"] == 5

    def test_get_papers_for_node(self, db_conn):
        nid = insert_tree_node(db_conn, "Test", "")
        insert_paper(db_conn, SAMPLE_PAPER_DICT)
        upsert_paper_tree_link(db_conn, "2504.11111", nid, 4)
        papers = get_papers_for_node(db_conn, nid)
        assert len(papers) == 1
        assert papers[0]["arxiv_id"] == "2504.11111"


# ── Candidate CRUD ────────────────────────────────────────────────────


class TestCandidateCrud:
    def test_insert_and_get_pending(self, db_conn):
        pid = insert_tree_node(db_conn, "Parent", "")
        insert_candidate(db_conn, "New Concept", "A new idea", pid, "2504.11111")
        pending = get_pending_candidates(db_conn)
        assert len(pending) == 1
        assert pending[0]["name"] == "New Concept"

    def test_confirm_candidate(self, db_conn):
        pid = insert_tree_node(db_conn, "Parent", "", level=0)
        cid = insert_candidate(db_conn, "Child", "desc", pid)
        new_id = confirm_candidate(db_conn, cid)
        assert new_id is not None
        # New node should exist in tree
        node = get_tree_node_by_id(db_conn, new_id)
        assert node.name == "Child"
        assert node.parent_id == pid
        assert node.level == 1
        # Candidate should be confirmed
        candidate = get_candidate_by_id(db_conn, cid)
        assert candidate.status == "confirmed"

    def test_reject_candidate(self, db_conn):
        pid = insert_tree_node(db_conn, "Parent", "")
        cid = insert_candidate(db_conn, "Bad", "desc", pid)
        ok = reject_candidate(db_conn, cid)
        assert ok
        candidate = get_candidate_by_id(db_conn, cid)
        assert candidate.status == "rejected"

    def test_confirm_nonexistent_returns_none(self, db_conn):
        assert confirm_candidate(db_conn, 999) is None

    def test_reject_nonexistent_returns_false(self, db_conn):
        assert reject_candidate(db_conn, 999) is False


# ── Candidate YAML I/O ────────────────────────────────────────────────


class TestCandidateYamlIO:
    def test_write_empty_candidates(self, db_conn, tmp_path):
        path = tmp_path / "candidates.yaml"
        write_candidates_yaml(db_conn, path)
        assert path.exists()
        content = path.read_text()
        assert "No pending candidates" in content

    def test_write_and_read_candidates(self, db_conn, tmp_path):
        pid = insert_tree_node(db_conn, "Parent", "")
        insert_candidate(db_conn, "Concept A", "desc A", pid, "2504.11111")
        insert_candidate(db_conn, "Concept B", "desc B", pid, "2504.22222")

        path = tmp_path / "candidates.yaml"
        write_candidates_yaml(db_conn, path)

        with open(path) as f:
            data = yaml.safe_load(f)
        assert len(data["candidates"]) == 2
        assert data["candidates"][0]["action"] == "pending"

    def test_read_candidates_with_actions(self, tmp_path):
        path = tmp_path / "candidates.yaml"
        data = {
            "candidates": [
                {"id": 1, "name": "A", "action": "confirm"},
                {"id": 2, "name": "B", "action": "pending"},
                {"id": 3, "name": "C", "action": "reject"},
            ]
        }
        path.write_text(yaml.dump(data))
        reviewed = read_candidates_yaml(path)
        assert len(reviewed) == 2  # pending skipped
        actions = {r["action"] for r in reviewed}
        assert actions == {"confirm", "reject"}

    def test_read_nonexistent_file(self, tmp_path):
        reviewed = read_candidates_yaml(tmp_path / "no_such.yaml")
        assert reviewed == []

    def test_process_candidate_review(self, db_conn, tmp_path):
        pid = insert_tree_node(db_conn, "Parent", "", level=0)
        cid = insert_candidate(db_conn, "Concept", "desc", pid)

        path = tmp_path / "candidates.yaml"
        data = {"candidates": [{"id": cid, "name": "Concept", "action": "confirm"}]}
        path.write_text(yaml.dump(data))

        stats = process_candidate_review(db_conn, path)
        assert stats["confirmed"] == 1
        assert stats["rejected"] == 0
        assert stats["errors"] == 0

    def test_process_reject(self, db_conn, tmp_path):
        pid = insert_tree_node(db_conn, "Parent", "")
        cid = insert_candidate(db_conn, "Bad", "desc", pid)

        path = tmp_path / "candidates.yaml"
        data = {"candidates": [{"id": cid, "name": "Bad", "action": "reject"}]}
        path.write_text(yaml.dump(data))

        stats = process_candidate_review(db_conn, path)
        assert stats["rejected"] == 1
