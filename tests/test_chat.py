import json
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import arxiv_db_server


class _FakeSSEResponse:
    """Stands in for a urllib response from an OpenAI-compatible API."""

    def __init__(self, chunks, content_type="text/event-stream; charset=utf-8"):
        self.headers = {"Content-Type": content_type}
        self._chunks = chunks
        self.closed = False

    def __iter__(self):
        return iter(self._chunks)

    def read(self):
        return b"".join(self._chunks)

    def close(self):
        self.closed = True


class ChatRequestTests(unittest.TestCase):
    def test_build_chat_request_appends_completions_path(self):
        req = arxiv_db_server.build_chat_request(
            "https://api.example.com/v1/", "test-model",
            [{"role": "user", "content": "hi"}], 0.5, "secret-key")

        self.assertEqual(req.full_url, "https://api.example.com/v1/chat/completions")
        self.assertEqual(req.get_header("Authorization"), "Bearer secret-key")
        self.assertEqual(req.get_header("Content-type"), "application/json")
        payload = json.loads(req.data.decode())
        self.assertEqual(payload["model"], "test-model")
        self.assertTrue(payload["stream"])
        self.assertEqual(payload["temperature"], 0.5)
        self.assertEqual(payload["messages"], [{"role": "user", "content": "hi"}])

    def test_build_chat_request_keeps_explicit_completions_url_and_allows_empty_key(self):
        req = arxiv_db_server.build_chat_request(
            "https://api.example.com/v1/chat/completions", "m",
            [{"role": "user", "content": "hi"}], None, "")

        self.assertEqual(req.full_url, "https://api.example.com/v1/chat/completions")
        self.assertIsNone(req.get_header("Authorization"))
        payload = json.loads(req.data.decode())
        self.assertNotIn("temperature", payload)

    def test_iter_chat_sse_forwards_event_stream_lines(self):
        resp = _FakeSSEResponse([
            b": keep-alive\n",
            b'data: {"choices":[{"delta":{"content":"He"}}]}\n',
            b"\n",
            b'data: {"choices":[{"delta":{"content":"llo"}}]}\n',
            b"data: [DONE]\n",
        ])

        lines = list(arxiv_db_server.iter_chat_sse(resp))

        self.assertEqual(lines, [
            'data: {"choices":[{"delta":{"content":"He"}}]}',
            'data: {"choices":[{"delta":{"content":"llo"}}]}',
            "data: [DONE]",
        ])

    def test_iter_chat_sse_wraps_plain_json_response(self):
        resp = _FakeSSEResponse(
            [b'{"choices":[{"message":{"content":"hi"}}]}'],
            content_type="application/json")

        lines = list(arxiv_db_server.iter_chat_sse(resp))

        self.assertEqual(lines, [
            'data: {"choices":[{"message":{"content":"hi"}}]}',
            "data: [DONE]",
        ])


class ChatConfigTests(unittest.TestCase):
    def test_config_roundtrip_and_defaults(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "chat_config.json")
            with mock.patch.object(arxiv_db_server, "CHAT_CONFIG_PATH", path):
                self.assertEqual(
                    arxiv_db_server.load_chat_config(),
                    {"base_url": "", "model": "", "temperature": 0.7})
                arxiv_db_server.save_chat_config({
                    "base_url": "https://api.example.com/v1",
                    "model": "test-model",
                    "temperature": 0.2,
                })
                config = arxiv_db_server.load_chat_config()

        self.assertEqual(config["base_url"], "https://api.example.com/v1")
        self.assertEqual(config["model"], "test-model")
        self.assertAlmostEqual(config["temperature"], 0.2)

    def test_load_config_survives_corrupt_file(self):
        with tempfile.TemporaryDirectory() as tmp:
            path = os.path.join(tmp, "chat_config.json")
            with open(path, "w", encoding="utf-8") as f:
                f.write("{not json")
            with mock.patch.object(arxiv_db_server, "CHAT_CONFIG_PATH", path):
                config = arxiv_db_server.load_chat_config()

        self.assertEqual(config, arxiv_db_server.DEFAULT_CHAT_CONFIG)


class PdfCacheTests(unittest.TestCase):
    PDF_BYTES = b"%PDF-1.4 fake\n" + b"x" * 2048

    def test_safe_pdf_filename(self):
        self.assertEqual(
            arxiv_db_server._safe_pdf_filename("2606.11084"), "2606.11084.pdf")
        self.assertEqual(
            arxiv_db_server._safe_pdf_filename("astro-ph/9901001"),
            "astro-ph_9901001.pdf")
        self.assertEqual(
            arxiv_db_server._safe_pdf_filename("2606.11084v2"), "2606.11084v2.pdf")
        for bad in ("", "  ", "../etc/passwd", "a/../b", "foo bar", "id;rm", "a/b/c/d"):
            self.assertIsNone(arxiv_db_server._safe_pdf_filename(bad), bad)

    def test_fetch_paper_pdf_downloads_once_and_caches(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = os.path.join(tmp, "pdf")
            with mock.patch.object(arxiv_db_server, "PDF_CACHE_DIR", cache_dir), \
                 mock.patch.object(arxiv_db_server.urllib.request, "urlopen") as urlopen:
                urlopen.return_value = _FakePdfResponse(self.PDF_BYTES)
                path1 = arxiv_db_server.fetch_paper_pdf("2606.00004")
                self.assertTrue(path1.endswith("2606.00004.pdf"))
                with open(path1, "rb") as f:
                    self.assertEqual(f.read(), self.PDF_BYTES)
                path2 = arxiv_db_server.fetch_paper_pdf("2606.00004")
                self.assertEqual(path1, path2)
                self.assertEqual(urlopen.call_count, 1)

    def test_fetch_paper_pdf_rejects_non_pdf_response(self):
        with tempfile.TemporaryDirectory() as tmp:
            cache_dir = os.path.join(tmp, "pdf")
            with mock.patch.object(arxiv_db_server, "PDF_CACHE_DIR", cache_dir), \
                 mock.patch.object(arxiv_db_server.urllib.request, "urlopen") as urlopen:
                urlopen.return_value = _FakePdfResponse(b"<html>withdrawn</html>")
                with self.assertRaises(ValueError):
                    arxiv_db_server.fetch_paper_pdf("2606.00005")

    def test_fetch_paper_pdf_rejects_invalid_id(self):
        with self.assertRaises(ValueError):
            arxiv_db_server.fetch_paper_pdf("../escape")


class _FakePdfResponse:
    def __init__(self, data):
        self._data = data
        self._pos = 0

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return False

    def read(self, size=None):
        if size is None:
            chunk = self._data[self._pos:]
            self._pos = len(self._data)
        else:
            chunk = self._data[self._pos:self._pos + size]
            self._pos += len(chunk)
        return chunk


class ChatLibraryTests(unittest.TestCase):
    def test_library_merges_saved_daily_and_recent(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "papers.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE saved_papers ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " arxiv_id TEXT NOT NULL UNIQUE, title TEXT NOT NULL,"
                " authors TEXT, abstract TEXT,"
                " relevance_score INTEGER DEFAULT 0, date_fetched TEXT,"
                " date_saved TEXT DEFAULT CURRENT_TIMESTAMP,"
                " notes TEXT DEFAULT '', updated_at TEXT)")
            conn.execute(
                "INSERT INTO saved_papers"
                " (arxiv_id, title, authors, abstract, relevance_score)"
                " VALUES ('2606.00001', 'Saved paper', 'A. Author',"
                " 'An abstract', 42)")
            conn.commit()
            conn.close()

            daily_path = os.path.join(tmp, "daily.json")
            recent_path = os.path.join(tmp, "recent.json")
            with open(daily_path, "w", encoding="utf-8") as f:
                json.dump([
                    {"id": "2606.00001", "title": "Duplicate",
                     "authors": ["X"], "abstract": "d", "score": 9},
                    {"id": "2606.00002", "title": "Daily paper",
                     "authors": ["Y", "Z"], "abstract": "a", "score": 7},
                ], f)
            with open(recent_path, "w", encoding="utf-8") as f:
                json.dump([
                    {"id": "2606.00002", "title": "Duplicate daily",
                     "authors": ["Y"], "abstract": "a", "score": 5},
                    {"id": "2606.00003", "title": "Recent paper",
                     "authors": ["W"], "abstract": "r", "score": 3},
                ], f)

            with mock.patch.object(arxiv_db_server, "DB_PATH", db_path), \
                 mock.patch.object(arxiv_db_server, "DAILY_JSON", daily_path), \
                 mock.patch.object(arxiv_db_server, "RECENT_JSON", recent_path):
                papers = arxiv_db_server.collect_chat_library()

        by_id = {p["arxiv_id"]: p for p in papers}
        self.assertEqual(set(by_id), {"2606.00001", "2606.00002", "2606.00003"})
        self.assertEqual(by_id["2606.00001"]["source"], "saved")
        self.assertEqual(by_id["2606.00001"]["title"], "Saved paper")
        self.assertEqual(by_id["2606.00002"]["source"], "daily")
        self.assertEqual(by_id["2606.00002"]["authors"], "Y, Z")
        self.assertEqual(by_id["2606.00003"]["source"], "recent")

    def test_library_tolerates_missing_json_snapshots(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "papers.db")
            conn = sqlite3.connect(db_path)
            conn.execute(
                "CREATE TABLE saved_papers ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " arxiv_id TEXT NOT NULL UNIQUE, title TEXT NOT NULL,"
                " authors TEXT, abstract TEXT,"
                " relevance_score INTEGER DEFAULT 0, date_fetched TEXT,"
                " date_saved TEXT DEFAULT CURRENT_TIMESTAMP,"
                " notes TEXT DEFAULT '', updated_at TEXT)")
            conn.commit()
            conn.close()

            with mock.patch.object(arxiv_db_server, "DB_PATH", db_path), \
                 mock.patch.object(arxiv_db_server, "DAILY_JSON",
                                   os.path.join(tmp, "missing-daily.json")), \
                 mock.patch.object(arxiv_db_server, "RECENT_JSON",
                                   os.path.join(tmp, "missing-recent.json")):
                papers = arxiv_db_server.collect_chat_library()

        self.assertEqual(papers, [])


class ChatPageHtmlTests(unittest.TestCase):
    """Guard the embedded chat page against structural regressions."""

    def test_hidden_css_rule_present(self):
        # The picker/reader toggle relies entirely on this rule; a rewrite
        # that drops it leaves the PDF rendered off-screen (see issue #2).
        self.assertIn(
            ".hidden { display: none !important; }",
            arxiv_db_server.CHAT_PAGE_HTML)

    def test_required_element_ids_present(self):
        for ident in ("layout", "pickerView", "readerView", "pdfFrame",
                      "pdfStatus", "paperInfo", "chatMessages"):
            self.assertIn(f'id="{ident}"', arxiv_db_server.CHAT_PAGE_HTML)


class FulltextTests(unittest.TestCase):
    HTML = (b"<html><head><title>t</title></head><body>"
            b"<script>alert(1)</script><p>Hello full text</p></body></html>")

    def test_fulltext_strips_scripts_and_injects_base(self):
        import tempfile, os
        from unittest import mock
        tmp = tempfile.mkdtemp()
        with mock.patch.object(arxiv_db_server, "FULLTEXT_CACHE_DIR", tmp), \
             mock.patch.object(arxiv_db_server.urllib.request, "urlopen") as u:
            u.return_value = _FakePdfResponse(self.HTML)
            path = arxiv_db_server.fetch_paper_fulltext("2606.11084")
            html = open(path, encoding="utf-8").read()
        self.assertNotIn("<script", html)
        self.assertIn("<base href=", html)
        self.assertIn("Hello full text", html)


if __name__ == "__main__":
    unittest.main()
