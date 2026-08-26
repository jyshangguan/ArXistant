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


class LocalPdfReaderTests(unittest.TestCase):
    def _pdf_bytes(self):
        import fitz
        doc = fitz.open()
        for page_no in range(1, 4):
            page = doc.new_page()
            page.insert_textbox(
                fitz.Rect(50, 50, 550, 750),
                (f"Local Reader Test — Page {page_no}\n\n" +
                 "Galaxy formation and black hole accretion are discussed in this paper. " * 18),
                fontsize=11)
        data = doc.tobytes()
        doc.close()
        return data

    def test_ingest_extracts_html_chunks_and_library_record(self):
        with tempfile.TemporaryDirectory() as tmp:
            db_path = os.path.join(tmp, "papers.db")
            docs_dir = os.path.join(tmp, "local_documents")
            with mock.patch.object(arxiv_db_server, "DB_PATH", db_path), \
                 mock.patch.object(arxiv_db_server, "LOCAL_DOCUMENT_DIR", docs_dir):
                arxiv_db_server.init_db()
                item = arxiv_db_server.ingest_local_pdf(self._pdf_bytes(), "reader-test.pdf")
                self.assertTrue(item["document_id"].startswith("local-"))
                self.assertEqual(item["source"], "local")
                self.assertEqual(item["page_count"], 3)
                self.assertGreater(item["chunk_count"], 0)
                pdf_path, html_path = arxiv_db_server._local_document_paths(item["document_id"])
                self.assertTrue(os.path.exists(pdf_path))
                self.assertTrue(os.path.exists(html_path))
                with open(html_path, encoding="utf-8") as f:
                    html = f.read()
                self.assertIn('pdf.min.mjs', html)
                self.assertIn('shell.dataset.page=String(number)', html)
                self.assertIn(item["document_id"], html)
                library = arxiv_db_server.collect_chat_library()
                local_item = next(p for p in library if p.get("source") == "local")
                self.assertEqual(local_item["document_id"], item["document_id"])
                context = arxiv_db_server.local_document_context(
                    item["document_id"], "black hole accretion")
                self.assertTrue(context)
                self.assertIn("black hole accretion", context[0]["text"].lower())

    def test_ingest_rejects_non_pdf(self):
        with self.assertRaisesRegex(ValueError, "not a valid PDF"):
            arxiv_db_server.ingest_local_pdf(b"not a pdf", "bad.pdf")

    def test_ingest_rejects_scanned_pdf(self):
        import fitz
        doc = fitz.open()
        doc.new_page()
        data = doc.tobytes()
        doc.close()
        with self.assertRaisesRegex(ValueError, "scanned PDF"):
            arxiv_db_server.ingest_local_pdf(data, "scan.pdf")

    def test_extraction_falls_back_when_pymupdf_is_missing(self):
        pdf_bytes = self._pdf_bytes()
        with mock.patch.dict(sys.modules, {"fitz": None}):
            pages, _title = arxiv_db_server._extract_pdf_pages(pdf_bytes)
        self.assertEqual(len(pages), 3)
        self.assertIn("Galaxy formation", pages[0])


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
            conn.execute(
                "CREATE TABLE my_publications ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " bibcode TEXT NOT NULL UNIQUE, title TEXT NOT NULL,"
                " authors TEXT, abstract TEXT, keywords TEXT, year TEXT,"
                " date_added TEXT DEFAULT CURRENT_TIMESTAMP)")
            conn.commit()
            import arxistant_sync
            arxistant_sync.migrate_db(conn)
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
            conn.execute(
                "CREATE TABLE my_publications ("
                " id INTEGER PRIMARY KEY AUTOINCREMENT,"
                " bibcode TEXT NOT NULL UNIQUE, title TEXT NOT NULL,"
                " authors TEXT, abstract TEXT, keywords TEXT, year TEXT,"
                " date_added TEXT DEFAULT CURRENT_TIMESTAMP)")
            conn.commit()
            import arxistant_sync
            arxistant_sync.migrate_db(conn)
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

    def test_clean_fulltext_title_drops_inline_thanks(self):
        # arXiv/ar5iv nest \thanks / pub-notes inside the title <h1>; the
        # cleaned <h1> must be just the <title> text (issue: wrong chat title).
        html = (
            "<html><head><title>Real Paper Title</title></head><body>"
            '<h1 class="ltx_title ltx_title_document">Real Paper Title'
            '<span class="ltx_pubnotes"><span class="ltx_note_name">Thanks: </span>'
            'The code can be downloaded from: https://example.com.</span></h1>'
            "<p>body</p></body></html>")
        out = arxiv_db_server.clean_fulltext_title(html)
        import re
        m = re.search(r"<h1[^>]*>(.*?)</h1>", out, re.S)
        self.assertIsNotNone(m)
        self.assertEqual(m.group(1).strip(), "Real Paper Title")
        self.assertNotIn("Thanks", m.group(1))


class WebSearchTests(unittest.TestCase):
    def test_ddg_real_url_decodes_uddg(self):
        href = "//duckduckgo.com/l/?uddg=https%3A%2F%2Fexample.com%2Fa&rut=abc"
        self.assertEqual(
            arxiv_db_server._ddg_real_url(href), "https://example.com/a")

    def test_ddg_real_url_passthrough(self):
        self.assertEqual(
            arxiv_db_server._ddg_real_url("https://x.org/y"), "https://x.org/y")

    def test_clean_text_strips_tags_and_entities(self):
        self.assertEqual(
            arxiv_db_server._clean_text("<b>A &amp; B</b>  c"), "A & B c")

    def test_run_chat_agent_tool_loop(self):
        calls = {"n": 0}
        def fake_completion(base_url, model, messages, temperature, api_key, tools=None):
            calls["n"] += 1
            has_tool = any(m.get("role") == "tool" for m in messages)
            if not has_tool and tools:
                return {"choices": [{"message": {
                    "role": "assistant", "content": "",
                    "tool_calls": [{"id": "c1", "type": "function",
                                    "function": {"name": "web_search",
                                                 "arguments": json.dumps({"query": "q"})}}]}}]}
            return {"choices": [{"message": {"role": "assistant", "content": "done"}}]}
        real_search = arxiv_db_server.web_search
        real_cc = arxiv_db_server._chat_completion
        arxiv_db_server.web_search = lambda q, max_results=6: "1. mock " + q
        arxiv_db_server._chat_completion = fake_completion
        try:
            out = arxiv_db_server.run_chat_agent(
                "http://x/v1", "m", [{"role": "user", "content": "hi"}], 0.7, "k")
        finally:
            arxiv_db_server.web_search = real_search
            arxiv_db_server._chat_completion = real_cc
        self.assertFalse(out["unsupported"])
        self.assertEqual(out["statuses"], ["q"])
        self.assertEqual(out["final"], "done")
        self.assertEqual(calls["n"], 2)


if __name__ == "__main__":
    unittest.main()


class TextToolCallTests(unittest.TestCase):
    DSML = ('Some prose.<｜｜DSML｜｜tool_calls>\n<｜｜DSML｜｜invoke name="web_search">\n'
            '<｜｜DSML｜｜parameter name="query" string="true">[Ne V] black hole mass</｜｜DSML｜｜parameter>\n'
            '</｜｜DSML｜｜invoke>\n<｜｜DSML｜｜tool_calls>')

    def test_extract_dsml_query(self):
        self.assertEqual(
            arxiv_db_server._extract_text_tool_queries(self.DSML),
            ["[Ne V] black hole mass"])

    def test_strip_tool_markup(self):
        self.assertEqual(
            arxiv_db_server._strip_tool_markup(self.DSML), "Some prose.")

    def test_run_agent_handles_text_tool_calls(self):
        calls = {"n": 0}
        def fake(base_url, model, messages, temperature, api_key, tools=None):
            calls["n"] += 1
            if any(m.get("role") == "user" and m.get("content", "").startswith("Web search results") for m in messages):
                return {"choices": [{"message": {"role": "assistant", "content": "Answer from results."}}]}
            return {"choices": [{"message": {"role": "assistant", "content": TextToolCallTests.DSML}}]}
        real_cc, real_ws = arxiv_db_server._chat_completion, arxiv_db_server.web_search
        arxiv_db_server._chat_completion = fake
        arxiv_db_server.web_search = lambda q, max_results=6: "1. mock " + q
        try:
            out = arxiv_db_server.run_chat_agent("http://x/v1", "m", [{"role": "user", "content": "hi"}], 0.7, "k")
        finally:
            arxiv_db_server._chat_completion, arxiv_db_server.web_search = real_cc, real_ws
        self.assertEqual(out["final"], "Answer from results.")
        self.assertEqual(out["statuses"], ["[Ne V] black hole mass"])


class DiscoverHelperTests(unittest.TestCase):
    def test_s2_to_item(self):
        item = arxiv_db_server._s2_to_item({
            "title": "T", "year": 2020, "citationCount": 5,
            "externalIds": {"ArXiv": "2001.00001"},
            "authors": [{"name": "A"}, {"name": "B"}],
            "tldr": {"text": "sum"},
        })
        self.assertEqual(item["arxiv_id"], "2001.00001")
        self.assertEqual(item["authors"], "A, B")
        self.assertEqual(item["citations"], 5)
        self.assertEqual(item["tldr"], "sum")

    def test_fulltext_search(self):
        import tempfile, os
        tmp = tempfile.mkdtemp()
        with open(os.path.join(tmp, "2606.99999.html"), "w") as f:
            f.write("<html><body><p>the accretion disk is bright</p></body></html>")
        real = arxiv_db_server.FULLTEXT_CACHE_DIR
        arxiv_db_server.FULLTEXT_CACHE_DIR = tmp
        try:
            r = arxiv_db_server.fulltext_search("accretion disk")
        finally:
            arxiv_db_server.FULLTEXT_CACHE_DIR = real
        self.assertEqual(len(r), 1)
        self.assertEqual(r[0]["arxiv_id"], "2606.99999")
        self.assertIn("accretion", r[0]["snippet"].lower())


class StreamingAgentPapersTests(unittest.TestCase):
    def test_emits_papers_event_for_search_tool(self):
        events = []
        class Stub:
            def _sse_write(self, obj): events.append(obj)
        calls = {"n": 0}
        def fake_stream(self, base_url, model, msgs, temperature, api_key, use_tools):
            calls["n"] += 1
            if calls["n"] == 1:
                return "", [{"id": "c1", "name": "search_papers",
                             "arguments": json.dumps({"query": "q"})}]
            return "Final answer.", []
        real_stream = arxiv_db_server.Handler._stream_one
        real_s2 = arxiv_db_server.s2_search
        Stub._stream_one = fake_stream
        arxiv_db_server.s2_search = lambda q, limit=10: [
            {"arxiv_id": "1", "title": "T", "year": 2020, "citations": 3,
             "source": "s2", "tldr": "", "authors": ""}]
        try:
            arxiv_db_server.Handler._run_streaming_agent(
                Stub(), [{"role": "user", "content": "find papers"}],
                "u", "m", 0.7, "k")
        finally:
            arxiv_db_server.Handler._stream_one = real_stream
            arxiv_db_server.s2_search = real_s2
            if hasattr(Stub, '_stream_one'): del Stub._stream_one
        statuses = [e.get("status") for e in events if isinstance(e, dict) and e.get("status")]
        self.assertIn("papers", statuses)

    def test_extract_text_tool_calls_dsml(self):
        dsml = ('<｜｜DSML｜｜tool_calls><｜｜DSML｜｜invoke name="search_papers">'
                '<｜｜DSML｜｜parameter name="query" string="true">neutron star</｜｜DSML｜｜parameter>'
                '</｜｜DSML｜｜invoke><｜｜DSML｜｜tool_calls>')
        calls = arxiv_db_server._extract_text_tool_calls(dsml)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "search_papers")
        self.assertEqual(calls[0][1].get("query"), "neutron star")
