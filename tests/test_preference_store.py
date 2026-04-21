"""Tests for the preference store."""

import pytest

from src.storage import init_db, insert_tree_node
from src.bot.preference_store import (
    get_preference,
    get_all_preferences,
    ensure_preference,
    boost_weight,
    get_weighted_score,
    initialize_all_preferences,
    WEIGHT_CAP,
    DEFAULT_WEIGHT,
)


@pytest.fixture
def db_conn_with_tree():
    conn = init_db(":memory:")
    ga_id = insert_tree_node(conn, "Galactic Dynamics",
                             "Dynamics and structure of galaxies.",
                             categories="astro-ph.GA")
    bar_id = insert_tree_node(conn, "Bar Formation",
                              "Formation and dynamics of galactic bars.",
                              parent_id=ga_id, level=1, categories="astro-ph.GA")
    he_id = insert_tree_node(conn, "High-Energy Astrophysical Transients",
                             "GRBs, supernovae, TDEs, FRBs.",
                             categories="astro-ph.HE")
    yield conn, ga_id, bar_id, he_id
    conn.close()


class TestGetPreference:
    def test_none_for_missing(self, db_conn_with_tree):
        conn, _, _, _ = db_conn_with_tree
        assert get_preference(conn, 9999) is None


class TestEnsurePreference:
    def test_creates_default(self, db_conn_with_tree):
        conn, _, bar_id, _ = db_conn_with_tree
        pref = ensure_preference(conn, bar_id)
        assert pref["weight"] == DEFAULT_WEIGHT
        assert pref["interaction_count"] == 0

    def test_idempotent(self, db_conn_with_tree):
        conn, _, bar_id, _ = db_conn_with_tree
        ensure_preference(conn, bar_id)
        ensure_preference(conn, bar_id)
        row = conn.execute(
            "SELECT COUNT(*) FROM user_preferences WHERE tree_node_id = ?",
            (bar_id,),
        ).fetchone()
        assert row[0] == 1


class TestBoostWeight:
    def test_boost_increases_weight(self, db_conn_with_tree):
        conn, _, bar_id, _ = db_conn_with_tree
        new_weight = boost_weight(conn, bar_id, amount=1.0)
        assert new_weight == DEFAULT_WEIGHT + 1.0

    def test_boost_increases_interaction_count(self, db_conn_with_tree):
        conn, _, bar_id, _ = db_conn_with_tree
        boost_weight(conn, bar_id, amount=1.0)
        boost_weight(conn, bar_id, amount=2.0)
        pref = get_preference(conn, bar_id)
        assert pref["interaction_count"] == 2

    def test_weight_capped(self, db_conn_with_tree):
        conn, _, bar_id, _ = db_conn_with_tree
        # Set to near cap
        ensure_preference(conn, bar_id)
        conn.execute(
            "UPDATE user_preferences SET weight = ? WHERE tree_node_id = ?",
            (WEIGHT_CAP - 0.5, bar_id),
        )
        conn.commit()

        new_weight = boost_weight(conn, bar_id, amount=10.0)
        assert new_weight <= WEIGHT_CAP


class TestGetAllPreferences:
    def test_returns_empty_initially(self, db_conn_with_tree):
        conn, _, _, _ = db_conn_with_tree
        prefs = get_all_preferences(conn)
        assert len(prefs) == 0

    def test_returns_prefs_with_names(self, db_conn_with_tree):
        conn, _, bar_id, _ = db_conn_with_tree
        ensure_preference(conn, bar_id)
        prefs = get_all_preferences(conn)
        assert len(prefs) == 1
        assert prefs[0]["node_name"] == "Bar Formation"

    def test_sorted_by_weight_desc(self, db_conn_with_tree):
        conn, _, bar_id, he_id = db_conn_with_tree
        ensure_preference(conn, bar_id)
        boost_weight(conn, bar_id, amount=3.0)
        ensure_preference(conn, he_id)
        boost_weight(conn, he_id, amount=1.0)

        prefs = get_all_preferences(conn)
        assert prefs[0]["node_name"] == "Bar Formation"  # weight 4.0 > 2.0


class TestInitializeAllPreferences:
    def test_inits_all_nodes(self, db_conn_with_tree):
        conn, _, _, _ = db_conn_with_tree
        count = initialize_all_preferences(conn)
        assert count == 3  # 3 nodes in fixture

    def test_idempotent(self, db_conn_with_tree):
        conn, _, _, _ = db_conn_with_tree
        initialize_all_preferences(conn)
        count = initialize_all_preferences(conn)
        assert count == 0


class TestGetWeightedScore:
    def test_no_links_returns_base_quality(self, db_conn_with_tree):
        conn, _, _, _ = db_conn_with_tree
        score = get_weighted_score(conn, "nonexistent", 4)
        assert score == 4.0

    def test_with_links_and_preferences(self, db_conn_with_tree):
        conn, _, bar_id, _ = db_conn_with_tree

        # Insert a paper
        conn.execute(
            """INSERT INTO papers (arxiv_id, title, authors, abstract, published,
               categories, primary_category, pdf_url, entry_url, quality_score)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            ("2504.12345", "Test", "Author", "Abstract", "2025-04-20",
             "astro-ph.GA", "astro-ph.GA", "", "", 4),
        )

        # Add link with high preference weight
        ensure_preference(conn, bar_id)
        boost_weight(conn, bar_id, amount=5.0)
        conn.execute(
            """INSERT INTO paper_tree_links (paper_id, tree_node_id, relevance_score)
               VALUES (?, ?, ?)""",
            ("2504.12345", bar_id, 4),
        )
        conn.commit()

        score = get_weighted_score(conn, "2504.12345", 4)
        # Should be 4 + 6.0 * 4 = 28.0
        assert score > 4.0
