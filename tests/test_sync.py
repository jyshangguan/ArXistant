import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import arxistant_sync as sync


def _make_db(path):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS saved_papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arxiv_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            authors TEXT,
            abstract TEXT,
            relevance_score INTEGER DEFAULT 0,
            date_fetched TEXT,
            date_saved TEXT DEFAULT CURRENT_TIMESTAMP,
            notes TEXT DEFAULT ''
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS my_publications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bibcode TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            authors TEXT,
            abstract TEXT,
            keywords TEXT,
            year TEXT,
            date_added TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    conn.commit()
    sync.migrate_db(conn)
    conn.close()


def _save_paper(db_path, arxiv_id, title="Title", notes="", tags="", updated_at=None):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(
        "INSERT INTO saved_papers (arxiv_id, title, authors, abstract, relevance_score, "
        "date_fetched, notes, tags, updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (arxiv_id, title, "A Author", "Abstract", 5, "2024-01-01", notes, tags,
         updated_at or sync.now_iso()),
    )
    conn.commit()
    conn.close()


def _paper_notes(db_path, arxiv_id):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT notes FROM saved_papers WHERE arxiv_id = ?", (arxiv_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def _paper_tags(db_path, arxiv_id):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT tags FROM saved_papers WHERE arxiv_id = ?", (arxiv_id,))
    row = c.fetchone()
    conn.close()
    return row[0] if row else None


def _paper_count(db_path):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM saved_papers")
    count = c.fetchone()[0]
    conn.close()
    return count


class SnapshotMergeTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_a = os.path.join(self.tmp.name, "A", "arxiv_papers.db")
        self.db_b = os.path.join(self.tmp.name, "B", "arxiv_papers.db")
        _make_db(self.db_a)
        _make_db(self.db_b)

    def test_export_import_roundtrip(self):
        _save_paper(self.db_a, "1901.01234", notes="hello")
        snapshot = sync.export_snapshot(self.db_a)

        self.assertEqual(snapshot["schema_version"], sync.SCHEMA_VERSION)
        self.assertEqual(len(snapshot["saved_papers"]), 1)

        stats = sync.import_and_merge(snapshot, self.db_b)
        self.assertEqual(stats["saved_papers"]["added"], 1)
        self.assertEqual(_paper_notes(self.db_b, "1901.01234"), "hello")

    def test_last_write_wins_favors_newer_record(self):
        # A saves a paper, B imports it, then B edits notes later than A's edit.
        _save_paper(self.db_a, "1901.01234", notes="old")
        sync.import_and_merge(sync.export_snapshot(self.db_a), self.db_b)

        conn = sqlite3.connect(self.db_b)
        c = conn.cursor()
        c.execute("UPDATE saved_papers SET notes = ?, updated_at = ? WHERE arxiv_id = ?",
                  ("newer", sync.now_iso(), "1901.01234"))
        conn.commit()
        conn.close()

        sync.import_and_merge(sync.export_snapshot(self.db_b), self.db_a)
        self.assertEqual(_paper_notes(self.db_a, "1901.01234"), "newer")

    def test_tombstone_propagates_deletion(self):
        _save_paper(self.db_a, "1901.01234")
        sync.import_and_merge(sync.export_snapshot(self.db_a), self.db_b)

        conn = sqlite3.connect(self.db_a)
        c = conn.cursor()
        c.execute("DELETE FROM saved_papers WHERE arxiv_id = ?", ("1901.01234",))
        sync.add_tombstone(conn, "saved_papers", "1901.01234")
        conn.commit()
        conn.close()

        stats = sync.import_and_merge(sync.export_snapshot(self.db_a), self.db_b)
        self.assertEqual(stats["saved_papers"]["deleted"], 1)
        self.assertEqual(_paper_count(self.db_b), 0)

    def test_migration_adds_tags_column_to_legacy_db(self):
        # _make_db creates the pre-tags schema; migrate_db must add the column.
        self.assertEqual(_paper_tags(self.db_a, "missing"), None)
        conn = sqlite3.connect(self.db_a)
        c = conn.cursor()
        c.execute("PRAGMA table_info(saved_papers)")
        columns = {row[1] for row in c.fetchall()}
        conn.close()
        self.assertIn("tags", columns)

    def test_tags_roundtrip_through_snapshot(self):
        _save_paper(self.db_a, "1901.01234", tags="jwst,black holes")
        snapshot = sync.export_snapshot(self.db_a)
        self.assertEqual(snapshot["saved_papers"][0]["tags"], "jwst,black holes")

        stats = sync.import_and_merge(snapshot, self.db_b)
        self.assertEqual(stats["saved_papers"]["added"], 1)
        self.assertEqual(_paper_tags(self.db_b, "1901.01234"), "jwst,black holes")

    def test_tags_last_write_wins(self):
        _save_paper(self.db_a, "1901.01234", tags="old-tag")
        sync.import_and_merge(sync.export_snapshot(self.db_a), self.db_b)

        conn = sqlite3.connect(self.db_b)
        c = conn.cursor()
        c.execute("UPDATE saved_papers SET tags = ?, updated_at = ? WHERE arxiv_id = ?",
                  ("new-tag", sync.now_iso(), "1901.01234"))
        conn.commit()
        conn.close()

        sync.import_and_merge(sync.export_snapshot(self.db_b), self.db_a)
        self.assertEqual(_paper_tags(self.db_a, "1901.01234"), "new-tag")

    def test_legacy_snapshot_without_tags_imports_as_empty(self):
        _save_paper(self.db_a, "1901.01234", tags="jwst")
        snapshot = sync.export_snapshot(self.db_a)
        for rec in snapshot["saved_papers"]:
            del rec["tags"]  # simulate a snapshot from a pre-tags version

        stats = sync.import_and_merge(snapshot, self.db_b)
        self.assertEqual(stats["saved_papers"]["added"], 1)
        self.assertEqual(_paper_tags(self.db_b, "1901.01234"), "")

    def test_keyword_merge_union_with_positive_wins(self):
        pos, neg = sync.merge_keywords(
            ["galaxies", "clusters"], ["cmb"],
            ["clusters", "xray"], ["galaxies", "dark-matter"],
        )
        self.assertEqual(pos, ["clusters", "galaxies", "xray"])
        self.assertEqual(neg, ["cmb", "dark-matter"])


class LocalFolderProviderTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "db", "arxiv_papers.db")
        _make_db(self.db)
        self.shared = os.path.join(self.tmp.name, "shared")

    def _enable(self):
        cfg = sync.load_config(self.db)
        cfg["enabled"] = True
        cfg["provider"] = "local_folder"
        cfg["local_folder"]["path"] = self.shared
        sync.save_config(cfg, self.db)

    def test_run_sync_writes_snapshot_file(self):
        self._enable()
        _save_paper(self.db, "1901.01234", notes="synced")

        result = sync.run_sync(self.db)
        self.assertTrue(result["success"], result)

        snapshot_path = os.path.join(self.shared, sync.SYNC_FILENAME)
        self.assertTrue(os.path.exists(snapshot_path))

    def test_run_sync_disabled_returns_error(self):
        result = sync.run_sync(self.db)
        self.assertFalse(result["success"])
        self.assertIn("disabled", result["error"])


class ConfigSecretsTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db = os.path.join(self.tmp.name, "db", "arxiv_papers.db")
        _make_db(self.db)

    def test_secrets_are_never_persisted_to_config(self):
        cfg = sync.load_config(self.db)
        cfg["webdav"]["password"] = "pw"
        sync.save_config(cfg, self.db)

        reloaded = sync.load_config(self.db)
        self.assertNotIn("password", reloaded["webdav"])


if __name__ == "__main__":
    unittest.main()
