"""SQLite-backed user preference CRUD + learning."""

from __future__ import annotations

import logging
import sqlite3

logger = logging.getLogger(__name__)

WEIGHT_CAP = 100.0
DEFAULT_WEIGHT = 1.0


def get_preference(
    conn: sqlite3.Connection,
    tree_node_id: int,
) -> dict | None:
    """Get preference for a tree node. Returns None if not set."""
    row = conn.execute(
        """SELECT * FROM user_preferences WHERE tree_node_id = ?""",
        (tree_node_id,),
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def get_all_preferences(conn: sqlite3.Connection) -> list[dict]:
    """Get all preferences with node names."""
    rows = conn.execute(
        """SELECT up.*, kt.name as node_name
           FROM user_preferences up
           JOIN knowledge_tree kt ON up.tree_node_id = kt.id
           ORDER BY up.weight DESC""",
    ).fetchall()
    return [dict(r) for r in rows]


def ensure_preference(
    conn: sqlite3.Connection,
    tree_node_id: int,
) -> dict:
    """Get or create a preference entry for a tree node."""
    pref = get_preference(conn, tree_node_id)
    if pref is not None:
        return pref

    # Lazy init
    conn.execute(
        """INSERT INTO user_preferences (tree_node_id, weight, interaction_count)
           VALUES (?, ?, ?)
           ON CONFLICT(tree_node_id) DO NOTHING""",
        (tree_node_id, DEFAULT_WEIGHT, 0),
    )
    conn.commit()

    row = conn.execute(
        "SELECT * FROM user_preferences WHERE tree_node_id = ?",
        (tree_node_id,),
    ).fetchone()
    return dict(row)


def boost_weight(
    conn: sqlite3.Connection,
    tree_node_id: int,
    amount: float = 1.0,
) -> float:
    """Boost the preference weight for a tree node.

    Returns the new weight.
    """
    ensure_preference(conn, tree_node_id)
    new_weight = min(WEIGHT_CAP, DEFAULT_WEIGHT + amount)  # simplified
    # Actually increment from current
    conn.execute(
        """UPDATE user_preferences
           SET weight = min(weight + ?, ?),
               interaction_count = interaction_count + 1,
               updated_at = datetime('now')
           WHERE tree_node_id = ?""",
        (amount, WEIGHT_CAP, tree_node_id),
    )
    conn.commit()

    row = conn.execute(
        "SELECT weight FROM user_preferences WHERE tree_node_id = ?",
        (tree_node_id,),
    ).fetchone()
    return row["weight"]


def get_weighted_score(
    conn: sqlite3.Connection,
    arxiv_id: str,
    base_quality: int,
) -> float:
    """Compute preference-weighted score for a paper.

    sort_key = quality_score + sum(pref.weight * link.relevance_score)
    """
    links = conn.execute(
        """SELECT ptl.relevance_score, COALESCE(up.weight, 1.0) as pref_weight
           FROM paper_tree_links ptl
           LEFT JOIN user_preferences up ON ptl.tree_node_id = up.tree_node_id
           WHERE ptl.paper_id = ?""",
        (arxiv_id,),
    ).fetchall()

    bonus = sum(r["pref_weight"] * r["relevance_score"] for r in links)
    return base_quality + bonus


def initialize_all_preferences(conn: sqlite3.Connection) -> int:
    """Initialize default preferences for all tree nodes that don't have one yet.

    Returns the number of newly initialized entries.
    """
    count = 0
    rows = conn.execute(
        """SELECT kt.id FROM knowledge_tree kt
           WHERE kt.status = 'active'
           AND kt.id NOT IN (SELECT tree_node_id FROM user_preferences)""",
    ).fetchall()

    for row in rows:
        conn.execute(
            "INSERT INTO user_preferences (tree_node_id, weight, interaction_count) VALUES (?, ?, ?)",
            (row["id"], DEFAULT_WEIGHT, 0),
        )
        count += 1

    if count > 0:
        conn.commit()
    return count
