"""Tests for the Notebook page: reading-list folders whose notes link to papers.

The Notebook (/notebook.html) ships with a seeded "To read" folder. Folders
can be added, renamed, and deleted; each folder holds lightweight notes that
link to papers by the same storage key the rest of the app uses (arXiv ID,
bibcode, or local- document id). Notes carry an optional text and are unique
per (folder, paper); note titles are back-filled from saved_papers so a paper
added by bare ID shows its real title once it has been saved.
"""

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
import arxistant_sync as sync


class NormalizeNotebookNameTests(unittest.TestCase):
    def test_trims_and_collapses_whitespace(self):
        self.assertEqual(server.normalize_notebook_name("  To   read "), "To read")
        self.assertEqual(server.normalize_notebook_name("A\n\tB"), "A B")

    def test_caps_length(self):
        self.assertEqual(len(server.normalize_notebook_name("x" * 500)),
                         server.NOTEBOOK_NAME_MAX)

    def test_empty_and_none(self):
        self.assertEqual(server.normalize_notebook_name(""), "")
        self.assertEqual(server.normalize_notebook_name("   "), "")
        self.assertEqual(server.normalize_notebook_name(None), "")


class NotebookSeedTests(unittest.TestCase):
    """init_db seeds the default 'To read' folder exactly once."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.db_path = os.path.join(self.tmp.name, "arxiv_papers.db")
        self.patchers = [
            mock.patch.object(server, "DB_PATH", self.db_path),
            mock.patch.object(server, "PUBLICATIONS_JSON",
                              os.path.join(self.tmp.name, "nope.json")),
        ]
        for p in self.patchers:
            p.start()

    def tearDown(self):
        for p in self.patchers:
            p.stop()
        self.tmp.cleanup()

    def _folder_names(self):
        conn = sqlite3.connect(self.db_path)
        rows = conn.execute(
            "SELECT name FROM notebook_folders ORDER BY id").fetchall()
        conn.close()
        return [r[0] for r in rows]

    def test_first_init_seeds_to_read(self):
        server.init_db()
        self.assertEqual(self._folder_names(), ["To read"])

    def test_second_init_does_not_duplicate(self):
        server.init_db()
        server.init_db()
        self.assertEqual(self._folder_names(), ["To read"])

    def test_deleted_folders_do_not_come_back(self):
        server.init_db()
        conn = sqlite3.connect(self.db_path)
        conn.execute("DELETE FROM notebook_folders")
        conn.commit()
        conn.close()
        server.init_db()
        self.assertEqual(self._folder_names(), [])

    def test_user_folders_survive_reinit(self):
        server.init_db()
        conn = sqlite3.connect(self.db_path)
        conn.execute("INSERT INTO notebook_folders (name) VALUES ('AGN')")
        conn.commit()
        conn.close()
        server.init_db()
        self.assertEqual(self._folder_names(), ["To read", "AGN"])


class NotebookPageHtmlTests(unittest.TestCase):
    def test_page_is_served_at_notebook_html_route(self):
        self.assertIn('elif path == "/notebook.html":',
                      (PROJECT_ROOT / "src" / "arxiv_db_server.py").read_text())

    def test_page_wires_folder_and_note_actions(self):
        html = server.NOTEBOOK_HTML
        self.assertIn("/api/notebook/folder/add", html)
        self.assertIn("/api/notebook/folder/rename", html)
        self.assertIn("/api/notebook/folder/delete", html)
        self.assertIn("/api/notebook/note/add", html)
        self.assertIn("/api/notebook/note/update", html)
        self.assertIn("/api/notebook/note/delete", html)
        # Notes link out to the papers themselves.
        self.assertIn("alphaxiv.org/abs/", html)
        self.assertIn("/chat.html?paper=", html)

    def test_mobile_menu_links_the_notebook(self):
        self.assertIn("'/notebook.html'", server.MOBILE_MENU_SCRIPT)
        self.assertIn("Notebook", server.MOBILE_MENU_SCRIPT)

    def test_legacy_navs_link_the_notebook(self):
        for page in (server.DATABASE_VIEWER_HTML, server.CHAT_PAGE_HTML,
                     server.PUBLICATIONS_VIEWER_HTML, server.CLOUD_SYNC_HTML,
                     server.SETTINGS_HTML):
            self.assertIn('/notebook.html', page)


class NotebookApiTests(unittest.TestCase):
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

    def _get_raw(self, path):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}{path}") as resp:
            return resp.status, resp.read().decode("utf-8")

    def _folder_by_name(self, name):
        folders = self._get("/api/notebook")["folders"]
        return next((f for f in folders if f["name"] == name), None)

    def _add_folder(self, name):
        status, body = self._post("/api/notebook/folder/add", {"name": name})
        self.assertEqual(status, 200, body)
        return body["folder"]["id"]

    # ── page ──

    def test_notebook_page_is_served(self):
        status, html = self._get_raw("/notebook.html")
        self.assertEqual(status, 200)
        self.assertIn("📓 Notebook", html)
        self.assertIn("/api/notebook", html)

    # ── folders ──

    def test_seeded_to_read_folder(self):
        folder = self._folder_by_name("To read")
        self.assertIsNotNone(folder)
        self.assertIsInstance(folder["notes"], list)

    def test_add_folder(self):
        folder_id = self._add_folder("Read in 2026")
        folder = self._folder_by_name("Read in 2026")
        self.assertIsNotNone(folder)
        self.assertEqual(folder["id"], folder_id)

    def test_add_folder_normalizes_name(self):
        self._post("/api/notebook/folder/add", {"name": "  Quasar   feeds "})
        self.assertIsNotNone(self._folder_by_name("Quasar feeds"))

    def test_add_duplicate_folder_conflicts(self):
        self._add_folder("Dup")
        status, body = self._post("/api/notebook/folder/add", {"name": "Dup"})
        self.assertEqual(status, 409)
        self.assertFalse(body["success"])

    def test_add_empty_folder_rejected(self):
        for name in ("", "   ", None):
            status, body = self._post("/api/notebook/folder/add", {"name": name})
            self.assertEqual(status, 400)
            self.assertFalse(body["success"])

    def test_rename_folder(self):
        folder_id = self._add_folder("Old name")
        status, body = self._post("/api/notebook/folder/rename",
                                  {"folder_id": folder_id, "name": "New name"})
        self.assertEqual(status, 200)
        self.assertIsNone(self._folder_by_name("Old name"))
        renamed = self._folder_by_name("New name")
        self.assertIsNotNone(renamed)
        self.assertEqual(renamed["id"], folder_id)

    def test_rename_to_existing_name_conflicts(self):
        self._add_folder("Taken")
        folder_id = self._add_folder("Other")
        status, body = self._post("/api/notebook/folder/rename",
                                  {"folder_id": folder_id, "name": "Taken"})
        self.assertEqual(status, 409)
        self.assertFalse(body["success"])

    def test_rename_keeps_same_name(self):
        # Renaming a folder to its current name is a no-op success, not a
        # self-conflict.
        folder_id = self._add_folder("Stable")
        status, body = self._post("/api/notebook/folder/rename",
                                  {"folder_id": folder_id, "name": " Stable "})
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])

    def test_rename_unknown_folder_is_404(self):
        status, body = self._post("/api/notebook/folder/rename",
                                  {"folder_id": 999999, "name": "X"})
        self.assertEqual(status, 404)
        self.assertFalse(body["success"])

    def test_rename_to_empty_rejected(self):
        folder_id = self._add_folder("Nonempty")
        status, _ = self._post("/api/notebook/folder/rename",
                               {"folder_id": folder_id, "name": "  "})
        self.assertEqual(status, 400)
        self.assertIsNotNone(self._folder_by_name("Nonempty"))

    def test_delete_folder_removes_its_notes(self):
        folder_id = self._add_folder("Doomed")
        self._post("/api/notebook/note/add",
                   {"folder_id": folder_id, "paper_id": "2501.11111",
                    "title": "Bye"})
        status, body = self._post("/api/notebook/folder/delete",
                                  {"folder_id": folder_id})
        self.assertEqual(status, 200)
        self.assertTrue(body["removed"])
        self.assertEqual(body["notes_deleted"], 1)
        self.assertIsNone(self._folder_by_name("Doomed"))
        conn = sqlite3.connect(self.db_path)
        left = conn.execute("SELECT COUNT(*) FROM notebook_notes WHERE folder_id = ?",
                            (folder_id,)).fetchone()[0]
        conn.close()
        self.assertEqual(left, 0)

    def test_delete_unknown_folder(self):
        status, body = self._post("/api/notebook/folder/delete",
                                  {"folder_id": 999999})
        self.assertEqual(status, 200)
        self.assertFalse(body["removed"])

    # ── notes ──

    def test_add_note_links_paper_into_folder(self):
        folder_id = self._add_folder("To read (notes)")
        status, body = self._post("/api/notebook/note/add", {
            "folder_id": folder_id, "paper_id": "2501.00001",
            "title": "Quasar feedback", "authors": "Shangguan, Jinyi",
            "note": "read before the proposal deadline",
        })
        self.assertEqual(status, 200)
        self.assertTrue(body["added"])
        self.assertIsNotNone(body["note_id"])
        folders = self._get("/api/notebook")["folders"]
        folder = next(f for f in folders if f["id"] == folder_id)
        self.assertEqual(len(folder["notes"]), 1)
        note = folder["notes"][0]
        self.assertEqual(note["paper_id"], "2501.00001")
        self.assertEqual(note["title"], "Quasar feedback")
        self.assertEqual(note["note"], "read before the proposal deadline")

    def test_same_paper_twice_in_folder_is_not_duplicated(self):
        folder_id = self._add_folder("Dedupe")
        self._post("/api/notebook/note/add",
                   {"folder_id": folder_id, "paper_id": "2501.00002"})
        status, body = self._post("/api/notebook/note/add",
                                  {"folder_id": folder_id, "paper_id": "2501.00002"})
        self.assertEqual(status, 200)
        self.assertFalse(body["added"])
        folders = self._get("/api/notebook")["folders"]
        folder = next(f for f in folders if f["id"] == folder_id)
        self.assertEqual(len(folder["notes"]), 1)

    def test_re_adding_with_note_refreshes_note_text(self):
        folder_id = self._add_folder("Refresh")
        self._post("/api/notebook/note/add",
                   {"folder_id": folder_id, "paper_id": "2501.00003",
                    "note": "first"})
        self._post("/api/notebook/note/add",
                   {"folder_id": folder_id, "paper_id": "2501.00003",
                    "note": "second"})
        folders = self._get("/api/notebook")["folders"]
        folder = next(f for f in folders if f["id"] == folder_id)
        self.assertEqual(len(folder["notes"]), 1)
        self.assertEqual(folder["notes"][0]["note"], "second")

    def test_same_paper_can_live_in_two_folders(self):
        a = self._add_folder("Folder A")
        b = self._add_folder("Folder B")
        self._post("/api/notebook/note/add", {"folder_id": a, "paper_id": "2501.00004"})
        status, body = self._post("/api/notebook/note/add",
                                  {"folder_id": b, "paper_id": "2501.00004"})
        self.assertEqual(status, 200)
        self.assertTrue(body["added"])

    def test_add_note_unknown_folder_is_404(self):
        status, body = self._post("/api/notebook/note/add",
                                  {"folder_id": 999999, "paper_id": "2501.00005"})
        self.assertEqual(status, 404)
        self.assertFalse(body["success"])

    def test_add_note_without_paper_rejected(self):
        folder_id = self._add_folder("Needs paper")
        status, body = self._post("/api/notebook/note/add",
                                  {"folder_id": folder_id, "paper_id": "  "})
        self.assertEqual(status, 400)
        self.assertFalse(body["success"])

    def test_note_title_backfilled_from_saved_papers(self):
        folder_id = self._add_folder("Backfill")
        self._post("/api/notebook/note/add",
                   {"folder_id": folder_id, "paper_id": "2501.00006"})
        # Saving the paper later gives the bare-ID note its real metadata.
        self._post("/api/save", {"arxiv_id": "2501.00006",
                                 "title": "Late title", "authors": "Late author",
                                 "abstract": ""})
        folders = self._get("/api/notebook")["folders"]
        folder = next(f for f in folders if f["id"] == folder_id)
        note = folder["notes"][0]
        self.assertEqual(note["title"], "Late title")
        self.assertEqual(note["authors"], "Late author")

    def test_update_note_text(self):
        folder_id = self._add_folder("Editable")
        _, body = self._post("/api/notebook/note/add",
                             {"folder_id": folder_id, "paper_id": "2501.00007",
                              "note": "draft"})
        note_id = body["note_id"]
        status, body = self._post("/api/notebook/note/update",
                                  {"note_id": note_id, "note": "final thoughts"})
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        folders = self._get("/api/notebook")["folders"]
        folder = next(f for f in folders if f["id"] == folder_id)
        self.assertEqual(folder["notes"][0]["note"], "final thoughts")

    def test_update_note_clears_text(self):
        folder_id = self._add_folder("Clearable")
        _, body = self._post("/api/notebook/note/add",
                             {"folder_id": folder_id, "paper_id": "2501.00008",
                              "note": "temporary"})
        status, _ = self._post("/api/notebook/note/update",
                               {"note_id": body["note_id"], "note": ""})
        self.assertEqual(status, 200)
        folders = self._get("/api/notebook")["folders"]
        folder = next(f for f in folders if f["id"] == folder_id)
        self.assertEqual(folder["notes"][0]["note"], "")

    def test_update_unknown_note_is_404(self):
        status, body = self._post("/api/notebook/note/update",
                                  {"note_id": 999999, "note": "x"})
        self.assertEqual(status, 404)
        self.assertFalse(body["success"])

    def test_delete_note(self):
        folder_id = self._add_folder("Removable")
        _, body = self._post("/api/notebook/note/add",
                             {"folder_id": folder_id, "paper_id": "2501.00009"})
        status, body = self._post("/api/notebook/note/delete",
                                  {"note_id": body["note_id"]})
        self.assertEqual(status, 200)
        self.assertTrue(body["removed"])
        folders = self._get("/api/notebook")["folders"]
        folder = next(f for f in folders if f["id"] == folder_id)
        self.assertEqual(folder["notes"], [])

    def test_notes_newest_first(self):
        folder_id = self._add_folder("Ordered")
        self._post("/api/notebook/note/add",
                   {"folder_id": folder_id, "paper_id": "2501.00010"})
        self._post("/api/notebook/note/add",
                   {"folder_id": folder_id, "paper_id": "2501.00011"})
        folders = self._get("/api/notebook")["folders"]
        folder = next(f for f in folders if f["id"] == folder_id)
        self.assertEqual([n["paper_id"] for n in folder["notes"]],
                         ["2501.00011", "2501.00010"])


class NotebookButtonScriptTests(unittest.TestCase):
    """The per-paper 📓 button script injected into the daily/recent/search pages."""

    def test_script_has_marker_and_no_collision(self):
        script = server.NOTEBOOK_BUTTON_SCRIPT
        self.assertTrue(script.startswith("<!-- notebook-button-embedded -->"))
        self.assertIn("notebook-button-embedded", script)

    def test_script_wires_button_folder_picker_and_api(self):
        script = server.NOTEBOOK_BUTTON_SCRIPT
        self.assertIn("notebook-btn", script)                 # the button
        self.assertIn("notebook-editor", script)               # the box below
        self.assertIn("arxistantAttachNotebookButtons", script)
        self.assertIn("/api/notebook", script)                 # folder list
        self.assertIn("/api/notebook/note/add", script)        # add paper
        self.assertIn("/api/notebook/folder/add", script)      # create folder

    def test_button_is_placed_right_after_the_tag_button(self):
        # The user asked for the button "just after the tag"; the script finds
        # the .tag-btn and inserts after it.
        script = server.NOTEBOOK_BUTTON_SCRIPT
        self.assertIn("querySelector('.tag-btn')", script)
        self.assertIn("insertAdjacentElement('afterend'", script)


class NotebookButtonInjectionTests(unittest.TestCase):
    """The daily, recent, and search serve paths inject the notebook script."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.db_path = os.path.join(cls.tmp.name, "arxiv_papers.db")
        # A minimal generated-style page: a paper card plus the ranker's
        # save-button marker (so the server injects chat/listen/notebook but
        # not its own save script, matching real generated files).
        cls.page = (
            "<!DOCTYPE html><html><head><title>Daily</title></head><body>"
            '<h1 data-date="2026-09-23">Daily</h1>'
            '<div class="paper">'
            '<h2>1. <span class="arxiv-id"><a href="https://arxiv.org/abs/2609.25371">arXiv:2609.25371</a></span>'
            ' — <a href="#">Title</a></h2>'
            '<p class="authors"><strong>Authors:</strong> J. Doe</p>'
            '<div class="score-row"><span class="score">Relevance: 90/100</span>'
            '<span class="paper-actions"></span></div>'
            "</div>"
            "<!-- save-button-embedded -->"
            "</body></html>")
        cls.daily_path = os.path.join(cls.tmp.name, "daily.html")
        cls.recent_path = os.path.join(cls.tmp.name, "recent.html")
        for p in (cls.daily_path, cls.recent_path):
            with open(p, "w", encoding="utf-8") as f:
                f.write(cls.page)
        cls.patchers = [
            mock.patch.object(server, "DB_PATH", cls.db_path),
            mock.patch.object(server, "DAILY_HTML", cls.daily_path),
            mock.patch.object(server, "RECENT_HTML", cls.recent_path),
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
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        for p in cls.patchers:
            p.stop()
        cls.tmp.cleanup()

    def _get_html(self, path):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}{path}") as resp:
            return resp.read().decode("utf-8")

    def test_daily_and_recent_inject_the_notebook_button_once(self):
        for path in ("/daily.html", "/recent.html", "/"):
            html = self._get_html(path)
            self.assertEqual(html.count("<!-- notebook-button-embedded -->"), 1,
                             f"notebook marker count on {path}")
            self.assertIn("arxistantAttachNotebookButtons", html)
            self.assertIn("/api/notebook/note/add", html)
            # The ranker's save script must not be double-injected.
            self.assertEqual(html.count("<!-- save-button-embedded -->"), 1, path)
            # Chat/listen injections are unaffected.
            self.assertIn("<!-- chat-link-embedded -->", html)
            self.assertIn("<!-- listen-button-embedded -->", html)

    def test_search_page_injects_the_notebook_button_once(self):
        # The Search page is served from SEARCH_HTML (not a generated file),
        # and its results are rendered dynamically — so both the injection
        # and the render-time attach hook must be present.
        html = self._get_html("/search.html")
        self.assertEqual(html.count("<!-- notebook-button-embedded -->"), 1)
        self.assertIn("arxistantAttachNotebookButtons", html)
        self.assertIn("window.arxistantAttachNotebookButtons()", html)
        self.assertIn("/api/notebook/note/add", html)
        # Save/chat injections are unaffected, and nothing is double-injected
        # (the save script has no marker comment; its attach function is the
        # presence check, like in test_search_page.py).
        self.assertIn("arxistantAttachSaveButtons", html)
        self.assertIn("arxistantAttachChatLinks", html)
        self.assertEqual(html.count("<!-- chat-link-embedded -->"), 1)


if __name__ == "__main__":
    unittest.main()
