"""Tests for saved-paper tag support (GitHub issue #3)."""

import json
import os
import sqlite3
import sys
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import arxiv_db_server as server
import arxiv_daily_ranker_html as ranker
import arxistant_sync as sync


class NormalizeTagsTests(unittest.TestCase):
    def test_accepts_list_and_string(self):
        self.assertEqual(server.normalize_tags(["jwst", "black holes"]),
                         "jwst,black holes")
        self.assertEqual(server.normalize_tags("jwst, black holes"),
                         "jwst,black holes")

    def test_trims_drops_empty_and_dedupes_case_insensitively(self):
        self.assertEqual(server.normalize_tags(" JWST , ,jwst, Black Holes "),
                         "jwst,black holes")

    def test_none_and_garbage(self):
        self.assertEqual(server.normalize_tags(None), "")
        self.assertEqual(server.normalize_tags(42), "42")

    def test_split_tags_roundtrip(self):
        self.assertEqual(server.split_tags("a,b"), ["a", "b"])
        self.assertEqual(server.split_tags(""), [])
        self.assertEqual(server.split_tags(None), [])


class TagUiHtmlTests(unittest.TestCase):
    def test_daily_page_save_script_includes_tag_editor(self):
        self.assertIn("/api/update_tags", server.SAVE_BUTTON_SCRIPT)
        self.assertIn("tag-editor", server.SAVE_BUTTON_SCRIPT)

    def test_ranker_page_save_script_includes_tag_editor_with_absolute_base(self):
        self.assertIn("const ARX = 'http://localhost:8765';",
                      ranker.SAVE_BUTTON_SCRIPT)
        self.assertIn("/api/update_tags", ranker.SAVE_BUTTON_SCRIPT)
        self.assertIn("<!-- save-button-embedded -->", ranker.SAVE_BUTTON_SCRIPT)

    def test_daily_and_recent_tag_editor_supports_tab_autocomplete(self):
        for script in (server.SAVE_BUTTON_SCRIPT, ranker.SAVE_BUTTON_SCRIPT):
            self.assertIn("closestExistingTag", script)
            self.assertIn("editDistance", script)
            self.assertIn("e.key === 'Tab'", script)
            self.assertIn("tag-ghost", script)
            self.assertIn("tag-ghost-prefix", script)
            self.assertNotIn("tag-suggestion", script)

    def test_saved_papers_page_has_tag_filter_and_editor(self):
        self.assertIn('id="tagBar"', server.DATABASE_VIEWER_HTML)
        self.assertIn("Filter by tags", server.DATABASE_VIEWER_HTML)
        self.assertIn("/api/update_tags", server.DATABASE_VIEWER_HTML)
        self.assertIn("openTagEditor", server.DATABASE_VIEWER_HTML)

    def test_tag_editors_autosave_without_save_or_close_buttons(self):
        # Tags persist on every add/remove and the editor closes on an
        # outside click, so there is no explicit Save/Close button anymore.
        for html in (server.SAVE_BUTTON_SCRIPT, ranker.SAVE_BUTTON_SCRIPT,
                     server.DATABASE_VIEWER_HTML):
            self.assertNotIn("tag-save", html)
            self.assertNotIn("tag-close", html)
            self.assertIn("/api/update_tags", html)


class TagApiEndToEndTests(unittest.TestCase):
    """Runs the real HTTP handler against a throwaway database."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "arxiv_papers.db")

        cls.patchers = [
            mock.patch.object(server, "DB_PATH", cls.db_path),
            mock.patch.object(server, "PUBLICATIONS_JSON",
                              os.path.join(cls.tmp.name, "nope.json")),
            mock.patch.object(sync, "schedule_auto_sync", lambda *a, **k: None),
            # Keep the test from touching the real retrain-state file.
            mock.patch.object(server, "record_training_change", lambda: {}),
        ]
        for p in cls.patchers:
            p.start()

        server.init_db()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        for p in cls.patchers:
            p.stop()
        cls.tmp.cleanup()

    def _post(self, path, payload):
        req = urllib.request.Request(
            f"http://127.0.0.1:{self.port}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def _get(self, path):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}{path}") as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _get_status(self, path):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}{path}") as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def test_save_with_tags_and_persistence(self):
        status, body = self._post("/api/save", {
            "arxiv_id": "2501.00001", "title": "T", "authors": "A",
            "abstract": "B", "relevance_score": 3, "date_fetched": "2025-01-01",
            "tags": ["jwst", "cmb"],
        })
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])

        papers = self._get("/api/papers")["papers"]
        paper = next(p for p in papers if p["arxiv_id"] == "2501.00001")
        self.assertEqual(paper["tags"], "jwst,cmb")

    def test_update_tags_and_resave_preserves_them(self):
        self._post("/api/save", {
            "arxiv_id": "2501.00002", "title": "T2", "authors": "A",
            "abstract": "B",
        })
        status, body = self._post("/api/update_tags", {
            "arxiv_id": "2501.00002", "tags": "SNe, JWST",
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["tags"], ["sne", "jwst"])

        # Re-saving without a tags field must not wipe the stored tags.
        self._post("/api/save", {
            "arxiv_id": "2501.00002", "title": "T2", "authors": "A",
            "abstract": "B",
        })
        papers = self._get("/api/papers")["papers"]
        paper = next(p for p in papers if p["arxiv_id"] == "2501.00002")
        self.assertEqual(paper["tags"], "sne,jwst")

    def test_update_tags_unknown_paper_is_404(self):
        status, body = self._post("/api/update_tags", {
            "arxiv_id": "2501.99999", "tags": "x",
        })
        self.assertEqual(status, 404)
        self.assertFalse(body["success"])

    def test_api_paper_returns_fresh_state(self):
        self._post("/api/save", {
            "arxiv_id": "2501.00003", "title": "T3", "authors": "A",
            "abstract": "B", "tags": ["fresh"],
        })
        status, body = self._get_status("/api/paper?arxiv_id=2501.00003")
        self.assertEqual(status, 200)
        self.assertEqual(body["paper"]["tags"], "fresh")

        status, body = self._get_status("/api/paper?arxiv_id=2501.40404")
        self.assertEqual(status, 404)
        self.assertFalse(body["success"])


class TagLowercaseMigrationTests(unittest.TestCase):
    def test_init_db_lowercases_preexisting_tags(self):
        with tempfile.TemporaryDirectory() as tmp:
            db = os.path.join(tmp, "arxiv_papers.db")
            conn = sqlite3.connect(db)
            conn.execute(
                "CREATE TABLE saved_papers (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " arxiv_id TEXT NOT NULL UNIQUE, title TEXT NOT NULL, authors TEXT,"
                " abstract TEXT, relevance_score INTEGER DEFAULT 0, date_fetched TEXT,"
                " date_saved TEXT DEFAULT CURRENT_TIMESTAMP, notes TEXT DEFAULT '',"
                " tags TEXT DEFAULT '', highlights TEXT DEFAULT '', updated_at TEXT)")
            conn.execute(
                "INSERT INTO saved_papers (arxiv_id, title, tags)"
                " VALUES ('1', 'T', 'JWST,Black Holes')")
            conn.execute(
                "CREATE TABLE my_publications (id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " bibcode TEXT NOT NULL UNIQUE, title TEXT NOT NULL, authors TEXT,"
                " abstract TEXT, keywords TEXT, year TEXT,"
                " date_added TEXT DEFAULT CURRENT_TIMESTAMP, updated_at TEXT)")
            conn.commit()
            conn.close()

            with mock.patch.object(server, "DB_PATH", db), \
                 mock.patch.object(server, "PUBLICATIONS_JSON",
                                   os.path.join(tmp, "nope.json")):
                server.init_db()

            conn = sqlite3.connect(db)
            row = conn.execute(
                "SELECT tags FROM saved_papers WHERE arxiv_id='1'").fetchone()
            conn.close()
            self.assertEqual(row[0], "jwst,black holes")


if __name__ == "__main__":
    unittest.main()
