"""SQLite storage for papers, knowledge tree, links, and candidate nodes."""

from __future__ import annotations

import logging
import sqlite3
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path

import yaml

logger = logging.getLogger(__name__)

SCHEMA_VERSION = 5

_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS schema_version (
    version INTEGER,
    applied_at TEXT
);

CREATE TABLE IF NOT EXISTS knowledge_tree (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    name        TEXT NOT NULL,
    description TEXT NOT NULL DEFAULT '',
    parent_id   INTEGER REFERENCES knowledge_tree(id),
    level       INTEGER NOT NULL DEFAULT 0,
    status      TEXT NOT NULL DEFAULT 'active',
    source      TEXT NOT NULL DEFAULT 'user',
    categories  TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS papers (
    arxiv_id         TEXT PRIMARY KEY,
    title            TEXT NOT NULL,
    authors          TEXT NOT NULL DEFAULT '',
    abstract         TEXT NOT NULL DEFAULT '',
    published        TEXT NOT NULL,
    categories       TEXT NOT NULL DEFAULT '',
    primary_category TEXT NOT NULL DEFAULT '',
    pdf_url          TEXT NOT NULL DEFAULT '',
    entry_url        TEXT NOT NULL DEFAULT '',
    quality_score    INTEGER,
    quality_reason   TEXT NOT NULL DEFAULT '',
    first_seen_at    TEXT NOT NULL DEFAULT (datetime('now')),
    last_analyzed_at TEXT
);

CREATE TABLE IF NOT EXISTS paper_tree_links (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    paper_id         TEXT NOT NULL REFERENCES papers(arxiv_id) ON DELETE CASCADE,
    tree_node_id     INTEGER NOT NULL REFERENCES knowledge_tree(id),
    relevance_score  INTEGER NOT NULL DEFAULT 1,
    relevance_reason TEXT NOT NULL DEFAULT '',
    linked_at        TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(paper_id, tree_node_id)
);

CREATE TABLE IF NOT EXISTS candidate_nodes (
    id               INTEGER PRIMARY KEY AUTOINCREMENT,
    name             TEXT NOT NULL,
    description      TEXT NOT NULL DEFAULT '',
    parent_id        INTEGER NOT NULL REFERENCES knowledge_tree(id),
    status           TEXT NOT NULL DEFAULT 'pending',
    source_paper_ids TEXT NOT NULL DEFAULT '',
    proposed_at      TEXT NOT NULL DEFAULT (datetime('now')),
    reviewed_at      TEXT
);

CREATE INDEX IF NOT EXISTS idx_knowledge_tree_parent ON knowledge_tree(parent_id);
CREATE INDEX IF NOT EXISTS idx_papers_quality ON papers(quality_score);
CREATE INDEX IF NOT EXISTS idx_papers_first_seen ON papers(first_seen_at);
CREATE INDEX IF NOT EXISTS idx_paper_tree_links_paper ON paper_tree_links(paper_id);
CREATE INDEX IF NOT EXISTS idx_paper_tree_links_node ON paper_tree_links(tree_node_id);
CREATE INDEX IF NOT EXISTS idx_candidate_nodes_status ON candidate_nodes(status);

CREATE TABLE IF NOT EXISTS reading_notes (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    arxiv_id        TEXT NOT NULL,
    title           TEXT NOT NULL DEFAULT '',
    full_text_hash  TEXT NOT NULL DEFAULT '',
    summary         TEXT NOT NULL DEFAULT '',
    key_findings    TEXT NOT NULL DEFAULT '',
    methodology     TEXT NOT NULL DEFAULT '',
    results         TEXT NOT NULL DEFAULT '',
    tree_connections TEXT NOT NULL DEFAULT '',
    unfamiliar_concepts TEXT NOT NULL DEFAULT '',
    raw_notes       TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(arxiv_id)
);

CREATE INDEX IF NOT EXISTS idx_reading_notes_arxiv ON reading_notes(arxiv_id);

CREATE TABLE IF NOT EXISTS understanding_certificates (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    arxiv_id        TEXT NOT NULL,
    point_id        TEXT NOT NULL,
    point_type      TEXT NOT NULL DEFAULT '',
    question        TEXT NOT NULL DEFAULT '',
    claim           TEXT NOT NULL DEFAULT '',
    logic_score     INTEGER NOT NULL DEFAULT 0,
    feynman_score   INTEGER NOT NULL DEFAULT 0,
    overall_score   INTEGER NOT NULL DEFAULT 0,
    understanding_level TEXT NOT NULL DEFAULT '',
    verified        INTEGER NOT NULL DEFAULT 0,
    certificate_json TEXT NOT NULL DEFAULT '',
    full_text_hash  TEXT NOT NULL DEFAULT '',
    created_at      TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(arxiv_id, point_id, full_text_hash)
);

CREATE INDEX IF NOT EXISTS idx_understanding_certificates_arxiv ON understanding_certificates(arxiv_id);
CREATE INDEX IF NOT EXISTS idx_understanding_certificates_hash ON understanding_certificates(full_text_hash);

CREATE TABLE IF NOT EXISTS chat_sessions (
    chat_id     TEXT PRIMARY KEY,
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE TABLE IF NOT EXISTS session_messages (
    id          INTEGER PRIMARY KEY AUTOINCREMENT,
    chat_id     TEXT NOT NULL REFERENCES chat_sessions(chat_id) ON DELETE CASCADE,
    role        TEXT NOT NULL,
    content     TEXT NOT NULL,
    created_at  TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS idx_session_messages_chat ON session_messages(chat_id);

CREATE TABLE IF NOT EXISTS user_preferences (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    tree_node_id        INTEGER NOT NULL REFERENCES knowledge_tree(id),
    weight              REAL NOT NULL DEFAULT 1.0,
    interaction_count   INTEGER NOT NULL DEFAULT 0,
    updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(tree_node_id)
);

CREATE TABLE IF NOT EXISTS build_sessions (
    chat_id     TEXT PRIMARY KEY,
    stage       TEXT NOT NULL DEFAULT 'idle',
    interests   TEXT NOT NULL DEFAULT '',
    tree_yaml   TEXT NOT NULL DEFAULT '',
    created_at  TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
);
"""


# ── Data classes ───────────────────────────────────────────────────────


@dataclass
class StoredPaper:
    arxiv_id: str
    title: str
    authors: str
    abstract: str
    published: str
    categories: str
    primary_category: str
    pdf_url: str
    entry_url: str
    quality_score: int | None = None
    quality_reason: str = ""
    first_seen_at: str = ""
    last_analyzed_at: str | None = None
    is_analyzed: bool = False
    is_read: bool = False


@dataclass
class TreeNode:
    id: int
    name: str
    description: str
    parent_id: int | None
    level: int
    status: str
    source: str
    categories: str
    created_at: str = ""


@dataclass
class PaperTreeLink:
    id: int
    paper_id: str
    tree_node_id: int
    relevance_score: int
    relevance_reason: str
    linked_at: str = ""


@dataclass
class CandidateNode:
    id: int
    name: str
    description: str
    parent_id: int
    status: str
    source_paper_ids: str
    proposed_at: str = ""
    reviewed_at: str | None = None


# ── Database init ──────────────────────────────────────────────────────


def init_db(db_path: str | Path) -> sqlite3.Connection:
    """Create/open the SQLite database and apply schema.

    Returns a connection with foreign keys enabled and row factory set.
    """
    db_path = Path(db_path)
    db_path.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(str(db_path), check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON")
    conn.row_factory = sqlite3.Row
    conn.executescript(_SCHEMA_SQL)

    # Check / set schema version
    row = conn.execute("SELECT version FROM schema_version").fetchone()
    if row is None:
        conn.execute(
            "INSERT INTO schema_version (version, applied_at) VALUES (?, ?)",
            (SCHEMA_VERSION, datetime.now(timezone.utc).isoformat()),
        )
        logger.info("Database created at %s (schema v%d)", db_path, SCHEMA_VERSION)
    else:
        current_version = row["version"]
        if current_version < SCHEMA_VERSION:
            _run_migrations(conn, current_version, SCHEMA_VERSION)
        logger.info("Database opened at %s (schema v%d)", db_path, SCHEMA_VERSION)

    conn.commit()
    return conn


# ── Knowledge tree CRUD ───────────────────────────────────────────────


def insert_tree_node(
    conn: sqlite3.Connection,
    name: str,
    description: str = "",
    parent_id: int | None = None,
    level: int = 0,
    status: str = "active",
    source: str = "user",
    categories: str = "",
) -> int:
    """Insert a knowledge tree node and return its ID."""
    cur = conn.execute(
        """INSERT INTO knowledge_tree (name, description, parent_id, level, status, source, categories)
           VALUES (?, ?, ?, ?, ?, ?, ?)""",
        (name, description, parent_id, level, status, source, categories),
    )
    conn.commit()
    return cur.lastrowid


def get_tree_node_by_name(
    conn: sqlite3.Connection, name: str
) -> TreeNode | None:
    """Find an active tree node by exact name."""
    row = conn.execute(
        "SELECT * FROM knowledge_tree WHERE name = ? AND status = 'active'", (name,)
    ).fetchone()
    if row is None:
        return None
    return TreeNode(**dict(row))


def get_tree_node_by_id(
    conn: sqlite3.Connection, node_id: int
) -> TreeNode | None:
    """Find a tree node by ID."""
    row = conn.execute(
        "SELECT * FROM knowledge_tree WHERE id = ?", (node_id,)
    ).fetchone()
    if row is None:
        return None
    return TreeNode(**dict(row))


def get_all_tree_nodes(conn: sqlite3.Connection) -> list[TreeNode]:
    """Return all active tree nodes."""
    rows = conn.execute(
        "SELECT * FROM knowledge_tree WHERE status = 'active' ORDER BY id"
    ).fetchall()
    return [TreeNode(**dict(r)) for r in rows]


def get_tree_children(
    conn: sqlite3.Connection, parent_id: int | None
) -> list[TreeNode]:
    """Get child nodes of a given parent (None for roots)."""
    if parent_id is None:
        rows = conn.execute(
            "SELECT * FROM knowledge_tree WHERE parent_id IS NULL AND status = 'active' ORDER BY id"
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM knowledge_tree WHERE parent_id = ? AND status = 'active' ORDER BY id",
            (parent_id,),
        ).fetchall()
    return [TreeNode(**dict(r)) for r in rows]


def count_tree_nodes(conn: sqlite3.Connection) -> int:
    """Count active tree nodes."""
    row = conn.execute(
        "SELECT COUNT(*) FROM knowledge_tree WHERE status = 'active'"
    ).fetchone()
    return row[0]


def clear_all_tree_nodes(conn: sqlite3.Connection) -> int:
    """Soft-delete all active tree nodes by setting status='deleted'.

    Returns the number of nodes cleared.
    """
    cur = conn.execute(
        "UPDATE knowledge_tree SET status = 'deleted' WHERE status = 'active'"
    )
    conn.commit()
    return cur.rowcount


# ── Paper CRUD ─────────────────────────────────────────────────────────


def paper_exists(conn: sqlite3.Connection, arxiv_id: str) -> bool:
    """Check if a paper is already stored."""
    row = conn.execute(
        "SELECT 1 FROM papers WHERE arxiv_id = ?", (arxiv_id,)
    ).fetchone()
    return row is not None


def insert_paper(conn: sqlite3.Connection, paper: dict) -> None:
    """Insert a paper (expects dict with keys matching the papers table columns).

    Silently ignores duplicates (arxiv_id is the primary key).
    """
    try:
        conn.execute(
            """INSERT INTO papers
               (arxiv_id, title, authors, abstract, published, categories,
                primary_category, pdf_url, entry_url)
               VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)""",
            (
                paper["arxiv_id"],
                paper["title"],
                paper["authors"],
                paper["abstract"],
                paper["published"],
                paper["categories"],
                paper["primary_category"],
                paper["pdf_url"],
                paper["entry_url"],
            ),
        )
        conn.commit()
    except sqlite3.IntegrityError:
        pass  # duplicate


def insert_papers_batch(conn: sqlite3.Connection, papers: list[dict]) -> int:
    """Insert multiple papers and return the count of newly inserted ones."""
    inserted = 0
    for p in papers:
        if not paper_exists(conn, p["arxiv_id"]):
            insert_paper(conn, p)
            inserted += 1
    return inserted


def get_paper(conn: sqlite3.Connection, arxiv_id: str) -> StoredPaper | None:
    """Fetch a paper by arxiv_id."""
    row = conn.execute(
        "SELECT * FROM papers WHERE arxiv_id = ?", (arxiv_id,)
    ).fetchone()
    if row is None:
        return None
    return StoredPaper(**dict(row))


def get_unanalyzed_papers(conn: sqlite3.Connection) -> list[StoredPaper]:
    """Get papers that haven't been analyzed yet (quality_score IS NULL)."""
    rows = conn.execute(
        "SELECT * FROM papers WHERE quality_score IS NULL ORDER BY first_seen_at"
    ).fetchall()
    return [StoredPaper(**dict(r)) for r in rows]


def get_analyzed_papers(
    conn: sqlite3.Connection, min_quality: int | None = None
) -> list[StoredPaper]:
    """Get analyzed papers, optionally filtered by minimum quality score."""
    if min_quality is not None:
        rows = conn.execute(
            "SELECT * FROM papers WHERE quality_score >= ? ORDER BY quality_score DESC",
            (min_quality,),
        ).fetchall()
    else:
        rows = conn.execute(
            "SELECT * FROM papers WHERE quality_score IS NOT NULL ORDER BY quality_score DESC"
        ).fetchall()
    return [StoredPaper(**dict(r)) for r in rows]


def count_papers(conn: sqlite3.Connection) -> int:
    """Count total papers in the database."""
    row = conn.execute("SELECT COUNT(*) FROM papers").fetchone()
    return row[0]


def get_recent_papers(
    conn: sqlite3.Connection, days_back: int = 3, target_date: str | None = None
) -> list[StoredPaper]:
    """Get papers with analysis status.

    If target_date is given (YYYY-MM-DD), returns papers published on that date.
    Otherwise returns papers first seen in the last N days.
    """
    if target_date:
        rows = conn.execute(
            """SELECT p.*,
                      p.quality_score IS NOT NULL AS is_analyzed,
                      (SELECT 1 FROM reading_notes rn WHERE rn.arxiv_id = p.arxiv_id) AS is_read
               FROM papers p
               WHERE date(p.first_seen_at) = ?
               ORDER BY p.first_seen_at DESC""",
            (target_date,),
        ).fetchall()
    else:
        rows = conn.execute(
            """SELECT p.*,
                      p.quality_score IS NOT NULL AS is_analyzed,
                      (SELECT 1 FROM reading_notes rn WHERE rn.arxiv_id = p.arxiv_id) AS is_read
               FROM papers p
               WHERE p.first_seen_at >= datetime('now', ? || ' days')
               ORDER BY p.first_seen_at DESC""",
            (str(-days_back),),
        ).fetchall()
    # Convert to StoredPaper, preserving extra columns
    result = []
    for r in rows:
        sp = StoredPaper(**dict(r))
        sp.is_analyzed = bool(r["is_analyzed"])
        sp.is_read = bool(r["is_read"])
        result.append(sp)
    return result


def update_paper_analysis(
    conn: sqlite3.Connection,
    arxiv_id: str,
    quality_score: int,
    quality_reason: str = "",
) -> None:
    """Set the quality score and analysis timestamp for a paper."""
    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        """UPDATE papers SET quality_score = ?, quality_reason = ?, last_analyzed_at = ?
           WHERE arxiv_id = ?""",
        (quality_score, quality_reason, now, arxiv_id),
    )
    conn.commit()


# ── Paper-tree link CRUD ──────────────────────────────────────────────


def upsert_paper_tree_link(
    conn: sqlite3.Connection,
    paper_id: str,
    tree_node_id: int,
    relevance_score: int,
    relevance_reason: str = "",
) -> None:
    """Insert or update a paper-to-tree-node link."""
    conn.execute(
        """INSERT INTO paper_tree_links (paper_id, tree_node_id, relevance_score, relevance_reason)
           VALUES (?, ?, ?, ?)
           ON CONFLICT(paper_id, tree_node_id) DO UPDATE SET
             relevance_score = excluded.relevance_score,
             relevance_reason = excluded.relevance_reason,
             linked_at = datetime('now')""",
        (paper_id, tree_node_id, relevance_score, relevance_reason),
    )
    conn.commit()


def get_links_for_paper(
    conn: sqlite3.Connection, paper_id: str
) -> list[PaperTreeLink]:
    """Get all tree links for a paper."""
    rows = conn.execute(
        """SELECT ptl.id, ptl.paper_id, ptl.tree_node_id, ptl.relevance_score,
                  ptl.relevance_reason, ptl.linked_at, kt.name as node_name
           FROM paper_tree_links ptl
           JOIN knowledge_tree kt ON ptl.tree_node_id = kt.id
           WHERE ptl.paper_id = ?
           ORDER BY ptl.relevance_score DESC""",
        (paper_id,),
    ).fetchall()
    # Return list of dicts (not PaperTreeLink) to include node_name
    return [dict(r) for r in rows]


def get_papers_for_node(
    conn: sqlite3.Connection, tree_node_id: int
) -> list[dict]:
    """Get papers linked to a tree node with their relevance info."""
    rows = conn.execute(
        """SELECT p.*, ptl.relevance_score, ptl.relevance_reason
           FROM papers p
           JOIN paper_tree_links ptl ON p.arxiv_id = ptl.paper_id
           WHERE ptl.tree_node_id = ?
           ORDER BY ptl.relevance_score DESC""",
        (tree_node_id,),
    ).fetchall()
    return [dict(r) for r in rows]


# ── Candidate node CRUD ───────────────────────────────────────────────


def insert_candidate(
    conn: sqlite3.Connection,
    name: str,
    description: str,
    parent_id: int,
    source_paper_ids: str = "",
) -> int:
    """Insert a candidate node proposal and return its ID."""
    cur = conn.execute(
        """INSERT INTO candidate_nodes (name, description, parent_id, source_paper_ids)
           VALUES (?, ?, ?, ?)""",
        (name, description, parent_id, source_paper_ids),
    )
    conn.commit()
    return cur.lastrowid


def get_pending_candidates(conn: sqlite3.Connection) -> list[dict]:
    """Get all candidate nodes with pending status.

    Returns list of dicts with candidate fields plus 'parent_name'.
    """
    rows = conn.execute(
        """SELECT cn.id, cn.name, cn.description, cn.parent_id, cn.status,
                  cn.source_paper_ids, cn.proposed_at, cn.reviewed_at,
                  kt.name as parent_name
           FROM candidate_nodes cn
           JOIN knowledge_tree kt ON cn.parent_id = kt.id
           WHERE cn.status = 'pending'
           ORDER BY cn.id"""
    ).fetchall()
    return [dict(r) for r in rows]


def get_candidate_by_id(
    conn: sqlite3.Connection, candidate_id: int
) -> CandidateNode | None:
    """Get a candidate node by ID."""
    row = conn.execute(
        "SELECT * FROM candidate_nodes WHERE id = ?", (candidate_id,)
    ).fetchone()
    if row is None:
        return None
    return CandidateNode(**dict(row))


def confirm_candidate(
    conn: sqlite3.Connection,
    candidate_id: int,
    source: str = "llm_proposed",
) -> int | None:
    """Confirm a candidate: create a tree node and mark candidate confirmed.

    Returns the new tree node ID, or None if candidate not found.
    """
    candidate = get_candidate_by_id(conn, candidate_id)
    if candidate is None:
        return None

    parent = get_tree_node_by_id(conn, candidate.parent_id)
    new_level = (parent.level + 1) if parent else 0

    new_id = insert_tree_node(
        conn,
        name=candidate.name,
        description=candidate.description,
        parent_id=candidate.parent_id,
        level=new_level,
        source=source,
    )

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE candidate_nodes SET status = 'confirmed', reviewed_at = ? WHERE id = ?",
        (now, candidate_id),
    )
    conn.commit()
    return new_id


def reject_candidate(conn: sqlite3.Connection, candidate_id: int) -> bool:
    """Reject a candidate node. Returns True if found and updated."""
    candidate = get_candidate_by_id(conn, candidate_id)
    if candidate is None:
        return False

    now = datetime.now(timezone.utc).isoformat()
    conn.execute(
        "UPDATE candidate_nodes SET status = 'rejected', reviewed_at = ? WHERE id = ?",
        (now, candidate_id),
    )
    conn.commit()
    return True


# ── Candidate YAML I/O ────────────────────────────────────────────────


def write_candidates_yaml(
    conn: sqlite3.Connection, filepath: str | Path
) -> None:
    """Write pending candidates to a YAML file for user review.

    Each candidate includes an 'action' field (default 'pending') that the user
    can set to 'confirm' or 'reject'.
    """
    filepath = Path(filepath)
    candidates = get_pending_candidates(conn)

    if not candidates:
        # Write empty file
        filepath.parent.mkdir(parents=True, exist_ok=True)
        filepath.write_text("# No pending candidates for review.\n")
        return

    entries = []
    for c in candidates:
        paper_ids = c["source_paper_ids"].split(",") if c["source_paper_ids"] else []
        entry = {
            "id": c["id"],
            "name": c["name"],
            "description": c["description"],
            "parent": c.get("parent_name", f"node_id:{c['parent_id']}"),
            "parent_id": c["parent_id"],
            "source_papers": paper_ids,
            "proposed_at": c["proposed_at"],
            "action": "pending",  # user should change to confirm/reject
        }
        entries.append(entry)

    filepath.parent.mkdir(parents=True, exist_ok=True)
    filepath.write_text(
        "# Candidate Knowledge Tree Nodes\n"
        "# Set 'action' to 'confirm' or 'reject' for each candidate, then re-run the pipeline.\n"
        "# Leave as 'pending' to defer the decision.\n\n"
        + yaml.dump({"candidates": entries}, default_flow_style=False, allow_unicode=True)
    )


def read_candidates_yaml(filepath: str | Path) -> list[dict]:
    """Read reviewed candidates from the YAML file.

    Returns a list of candidate dicts. Skips entries with action='pending'.
    """
    filepath = Path(filepath)
    if not filepath.exists():
        return []

    with open(filepath) as f:
        data = yaml.safe_load(f)

    if not data or "candidates" not in data:
        return []

    results = []
    for c in data["candidates"]:
        action = c.get("action", "pending")
        if action in ("confirm", "reject"):
            results.append(c)
    return results


def process_candidate_review(
    conn: sqlite3.Connection, filepath: str | Path
) -> dict[str, int]:
    """Process user-reviewed candidates from the YAML file.

    Returns a dict with counts: confirmed, rejected, errors.
    """
    reviewed = read_candidates_yaml(filepath)
    stats = {"confirmed": 0, "rejected": 0, "errors": 0}

    for c in reviewed:
        action = c.get("action", "pending")
        cid = c.get("id")
        if cid is None:
            stats["errors"] += 1
            continue

        if action == "confirm":
            result = confirm_candidate(conn, cid, source="llm_proposed")
            if result is not None:
                logger.info("Confirmed candidate '%s' → tree node %d", c["name"], result)
                stats["confirmed"] += 1
            else:
                logger.warning("Candidate ID %d not found for confirmation", cid)
                stats["errors"] += 1
        elif action == "reject":
            ok = reject_candidate(conn, cid)
            if ok:
                logger.info("Rejected candidate '%s'", c["name"])
                stats["rejected"] += 1
            else:
                logger.warning("Candidate ID %d not found for rejection", cid)
                stats["errors"] += 1

    return stats


# ── Schema migrations ──────────────────────────────────────────────────


_MIGRATIONS: dict[int, str] = {
    2: """
    CREATE TABLE IF NOT EXISTS reading_notes (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        arxiv_id        TEXT NOT NULL,
        title           TEXT NOT NULL DEFAULT '',
        full_text_hash  TEXT NOT NULL DEFAULT '',
        summary         TEXT NOT NULL DEFAULT '',
        key_findings    TEXT NOT NULL DEFAULT '',
        methodology     TEXT NOT NULL DEFAULT '',
        results         TEXT NOT NULL DEFAULT '',
        tree_connections TEXT NOT NULL DEFAULT '',
        unfamiliar_concepts TEXT NOT NULL DEFAULT '',
        raw_notes       TEXT NOT NULL DEFAULT '',
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(arxiv_id)
    );
    CREATE INDEX IF NOT EXISTS idx_reading_notes_arxiv ON reading_notes(arxiv_id);
    """,
    3: """
    CREATE TABLE IF NOT EXISTS chat_sessions (
        chat_id     TEXT PRIMARY KEY,
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    );

    CREATE TABLE IF NOT EXISTS session_messages (
        id          INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id     TEXT NOT NULL REFERENCES chat_sessions(chat_id) ON DELETE CASCADE,
        role        TEXT NOT NULL,
        content     TEXT NOT NULL,
        created_at  TEXT NOT NULL DEFAULT (datetime('now'))
    );
    CREATE INDEX IF NOT EXISTS idx_session_messages_chat ON session_messages(chat_id);

    CREATE TABLE IF NOT EXISTS user_preferences (
        id                  INTEGER PRIMARY KEY AUTOINCREMENT,
        tree_node_id        INTEGER NOT NULL REFERENCES knowledge_tree(id),
        weight              REAL NOT NULL DEFAULT 1.0,
        interaction_count   INTEGER NOT NULL DEFAULT 0,
        updated_at          TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(tree_node_id)
    );
    """,
    4: """
    CREATE TABLE IF NOT EXISTS build_sessions (
        chat_id     TEXT PRIMARY KEY,
        stage       TEXT NOT NULL DEFAULT 'idle',
        interests   TEXT NOT NULL DEFAULT '',
        tree_yaml   TEXT NOT NULL DEFAULT '',
        created_at  TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at  TEXT NOT NULL DEFAULT (datetime('now'))
    );
    """,
    5: """
    CREATE TABLE IF NOT EXISTS understanding_certificates (
        id              INTEGER PRIMARY KEY AUTOINCREMENT,
        arxiv_id        TEXT NOT NULL,
        point_id        TEXT NOT NULL,
        point_type      TEXT NOT NULL DEFAULT '',
        question        TEXT NOT NULL DEFAULT '',
        claim           TEXT NOT NULL DEFAULT '',
        logic_score     INTEGER NOT NULL DEFAULT 0,
        feynman_score   INTEGER NOT NULL DEFAULT 0,
        overall_score   INTEGER NOT NULL DEFAULT 0,
        understanding_level TEXT NOT NULL DEFAULT '',
        verified        INTEGER NOT NULL DEFAULT 0,
        certificate_json TEXT NOT NULL DEFAULT '',
        full_text_hash  TEXT NOT NULL DEFAULT '',
        created_at      TEXT NOT NULL DEFAULT (datetime('now')),
        updated_at      TEXT NOT NULL DEFAULT (datetime('now')),
        UNIQUE(arxiv_id, point_id, full_text_hash)
    );
    CREATE INDEX IF NOT EXISTS idx_understanding_certificates_arxiv
    ON understanding_certificates(arxiv_id);
    CREATE INDEX IF NOT EXISTS idx_understanding_certificates_hash
    ON understanding_certificates(full_text_hash);
    """,
}


def _run_migrations(
    conn: sqlite3.Connection, from_version: int, to_version: int
) -> None:
    """Apply schema migrations sequentially from from_version to to_version."""
    for version in range(from_version + 1, to_version + 1):
        sql = _MIGRATIONS.get(version)
        if sql is None:
            logger.warning("No migration for schema v%d, skipping", version)
            continue
        logger.info("Migrating schema v%d → v%d", version - 1, version)
        conn.executescript(sql)
        conn.execute(
            "UPDATE schema_version SET version = ?, applied_at = ?",
            (version, datetime.now(timezone.utc).isoformat()),
        )
        conn.commit()
        logger.info("Migration to v%d complete", version)


# ── Reading notes CRUD ─────────────────────────────────────────────────


def get_reading_note(
    conn: sqlite3.Connection, arxiv_id: str
) -> dict | None:
    """Get a reading note by arxiv_id. Returns None if not found."""
    row = conn.execute(
        "SELECT * FROM reading_notes WHERE arxiv_id = ?", (arxiv_id,)
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def upsert_reading_note(
    conn: sqlite3.Connection,
    arxiv_id: str,
    title: str = "",
    full_text_hash: str = "",
    summary: str = "",
    key_findings: str = "",
    methodology: str = "",
    results: str = "",
    tree_connections: str = "",
    unfamiliar_concepts: str = "",
) -> None:
    """Insert or update a reading note for a paper."""
    conn.execute(
        """INSERT INTO reading_notes
           (arxiv_id, title, full_text_hash, summary, key_findings, methodology,
            results, tree_connections, unfamiliar_concepts, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(arxiv_id) DO UPDATE SET
             title = excluded.title,
             full_text_hash = excluded.full_text_hash,
             summary = excluded.summary,
             key_findings = excluded.key_findings,
             methodology = excluded.methodology,
             results = excluded.results,
             tree_connections = excluded.tree_connections,
             unfamiliar_concepts = excluded.unfamiliar_concepts,
             updated_at = datetime('now')""",
        (arxiv_id, title, full_text_hash, summary, key_findings, methodology,
         results, tree_connections, unfamiliar_concepts),
    )
    conn.commit()


def delete_reading_note(
    conn: sqlite3.Connection, arxiv_id: str
) -> bool:
    """Delete a reading note. Returns True if found and deleted."""
    cur = conn.execute(
        "DELETE FROM reading_notes WHERE arxiv_id = ?", (arxiv_id,)
    )
    conn.commit()
    return cur.rowcount > 0


# ── Build session CRUD ───────────────────────────────────────────────


def get_build_session(
    conn: sqlite3.Connection, chat_id: str
) -> dict | None:
    """Get a build session by chat_id. Returns None if not found."""
    row = conn.execute(
        "SELECT * FROM build_sessions WHERE chat_id = ?", (chat_id,)
    ).fetchone()
    if row is None:
        return None
    return dict(row)


def upsert_build_session(
    conn: sqlite3.Connection,
    chat_id: str,
    stage: str = "idle",
    interests: str = "",
    tree_yaml: str = "",
) -> None:
    """Insert or update a build session."""
    conn.execute(
        """INSERT INTO build_sessions (chat_id, stage, interests, tree_yaml, updated_at)
           VALUES (?, ?, ?, ?, datetime('now'))
           ON CONFLICT(chat_id) DO UPDATE SET
             stage = excluded.stage,
             interests = excluded.interests,
             tree_yaml = excluded.tree_yaml,
             updated_at = datetime('now')""",
        (chat_id, stage, interests, tree_yaml),
    )
    conn.commit()


def delete_build_session(
    conn: sqlite3.Connection, chat_id: str
) -> bool:
    """Delete a build session. Returns True if found and deleted."""
    cur = conn.execute(
        "DELETE FROM build_sessions WHERE chat_id = ?", (chat_id,)
    )
    conn.commit()
    return cur.rowcount > 0


# ── Understanding certificates CRUD ────────────────────────────────────


def upsert_understanding_certificate(
    conn: sqlite3.Connection,
    arxiv_id: str,
    point_id: str,
    point_type: str,
    question: str,
    claim: str,
    logic_score: int,
    feynman_score: int,
    overall_score: int,
    understanding_level: str,
    verified: bool,
    certificate_json: str,
    full_text_hash: str,
) -> None:
    """Insert or update an understanding certificate."""
    conn.execute(
        """INSERT INTO understanding_certificates
           (arxiv_id, point_id, point_type, question, claim,
            logic_score, feynman_score, overall_score, understanding_level,
            verified, certificate_json, full_text_hash, updated_at)
           VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, datetime('now'))
           ON CONFLICT(arxiv_id, point_id, full_text_hash) DO UPDATE SET
             point_type = excluded.point_type,
             question = excluded.question,
             claim = excluded.claim,
             logic_score = excluded.logic_score,
             feynman_score = excluded.feynman_score,
             overall_score = excluded.overall_score,
             understanding_level = excluded.understanding_level,
             verified = excluded.verified,
             certificate_json = excluded.certificate_json,
             updated_at = datetime('now')""",
        (arxiv_id, point_id, point_type, question, claim,
         logic_score, feynman_score, overall_score, understanding_level,
         int(verified), certificate_json, full_text_hash),
    )
    conn.commit()


def get_certificates_for_paper(
    conn: sqlite3.Connection,
    arxiv_id: str,
) -> list[dict]:
    """Get all understanding certificates for a paper."""
    rows = conn.execute(
        "SELECT * FROM understanding_certificates WHERE arxiv_id = ? ORDER BY created_at",
        (arxiv_id,),
    ).fetchall()
    return [dict(row) for row in rows]


def get_latest_certificate(
    conn: sqlite3.Connection,
    arxiv_id: str,
    point_id: str,
) -> dict | None:
    """Get the latest certificate for a specific point."""
    row = conn.execute(
        """SELECT * FROM understanding_certificates
           WHERE arxiv_id = ? AND point_id = ?
           ORDER BY created_at DESC LIMIT 1""",
        (arxiv_id, point_id),
    ).fetchone()
    return dict(row) if row else None


def has_certificates_for_paper(
    conn: sqlite3.Connection,
    arxiv_id: str,
    full_text_hash: str,
) -> bool:
    """Check if certificates exist for this paper version."""
    row = conn.execute(
        "SELECT 1 FROM understanding_certificates WHERE arxiv_id = ? AND full_text_hash = ? LIMIT 1",
        (arxiv_id, full_text_hash),
    ).fetchone()
    return row is not None
