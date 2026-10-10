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


def _make_notebook_db(path):
    """A database with the pre-notebook-sync (0.4.6) notebook tables.

    Creating them without sync_key/updated_at exercises the migration that
    real upgrades go through.
    """
    _make_db(path)
    conn = sqlite3.connect(path)
    c = conn.cursor()
    c.execute('''
        CREATE TABLE IF NOT EXISTS notebook_folders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    c.execute('''
        CREATE TABLE IF NOT EXISTS notebook_notes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            folder_id INTEGER NOT NULL,
            paper_id TEXT NOT NULL,
            title TEXT DEFAULT '',
            authors TEXT DEFAULT '',
            url TEXT DEFAULT '',
            note TEXT DEFAULT '',
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            UNIQUE (folder_id, paper_id)
        )
    ''')
    conn.commit()
    sync.migrate_db(conn)
    conn.close()


def _nb_folder(db_path, name, key=None, updated="2026-10-01T00:00:00+00:00"):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute(
        "INSERT INTO notebook_folders (name, created_at, sync_key, updated_at) "
        "VALUES (?, ?, ?, ?)",
        (name, updated, key or sync.new_sync_key(), updated))
    conn.commit()
    folder_id = c.lastrowid
    conn.close()
    return folder_id


def _nb_folder_id(db_path, name):
    conn = sqlite3.connect(db_path)
    row = conn.execute("SELECT id FROM notebook_folders WHERE name = ?",
                       (name,)).fetchone()
    conn.close()
    return row[0] if row else None


def _nb_note(db_path, folder_id, paper_id, note="", title="A paper",
             updated="2026-10-01T00:00:00+00:00"):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT sync_key FROM notebook_folders WHERE id = ?",
              (folder_id,))
    folder_key = c.fetchone()[0]
    c.execute(
        "INSERT OR REPLACE INTO notebook_notes "
        "(folder_id, paper_id, title, authors, url, note, created_at, "
        " sync_key, updated_at) VALUES (?, ?, ?, '', '', ?, ?, ?, ?)",
        (folder_id, paper_id, title, note, updated,
         sync.notebook_note_sync_key(folder_key, paper_id), updated))
    conn.commit()
    conn.close()


def _nb_delete_folder(db_path, folder_id):
    """Delete a folder + its notes with tombstones, like the API handler."""
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT sync_key FROM notebook_folders WHERE id = ?",
              (folder_id,))
    folder_key = c.fetchone()[0]
    c.execute("SELECT sync_key FROM notebook_notes WHERE folder_id = ?",
              (folder_id,))
    note_keys = [r[0] for r in c.fetchall()]
    c.execute("DELETE FROM notebook_notes WHERE folder_id = ?", (folder_id,))
    c.execute("DELETE FROM notebook_folders WHERE id = ?", (folder_id,))
    sync.add_tombstone(conn, "notebook_folders", folder_key)
    for note_key in note_keys:
        sync.add_tombstone(conn, "notebook_notes", note_key)
    conn.commit()
    conn.close()


def _nb_delete_note(db_path, paper_id):
    conn = sqlite3.connect(db_path)
    c = conn.cursor()
    c.execute("SELECT sync_key FROM notebook_notes WHERE paper_id = ?",
              (paper_id,))
    note_key = c.fetchone()[0]
    c.execute("DELETE FROM notebook_notes WHERE paper_id = ?", (paper_id,))
    sync.add_tombstone(conn, "notebook_notes", note_key)
    conn.commit()
    conn.close()


def _nb_folders(db_path):
    conn = sqlite3.connect(db_path)
    rows = conn.execute("SELECT name FROM notebook_folders ORDER BY id").fetchall()
    conn.close()
    return [r[0] for r in rows]


def _nb_notes(db_path):
    """(folder name, paper_id, note) triples across all folders."""
    conn = sqlite3.connect(db_path)
    rows = conn.execute(
        "SELECT f.name, n.paper_id, n.note FROM notebook_notes n "
        "JOIN notebook_folders f ON f.id = n.folder_id "
        "ORDER BY n.paper_id").fetchall()
    conn.close()
    return rows


class NotebookSyncTests(unittest.TestCase):
    """Notebook folders and notes sync across devices (issue #14).

    Folders merge last-write-wins on a stable sync key (renames keep their
    identity), notes on (folder key, paper id) so the same paper filed on
    two devices is one note.
    """

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.db_a = os.path.join(self.tmp.name, "A", "arxiv_papers.db")
        self.db_b = os.path.join(self.tmp.name, "B", "arxiv_papers.db")
        _make_notebook_db(self.db_a)
        _make_notebook_db(self.db_b)

    def _sync_a_to_b(self):
        return sync.import_and_merge(sync.export_snapshot(self.db_a), self.db_b)

    def test_folders_and_notes_roundtrip(self):
        folder = _nb_folder(self.db_a, "Reading queue")
        _nb_note(self.db_a, folder, "2501.00001", note="start with §2")
        _nb_note(self.db_a, folder, "2501.00002")

        stats = self._sync_a_to_b()
        self.assertEqual(stats["notebook_folders"]["added"], 1)
        self.assertEqual(stats["notebook_notes"]["added"], 2)
        self.assertEqual(_nb_folders(self.db_b), ["Reading queue"])
        self.assertEqual(_nb_notes(self.db_b),
                         [("Reading queue", "2501.00001", "start with §2"),
                          ("Reading queue", "2501.00002", "")])

    def test_second_sync_is_a_noop(self):
        folder = _nb_folder(self.db_a, "Reading queue")
        _nb_note(self.db_a, folder, "2501.00001")
        self._sync_a_to_b()
        stats = self._sync_a_to_b()
        self.assertEqual(stats["notebook_folders"]["added"], 0)
        self.assertEqual(stats["notebook_folders"]["updated"], 0)
        self.assertEqual(stats["notebook_notes"]["added"], 0)
        self.assertEqual(stats["notebook_notes"]["updated"], 0)

    def test_rename_propagates_without_duplicating(self):
        folder = _nb_folder(self.db_a, "Old name")
        _nb_note(self.db_a, folder, "2501.00001", note="kept")
        self._sync_a_to_b()
        self.assertEqual(_nb_folders(self.db_b), ["Old name"])

        conn = sqlite3.connect(self.db_a)
        conn.execute(
            "UPDATE notebook_folders SET name = 'New name', updated_at = ? "
            "WHERE id = ?", (sync.now_iso(), folder))
        conn.commit()
        conn.close()

        stats = self._sync_a_to_b()
        self.assertEqual(stats["notebook_folders"]["updated"], 1)
        self.assertEqual(_nb_folders(self.db_b), ["New name"])
        self.assertEqual(_nb_notes(self.db_b),
                         [("New name", "2501.00001", "kept")])

    def test_folder_delete_propagates_with_its_notes(self):
        doomed = _nb_folder(self.db_a, "Doomed")
        _nb_note(self.db_a, doomed, "2501.00001", note="goes too")
        keeper = _nb_folder(self.db_a, "Keeper")
        _nb_note(self.db_a, keeper, "2501.00002", note="stays")
        self._sync_a_to_b()
        self.assertEqual(len(_nb_folders(self.db_b)), 2)

        _nb_delete_folder(self.db_a, doomed)
        stats = self._sync_a_to_b()
        self.assertEqual(stats["notebook_folders"]["deleted"], 1)
        self.assertEqual(_nb_folders(self.db_b), ["Keeper"])
        self.assertEqual(_nb_notes(self.db_b),
                         [("Keeper", "2501.00002", "stays")])

    def test_note_delete_propagates(self):
        folder = _nb_folder(self.db_a, "F")
        _nb_note(self.db_a, folder, "2501.00001", note="bye")
        _nb_note(self.db_a, folder, "2501.00002", note="stay")
        self._sync_a_to_b()
        _nb_delete_note(self.db_a, "2501.00001")
        stats = self._sync_a_to_b()
        self.assertEqual(stats["notebook_notes"]["deleted"], 1)
        self.assertEqual(_nb_notes(self.db_b),
                         [("F", "2501.00002", "stay")])

    def test_note_edit_last_write_wins(self):
        folder = _nb_folder(self.db_a, "F", updated="2026-10-01T00:00:00+00:00")
        _nb_note(self.db_a, folder, "2501.00001", note="original",
                 updated="2026-10-01T00:00:00+00:00")
        self._sync_a_to_b()

        folder_b = _nb_folder_id(self.db_b, "F")
        _nb_note(self.db_a, folder, "2501.00001", note="from A",
                 updated="2026-10-02T00:00:00+00:00")
        _nb_note(self.db_b, folder_b, "2501.00001", note="from B",
                 updated="2026-10-03T00:00:00+00:00")

        self._sync_a_to_b()
        self.assertEqual(_nb_notes(self.db_b),
                         [("F", "2501.00001", "from B")])
        # Syncing back converges A onto B's newer text.
        sync.import_and_merge(sync.export_snapshot(self.db_b), self.db_a)
        self.assertEqual(_nb_notes(self.db_a),
                         [("F", "2501.00001", "from B")])

    def test_same_paper_filed_on_both_devices_is_one_note(self):
        # The note identity is (folder key, paper id): once B knows the
        # folder, filing the same paper there lands on the same key, so the
        # merge updates the one note instead of adding a second.
        folder = _nb_folder(self.db_a, "To read")
        _nb_note(self.db_a, folder, "2501.00001", note="from A")
        self._sync_a_to_b()

        folder_b = _nb_folder_id(self.db_b, "To read")
        _nb_note(self.db_b, folder_b, "2501.00001", note="from B",
                 updated="2026-10-03T00:00:00+00:00")

        stats = self._sync_a_to_b()
        # B's edit is newer, so importing A's older snapshot changes nothing
        # on B (no duplicate, no rollback)…
        self.assertEqual(stats["notebook_notes"]["added"], 0)
        self.assertEqual(stats["notebook_notes"]["updated"], 0)
        self.assertEqual(_nb_notes(self.db_b),
                         [("To read", "2501.00001", "from B")])
        # …and the next sync back converges A onto B's text.
        sync.import_and_merge(sync.export_snapshot(self.db_b), self.db_a)
        self.assertEqual(_nb_notes(self.db_a),
                         [("To read", "2501.00001", "from B")])

    def test_same_named_folders_merge_via_adoption(self):
        # The seeded "To read" exists on both devices with different keys
        # (one upgraded from 0.4.6, one fresh). Importing A into B must not
        # duplicate the folder or lose either side's notes.
        folder_a = _nb_folder(self.db_a, "To read", key="seed-to-read")
        _nb_note(self.db_a, folder_a, "2501.00001", note="on A")
        folder_b = _nb_folder(self.db_b, "To read", key="old-backfill-key")
        _nb_note(self.db_b, folder_b, "2501.00002", note="on B")

        stats = self._sync_a_to_b()
        self.assertEqual(stats["notebook_folders"]["added"], 0)
        self.assertEqual(_nb_folders(self.db_b), ["To read"])
        self.assertEqual(sorted(_nb_notes(self.db_b)),
                         [("To read", "2501.00001", "on A"),
                          ("To read", "2501.00002", "on B")])

    def test_identical_seed_keys_merge_cleanly(self):
        folder_a = _nb_folder(self.db_a, "To read", key="seed-to-read",
                              updated="2026-10-02T00:00:00+00:00")
        _nb_note(self.db_a, folder_a, "2501.00001")
        folder_b = _nb_folder(self.db_b, "To read", key="seed-to-read",
                              updated="2026-10-01T00:00:00+00:00")
        _nb_note(self.db_b, folder_b, "2501.00002")

        stats = self._sync_a_to_b()
        self.assertEqual(stats["notebook_folders"]["added"], 0)
        self.assertEqual(stats["notebook_folders"]["kept"], 1)
        self.assertEqual(_nb_folders(self.db_b), ["To read"])
        self.assertEqual(len(_nb_notes(self.db_b)), 2)

    def test_rename_to_taken_name_keeps_both_folders(self):
        # A renames its folder to a name B already uses on another folder
        # (legal on A, whose database has no such name). Folder names are
        # UNIQUE, so the merge suffixes the older one instead of crashing.
        folder_a = _nb_folder(self.db_a, "Alpha")
        _nb_note(self.db_a, folder_a, "2501.00001")
        self._sync_a_to_b()
        folder_b = _nb_folder(self.db_b, "Beta")
        _nb_note(self.db_b, folder_b, "2501.00002")

        conn = sqlite3.connect(self.db_a)
        conn.execute(
            "UPDATE notebook_folders SET name = 'Beta', updated_at = ? "
            "WHERE id = ?",
            ("2026-10-05T00:00:00+00:00", folder_a))
        conn.commit()
        conn.close()

        self._sync_a_to_b()
        names = sorted(_nb_folders(self.db_b))
        self.assertEqual(names, ["Beta", "Beta (2)"])
        self.assertEqual(len(_nb_notes(self.db_b)), 2)

    def test_note_of_deleted_folder_is_dropped(self):
        # A deletes the folder after B synced it. The folder tombstone wins,
        # so B's copy of the note goes with the folder instead of
        # resurrecting it under a recreated folder.
        folder = _nb_folder(self.db_a, "F")
        _nb_note(self.db_a, folder, "2501.00001")
        self._sync_a_to_b()
        _nb_delete_folder(self.db_a, folder)
        self._sync_a_to_b()
        self.assertEqual(_nb_folders(self.db_b), [])
        self.assertEqual(_nb_notes(self.db_b), [])

    def test_legacy_snapshot_leaves_local_notebook_untouched(self):
        folder_b = _nb_folder(self.db_b, "Local")
        _nb_note(self.db_b, folder_b, "2501.00001", note="local note")

        snapshot = sync.export_snapshot(self.db_a)
        snapshot.pop("notebook_folders")
        snapshot.pop("notebook_notes")
        sync.import_and_merge(snapshot, self.db_b)

        self.assertEqual(_nb_folders(self.db_b), ["Local"])
        self.assertEqual(_nb_notes(self.db_b),
                         [("Local", "2501.00001", "local note")])

    def test_export_import_tolerate_missing_notebook_tables(self):
        # Databases from before the Notebook feature: no crash, no stats.
        db_c = os.path.join(self.tmp.name, "C", "arxiv_papers.db")
        _make_db(db_c)
        snapshot = sync.export_snapshot(db_c)
        self.assertEqual(snapshot["notebook_folders"], [])
        self.assertEqual(snapshot["notebook_notes"], [])
        stats = sync.import_and_merge(snapshot, db_c)
        self.assertNotIn("notebook_folders", stats)
        self.assertNotIn("notebook_notes", stats)

    def test_migration_backfills_keys_and_timestamps(self):
        # _make_notebook_db creates the 0.4.6 schema; after migration every
        # folder has a key, every note a derived key, and timestamps exist.
        folder = _nb_folder(self.db_a, "Legacy")
        _nb_note(self.db_a, folder, "2501.00001")
        conn = sqlite3.connect(self.db_a)
        folder_key, updated = conn.execute(
            "SELECT sync_key, updated_at FROM notebook_folders "
            "WHERE id = ?", (folder,)).fetchone()
        note_key, note_updated = conn.execute(
            "SELECT sync_key, updated_at FROM notebook_notes "
            "WHERE paper_id = '2501.00001'").fetchone()
        conn.close()
        self.assertTrue(folder_key)
        self.assertTrue(note_key)
        self.assertEqual(note_key,
                         sync.notebook_note_sync_key(folder_key, "2501.00001"))
        # Filled by _nb_folder/_nb_note, but the migration guarantees the
        # epoch fallback for anything older.
        self.assertTrue(updated)
        self.assertTrue(note_updated)

    def test_two_devices_converge_through_provider(self):
        # The real flow: A uploads, B pulls + merges + uploads, A pulls.
        shared = os.path.join(self.tmp.name, "shared")
        for db in (self.db_a, self.db_b):
            cfg = sync.load_config(db)
            cfg["enabled"] = True
            cfg["provider"] = "local_folder"
            cfg["local_folder"]["path"] = shared
            sync.save_config(cfg, db)

        folder_a = _nb_folder(self.db_a, "Phone reading",
                              key="seed-to-read")
        _nb_note(self.db_a, folder_a, "2501.00001", note="filed on desktop")

        self.assertTrue(sync.run_sync(self.db_a)["success"])
        self.assertTrue(sync.run_sync(self.db_b)["success"])
        self.assertEqual(_nb_folders(self.db_b), ["Phone reading"])
        self.assertEqual(_nb_notes(self.db_b),
                         [("Phone reading", "2501.00001", "filed on desktop")])

        # B files another paper; A converges on the next sync.
        folder_b = _nb_folder_id(self.db_b, "Phone reading")
        _nb_note(self.db_b, folder_b, "2501.00002", note="filed on phone")
        self.assertTrue(sync.run_sync(self.db_b)["success"])
        self.assertTrue(sync.run_sync(self.db_a)["success"])
        self.assertEqual(sorted(_nb_notes(self.db_a)),
                         [("Phone reading", "2501.00001", "filed on desktop"),
                          ("Phone reading", "2501.00002", "filed on phone")])


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
