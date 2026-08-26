"""Tests for Chat-page highlights + notes persistence (GitHub issue #6)."""

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
import arxistant_sync as sync


class NormalizeHighlightsTests(unittest.TestCase):
    def test_normalizes_and_dedupes(self):
        # Internal whitespace is preserved verbatim (exact re-match); ends are
        # trimmed and dedup is on the collapsed form.
        value = ["  a passage   of text ", "a passage of text", "okay", "x" * 5000]
        self.assertEqual(server.normalize_highlights(value),
                         json.dumps([{"q": "a passage   of text", "n": "", "c": "#9be7ff"},
                                     {"q": "okay", "n": "", "c": "#9be7ff"}], ensure_ascii=False))

    def test_accepts_objects_with_notes(self):
        value = [{"q": "some quote", "n": "my note", "c": "#ffe08a"}, "bare quote"]
        self.assertEqual(server.normalize_highlights(value),
                         json.dumps([{"q": "some quote", "n": "my note", "c": "#ffe08a"},
                                     {"q": "bare quote", "n": "", "c": "#9be7ff"}], ensure_ascii=False))

    def test_accepts_json_string_and_rejects_garbage(self):
        self.assertEqual(server.normalize_highlights('["one two three"]'),
                         json.dumps([{"q": "one two three", "n": "", "c": "#9be7ff"}], ensure_ascii=False))
        self.assertEqual(server.normalize_highlights("not json"), "")
        self.assertEqual(server.normalize_highlights(None), "")
        self.assertEqual(server.normalize_highlights(42), "")

    def test_parse_roundtrip_and_garbage(self):
        # Legacy bare strings are upgraded to {q, n} objects.
        self.assertEqual(server.parse_highlights('["a b c"]'), [{"q": "a b c", "n": "", "c": "#9be7ff"}])
        self.assertEqual(server.parse_highlights('[{"q": "x y", "n": "z"}]'),
                         [{"q": "x y", "n": "z", "c": "#9be7ff"}])
        self.assertEqual(server.parse_highlights(""), [])
        self.assertEqual(server.parse_highlights("nope"), [])


class HighlightsApiEndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "arxiv_papers.db")
        cls.patchers = [
            mock.patch.object(server, "DB_PATH", cls.db_path),
            mock.patch.object(server, "PUBLICATIONS_JSON",
                              os.path.join(cls.tmp.name, "nope.json")),
            mock.patch.object(sync, "schedule_auto_sync", lambda *a, **k: None),
            mock.patch.object(server, "record_training_change", lambda: {}),
        ]
        for p in cls.patchers:
            p.start()
        server.init_db()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        threading.Thread(target=cls.httpd.serve_forever, daemon=True).start()

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
            headers={"Content-Type": "application/json"}, method="POST")
        try:
            with urllib.request.urlopen(req) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8"))
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8"))

    def _get(self, path):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}{path}") as resp:
            return json.loads(resp.read().decode("utf-8"))

    def _save(self, arxiv_id, **extra):
        body = {"arxiv_id": arxiv_id, "title": "T", "authors": "A",
                "abstract": "B"}
        body.update(extra)
        status, data = self._post("/api/save", body)
        assert status == 200 and data["success"]

    def test_update_highlights_roundtrip_and_preserved_on_resave(self):
        self._save("2501.00010")
        status, body = self._post("/api/update_highlights", {
            "arxiv_id": "2501.00010",
            "highlights": ["we observe a strong flare", "  the   spectrum hardens "],
        })
        self.assertEqual(status, 200)
        self.assertEqual(body["highlights"],
                         [{"q": "we observe a strong flare", "n": "", "c": "#9be7ff"},
                          {"q": "the   spectrum hardens", "n": "", "c": "#9be7ff"}])

        # Re-saving without highlights must not wipe them.
        self._save("2501.00010")
        lib = {p["arxiv_id"]: p for p in self._get("/api/chat/library")["papers"]}
        self.assertEqual(lib["2501.00010"]["highlights"],
                         [{"q": "we observe a strong flare", "n": "", "c": "#9be7ff"},
                          {"q": "the   spectrum hardens", "n": "", "c": "#9be7ff"}])

    def test_annotation_notes_roundtrip_and_survive_resave(self):
        self._save("2501.00012")
        status, body = self._post("/api/update_highlights", {
            "arxiv_id": "2501.00012",
            "highlights": [
                {"q": "a carefully selected passage", "n": "check this result", "c": "#e1bee7"},
                {"q": "a second passage", "n": "compare with section 4"},
            ],
        })
        self.assertEqual(status, 200)
        expected = [
            {"q": "a carefully selected passage", "n": "check this result", "c": "#e1bee7"},
            {"q": "a second passage", "n": "compare with section 4", "c": "#9be7ff"},
        ]
        self.assertEqual(body["highlights"], expected)

        # Saving paper metadata again must retain every passage and note.
        self._save("2501.00012", title="Updated title")
        lib = {p["arxiv_id"]: p for p in self._get("/api/chat/library")["papers"]}
        self.assertEqual(lib["2501.00012"]["highlights"], expected)

    def test_update_highlights_unknown_paper_is_404(self):
        status, body = self._post("/api/update_highlights", {
            "arxiv_id": "2501.99999", "highlights": ["x y z"]})
        self.assertEqual(status, 404)
        self.assertFalse(body["success"])

    def test_notes_flow_shared_with_saved_papers(self):
        self._save("2501.00011")
        status, _ = self._post("/api/update_notes", {
            "arxiv_id": "2501.00011", "notes": "interesting method"})
        self.assertEqual(status, 200)
        lib = {p["arxiv_id"]: p for p in self._get("/api/chat/library")["papers"]}
        self.assertEqual(lib["2501.00011"]["notes"], "interesting method")

        status, body = self._post("/api/update_notes", {
            "arxiv_id": "2501.40404", "notes": "nope"})
        self.assertEqual(status, 404)
        self.assertFalse(body["success"])


class ChatPageHtmlTests(unittest.TestCase):
    def test_chat_page_has_highlight_and_notes_ui(self):
        html = server.CHAT_PAGE_HTML
        self.assertIn("🖍 Highlight", html)
        self.assertIn("highlightSelection", html)
        self.assertIn("/api/update_highlights", html)
        self.assertIn("user-hl", html)
        self.assertIn('id="annotationList"', html)
        self.assertIn("renderAnnotationList", html)
        self.assertIn("setAnnotationColor", html)
        self.assertNotIn('id="notesArea"', html)
        self.assertNotIn('id="quickPrompts"', html)
        self.assertIn("handleHighlightClick", html)
        self.assertIn("selectionWasDragged", html)
        self.assertIn("CSS.highlights", html)
        self.assertIn("new win.Highlight", html)
        self.assertIn("handleTextDocumentClick", html)


if __name__ == "__main__":
    unittest.main()
