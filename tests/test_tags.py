"""Tests for saved-paper tag support (GitHub issue #3)."""

import json
import os
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
                         "JWST,Black Holes")

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

    def test_saved_papers_page_has_tag_filter_and_editor(self):
        self.assertIn('id="tagBar"', server.DATABASE_VIEWER_HTML)
        self.assertIn("Filter by tags", server.DATABASE_VIEWER_HTML)
        self.assertIn("/api/update_tags", server.DATABASE_VIEWER_HTML)
        self.assertIn("openTagEditor", server.DATABASE_VIEWER_HTML)


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
        self.assertEqual(body["tags"], ["SNe", "JWST"])

        # Re-saving without a tags field must not wipe the stored tags.
        self._post("/api/save", {
            "arxiv_id": "2501.00002", "title": "T2", "authors": "A",
            "abstract": "B",
        })
        papers = self._get("/api/papers")["papers"]
        paper = next(p for p in papers if p["arxiv_id"] == "2501.00002")
        self.assertEqual(paper["tags"], "SNe,JWST")

    def test_update_tags_unknown_paper_is_404(self):
        status, body = self._post("/api/update_tags", {
            "arxiv_id": "2501.99999", "tags": "x",
        })
        self.assertEqual(status, 404)
        self.assertFalse(body["success"])


if __name__ == "__main__":
    unittest.main()
