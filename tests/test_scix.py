"""Tests for SciX (scixplorer.org) paper support — the dev_sci core.

Papers browsed on scixplorer.org are keyed by their ADS bibcode when the
record has no arXiv ID (journal-only papers); arXiv papers keep their arXiv
ID as the key so one paper can never be stored twice. These tests pin the
key rule, the resolver, the ADS-token settings endpoints, the /api/save
re-keying guard, and the abstract-card reader route.
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


def _doc(**overrides):
    """A minimal published SciX record (journal-only, no arXiv version)."""
    doc = {
        "bibcode": "1929PNAS...15..168H",
        "title": ["A Relation between Distance and Radial Velocity among "
                  "Extra-Galactic Nebulae"],
        "author": ["Hubble, Edwin"],
        "abstract": "Determinations of the motion of the sun with respect to "
                    "the extra-galactic nebulae.",
        "identifier": ["10.1073/pnas.15.3.168", "1929PNAS...15..168H"],
        "year": "1929",
        "citation_count": 1275,
        "pubdate": "1929-03-00",
    }
    doc.update(overrides)
    return doc


class KeyRuleTests(unittest.TestCase):
    """The storage-key rule: arXiv ID when present, else the bibcode."""

    def test_is_arxiv_id(self):
        self.assertTrue(server.is_arxiv_id("2307.11273"))
        self.assertTrue(server.is_arxiv_id("2307.11273v2"))
        self.assertTrue(server.is_arxiv_id("math/0102001"))
        self.assertFalse(server.is_arxiv_id("1929PNAS...15..168H"))
        self.assertFalse(server.is_arxiv_id("2023arXiv230711273V"))
        self.assertFalse(server.is_arxiv_id(""))
        self.assertFalse(server.is_arxiv_id(None))

    def test_arxiv_bibcode_converts_to_id(self):
        self.assertEqual(server.arxiv_id_from_bibcode("2023arXiv230711273V"),
                         "2307.11273")
        self.assertEqual(server.arxiv_id_from_bibcode("2018arXiv180706209P"),
                         "1807.06209")
        self.assertEqual(server.arxiv_id_from_bibcode("1929PNAS...15..168H"), "")
        self.assertEqual(server.arxiv_id_from_bibcode(""), "")

    def test_doc_with_arxiv_identifier_uses_arxiv_id(self):
        doc = _doc(bibcode="2020A&A...641A...6P",
                   identifier=["2018arXiv180706209P", "2020A&A...641A...6P",
                               "arXiv:1807.06209"])
        self.assertEqual(server.arxiv_id_from_doc(doc), "1807.06209")
        self.assertEqual(server.paper_key_for_doc(doc), "1807.06209")

    def test_doc_with_versioned_arxiv_identifier_strips_version(self):
        doc = _doc(identifier=["arXiv:2307.11273v2", "2023arXiv230711273V"])
        self.assertEqual(server.arxiv_id_from_doc(doc), "2307.11273")

    def test_doc_arxiv_field_only(self):
        doc = _doc(arxiv="2307.11273v3")
        self.assertEqual(server.arxiv_id_from_doc(doc), "2307.11273")

    def test_journal_only_doc_uses_bibcode(self):
        doc = _doc()
        self.assertEqual(server.arxiv_id_from_doc(doc), "")
        self.assertEqual(server.paper_key_for_doc(doc), "1929PNAS...15..168H")

    def test_arxiv_bibcode_doc_converts(self):
        # An arXiv record whose identifier list only carries the bibcode.
        doc = _doc(bibcode="2023arXiv230711273V",
                   identifier=["2023arXiv230711273V", "arXiv:2307.11273"])
        self.assertEqual(server.paper_key_for_doc(doc), "2307.11273")
        doc2 = _doc(bibcode="2023arXiv230711273V", identifier=[])
        self.assertEqual(server.paper_key_for_doc(doc2), "2307.11273")

    def test_scix_paper_from_doc_shape(self):
        doc = _doc()
        paper = server.scix_paper_from_doc(doc)
        self.assertEqual(paper["id"], "1929PNAS...15..168H")
        self.assertEqual(paper["bibcode"], "1929PNAS...15..168H")
        self.assertEqual(paper["source"], "scix")
        self.assertFalse(paper["is_arxiv"])
        self.assertEqual(paper["year"], "1929")
        self.assertEqual(paper["citation_count"], 1275)
        self.assertEqual(paper["authors"], ["Hubble, Edwin"])

    def test_scix_paper_year_falls_back_to_pubdate(self):
        paper = server.scix_paper_from_doc(_doc(year=""))
        self.assertEqual(paper["year"], "1929")


class ScixResolveTests(unittest.TestCase):
    def _api_response(self, docs):
        return json.dumps({"response": {"numFound": len(docs), "docs": docs}})

    def test_resolves_journal_only_record_to_bibcode_key(self):
        with mock.patch.object(server, "load_ads_token", return_value="tok"), \
                mock.patch.object(server, "_fetch_with_retries",
                                  return_value=self._api_response([_doc()])) as fr:
            paper = server.scix_resolve("1929PNAS...15..168H")
        self.assertEqual(paper["id"], "1929PNAS...15..168H")
        url = fr.call_args[0][0]
        self.assertIn("identifier%3A%221929PNAS", url.replace("%22", "%22"))
        self.assertIn("api.scixplorer.org", url)

    def test_resolves_arxiv_identifier_to_arxiv_id_key(self):
        doc = _doc(bibcode="2020A&A...641A...6P",
                   identifier=["2018arXiv180706209P", "2020A&A...641A...6P",
                               "arXiv:1807.06209"])
        with mock.patch.object(server, "load_ads_token", return_value="tok"), \
                mock.patch.object(server, "_fetch_with_retries",
                                  return_value=self._api_response([doc])):
            paper = server.scix_resolve("2020A&A...641A...6P")
        self.assertEqual(paper["id"], "1807.06209")
        self.assertEqual(paper["bibcode"], "2020A&A...641A...6P")
        self.assertTrue(paper["is_arxiv"])

    def test_requires_exact_identifier_match(self):
        # A doc that merely mentions the identifier must not be accepted.
        with mock.patch.object(server, "load_ads_token", return_value="tok"), \
                mock.patch.object(
                    server, "_fetch_with_retries",
                    return_value=self._api_response(
                        [_doc(bibcode="1998AJ....116.1009R",
                              identifier=["1998AJ....116.1009R"])])):
            self.assertIsNone(server.scix_resolve("1929PNAS...15..168H"))

    def test_no_match_returns_none(self):
        with mock.patch.object(server, "load_ads_token", return_value="tok"), \
                mock.patch.object(server, "_fetch_with_retries",
                                  return_value=self._api_response([])):
            self.assertIsNone(server.scix_resolve("1900AAA....1....1Z"))

    def test_missing_token_raises(self):
        with mock.patch.object(server, "load_ads_token", return_value=None):
            with self.assertRaises(RuntimeError) as ctx:
                server.scix_resolve("1929PNAS...15..168H")
            self.assertIn("ADS token", str(ctx.exception))

    def test_malformed_identifier_rejected(self):
        for bad in ("", "with space", 'quote"attack', "a/b/c/d"):
            with self.assertRaises(ValueError):
                server.scix_resolve(bad)

    def test_test_ads_token_translates_401(self):
        with mock.patch.object(server, "load_ads_token", return_value="bad"), \
                mock.patch.object(
                    server, "_fetch_with_retries",
                    side_effect=RuntimeError(
                        "SciX did not respond after 3 attempts "
                        "(HTTP 401: Unauthorized).")):
            with self.assertRaises(RuntimeError) as ctx:
                server.test_ads_token()
            self.assertIn("rejected", str(ctx.exception))


class ScixEndpointTests(unittest.TestCase):
    """HTTP behavior of /api/scix/resolve and the ADS-token endpoints."""

    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        cls.token_path = os.path.join(cls.tmp.name, "ads_token.txt")
        cls.db_path = os.path.join(cls.tmp.name, "arxiv_papers.db")

        cls.patchers = [
            mock.patch.object(server, "ADS_TOKEN_PATH", cls.token_path),
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
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        for p in cls.patchers:
            p.stop()
        cls.tmp.cleanup()

    def _get_status(self, path):
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}{path}") as resp:
                return (resp.status, json.loads(resp.read().decode("utf-8")),
                        resp.headers)
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read().decode("utf-8")), e.headers

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

    # ---- resolve endpoint ----

    def test_resolve_success(self):
        with mock.patch.object(server, "load_ads_token", return_value="tok"), \
                mock.patch.object(
                    server, "scix_resolve",
                    return_value=server.scix_paper_from_doc(_doc())):
            status, body, _ = self._get_status(
                "/api/scix/resolve?q=1929PNAS...15..168H")
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        self.assertEqual(body["paper"]["id"], "1929PNAS...15..168H")

    def test_resolve_canonicalizes_arxiv_bibcode_keys(self):
        with mock.patch.object(server, "scix_resolve", return_value=None) as resolve:
            status, body, _ = self._get_status(
                "/api/scix/resolve?q=2023arXiv230711273V")
        self.assertEqual(status, 404)
        resolve.assert_called_once_with("2307.11273")

    def test_resolve_not_found_is_404(self):
        with mock.patch.object(server, "load_ads_token", return_value="tok"), \
                mock.patch.object(server, "scix_resolve", return_value=None):
            status, body, _ = self._get_status("/api/scix/resolve?q=1900AAA")
        self.assertEqual(status, 404)
        self.assertFalse(body["success"])

    def test_resolve_without_token_is_503(self):
        with mock.patch.object(server, "scix_resolve",
                               side_effect=RuntimeError(
                                   "No ADS token configured")):
            status, body, _ = self._get_status("/api/scix/resolve?q=x")
        self.assertEqual(status, 503)
        self.assertIn("ADS token", body["error"])

    def test_resolve_missing_query_is_400(self):
        status, body, _ = self._get_status("/api/scix/resolve")
        self.assertEqual(status, 400)

    # ---- ADS token endpoints ----

    def test_token_roundtrip_save_clear_and_status(self):
        status, body = self._get_status("/api/ads/token")[:2]
        self.assertEqual((status, body["has_token"]), (200, False))

        status, body = self._post("/api/ads/token", {"token": "  abc123  "})
        self.assertEqual(status, 200)
        self.assertTrue(body["has_token"])
        with open(self.token_path) as f:
            self.assertEqual(f.read(), "abc123")
        self.assertEqual(os.stat(self.token_path).st_mode & 0o777, 0o600)

        status, body = self._get_status("/api/ads/token")[:2]
        self.assertTrue(body["has_token"])

        status, body = self._post("/api/ads/token", {"token": ""})
        self.assertEqual(status, 200)
        self.assertFalse(body["has_token"])
        self.assertFalse(os.path.exists(self.token_path))

    def test_token_test_success_and_failure(self):
        self._post("/api/ads/token", {"token": "good"})
        with mock.patch.object(server, "test_ads_token", return_value=36902513):
            status, body = self._post("/api/ads/token/test", {})
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        self.assertEqual(body["num_found"], 36902513)

        with mock.patch.object(
                server, "test_ads_token",
                side_effect=RuntimeError("SciX rejected the token (HTTP 401)")):
            status, body = self._post("/api/ads/token/test", {})
        self.assertEqual(status, 502)
        self.assertIn("rejected", body["error"])

    # ---- save guard ----

    def test_save_rekeys_arxiv_bibcode_to_arxiv_id(self):
        status, body = self._post("/api/save", {
            "arxiv_id": "2023arXiv230711273V",
            "title": "Re-keyed", "authors": "A", "abstract": "B",
        })
        self.assertEqual(status, 200)
        self.assertTrue(body["success"])
        conn = sqlite3.connect(self.db_path)
        try:
            rows = [r[0] for r in conn.execute(
                "SELECT arxiv_id FROM saved_papers").fetchall()]
        finally:
            conn.close()
        self.assertIn("2307.11273", rows)
        self.assertNotIn("2023arXiv230711273V", rows)

    def test_save_bibcode_key_persists_as_is(self):
        self._post("/api/save", {
            "arxiv_id": "1929PNAS...15..168H",
            "title": "Hubble", "authors": "Hubble, Edwin",
            "abstract": "Recession of the nebulae.",
        })
        conn = sqlite3.connect(self.db_path)
        try:
            row = conn.execute(
                "SELECT title, authors FROM saved_papers WHERE arxiv_id = ?",
                ("1929PNAS...15..168H",)).fetchone()
        finally:
            conn.close()
        self.assertEqual(row[0], "Hubble")

    def test_save_without_key_is_rejected(self):
        status, body = self._post("/api/save", {"title": "No id"})
        self.assertEqual(status, 400)
        self.assertIn("identifier", body["error"])

    # ---- abstract-card reader route ----

    def test_fulltext_serves_abstract_card_for_bibcode_key(self):
        self._post("/api/save", {
            "arxiv_id": "1929PNAS...15..168H",
            "title": "Hubble", "authors": "Hubble, Edwin",
            "abstract": "Determinations of the motion of the sun with respect "
                        "to the extra-galactic nebulae.",
        })
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}"
                "/api/chat/fulltext?arxiv_id=1929PNAS...15..168H") as resp:
            html = resp.read().decode("utf-8")
        self.assertIn('data-arx-abstract="true"', html)
        self.assertIn("Hubble", html)
        self.assertIn("scixplorer.org/abs/1929PNAS", html)
        self.assertIn("Abstract-only", html)

    def test_fulltext_resolves_unsaved_bibcode_paper(self):
        paper = server.scix_paper_from_doc(_doc())
        with mock.patch.object(server, "scix_resolve", return_value=paper) as resolve:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}"
                    "/api/chat/fulltext?arxiv_id=1929PNAS...15..168H") as resp:
                html = resp.read().decode("utf-8")
        resolve.assert_called_once_with("1929PNAS...15..168H")
        self.assertIn("data-arx-abstract", html)
        self.assertIn("Hubble", html)

    def test_fulltext_canonicalizes_arxiv_bibcode(self):
        with mock.patch.object(
                server, "fetch_paper_fulltext",
                side_effect=ValueError("not found")) as fpf:
            status, body, _ = self._get_status(
                "/api/chat/fulltext?arxiv_id=2023arXiv230711273V")
        self.assertEqual(status, 502)
        fpf.assert_called_once_with("2307.11273")


class AbstractCardHtmlTests(unittest.TestCase):
    def test_card_is_selectable_and_marked_ready(self):
        html = server.scix_abstract_card_html(
            "1929PNAS...15..168H",
            paper={"title": "T", "authors": ["A"], "abstract": "AB",
                   "bibcode": "1929PNAS...15..168H", "year": "1929"})
        self.assertIn('data-arx-abstract="true"', html)
        self.assertIn("1929PNAS", html)
        self.assertIn("Abstract-only", html)

    def test_error_card(self):
        html = server.scix_abstract_card_html("x", error="No token")
        self.assertIn("Could not load", html)
        self.assertIn('data-arx-abstract="true"', html)

    def test_card_escapes_malicious_content(self):
        html = server.scix_abstract_card_html(
            "<script>", paper={"title": "<script>alert(1)</script>",
                               "abstract": "<img onerror=x>"})
        self.assertNotIn("<script>alert", html)
        self.assertIn("&lt;script&gt;", html)


class ScixUiHtmlTests(unittest.TestCase):
    """The chat and search pages must handle bibcode-keyed papers."""

    def test_chat_page_has_scix_fallback_and_labels(self):
        html = server.CHAT_PAGE_HTML
        self.assertIn("/api/scix/resolve", html)
        self.assertIn("isArxivId", html)
        self.assertIn("scix", html)  # SOURCE_LABELS entry
        self.assertIn("arxAbstract", html)  # abstract-card load-poll check
        self.assertIn("https://scixplorer.org/abs/", html)

    def test_search_page_emits_data_paper_id(self):
        html = server.SEARCH_HTML
        self.assertIn("data-paper-id", html)
        self.assertIn("p.is_arxiv", html)

    def test_injected_scripts_prefer_data_paper_id(self):
        for script in (server.SAVE_BUTTON_SCRIPT, server.CHAT_LINK_SCRIPT):
            self.assertIn("dataset.paperId", script)
        # tag script shares paperMeta()
        self.assertIn("dataset.paperId", server.SAVE_BUTTON_SCRIPT)


class AdsSearchKeyRuleTests(unittest.TestCase):
    def test_ads_search_applies_key_rule(self):
        docs = {"response": {"docs": [
            _doc(bibcode="2020A&A...641A...6P",
                 identifier=["arXiv:1807.06209", "2020A&A...641A...6P"]),
            _doc(),  # journal-only
        ]}}
        with mock.patch.object(
                server, "_fetch_with_retries",
                return_value=json.dumps(docs)) as fr:
            papers = server.search_ads_api("planck", "tok")
        self.assertEqual(papers[0]["id"], "1807.06209")
        self.assertTrue(papers[0]["is_arxiv"])
        self.assertEqual(papers[1]["id"], "1929PNAS...15..168H")
        self.assertFalse(papers[1]["is_arxiv"])
        url = fr.call_args[0][0]
        self.assertIn("identifier", url)


if __name__ == "__main__":
    unittest.main()
