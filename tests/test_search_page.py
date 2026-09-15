"""Tests for the Search page: rename, arXiv fielded pass-through, ADS fields.

The page moved from /search-arxiv.html to /search.html (it stopped being
arXiv-only long ago), the arXiv query builder must pass fielded queries
through raw instead of wrapping them in all: (which produced nonsense like
all:au:Shangguan), and ADS queries keep the user's field:value format with
only the useless id: prefix remapped to identifier:.
"""

import json
import sys
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


ARXIV_ENTRY = (b"<?xml version='1.0'?><feed "
               b"xmlns='http://www.w3.org/2005/Atom'><title>q</title>"
               b"<entry><id>http://arxiv.org/abs/1802.08364v1</id>"
               b"<title>Paper</title>"
               b"<summary>Abstract text</summary>"
               b"<published>2018-02-01T00:00:00Z</published>"
               b"<author><name>Shangguan, Jinyi</name></author>"
               b"</entry></feed>")


class BuildArxivQueryTests(unittest.TestCase):
    def test_plain_keywords_wrapped_in_all(self):
        self.assertEqual(server.build_arxiv_search_query("quasar feedback"),
                         "all:quasar feedback")

    def test_plain_quoted_phrase_wrapped_in_all(self):
        self.assertEqual(server.build_arxiv_search_query('"dark matter"'),
                         'all:"dark matter"')

    def test_fielded_query_passes_through_raw(self):
        self.assertEqual(server.build_arxiv_search_query("au:Shangguan"),
                         "au:Shangguan")
        self.assertEqual(server.build_arxiv_search_query('ti:"dark matter"'),
                         'ti:"dark matter"')
        self.assertEqual(server.build_arxiv_search_query("cat:astro-ph.GA"),
                         "cat:astro-ph.GA")

    def test_operator_query_passes_through_raw(self):
        self.assertEqual(
            server.build_arxiv_search_query("ti:quasar ANDNOT abs:radio"),
            "ti:quasar ANDNOT abs:radio")

    def test_combined_fields_pass_through(self):
        q = 'cat:astro-ph.GA AND abs:"star formation"'
        self.assertEqual(server.build_arxiv_search_query(q), q)

    def test_known_prefixes_normalized_to_lowercase(self):
        self.assertEqual(server.build_arxiv_search_query("AU:Shangguan"),
                         "au:Shangguan")

    def test_url_with_colon_is_not_fielded(self):
        # A pasted URL contains "https:" — not an arXiv prefix, so it is
        # treated as a plain keyword.
        self.assertEqual(
            server.build_arxiv_search_query("https://arxiv.org/abs/1802.08364"),
            "all:https://arxiv.org/abs/1802.08364")

    def test_empty(self):
        self.assertEqual(server.build_arxiv_search_query(""), "")
        self.assertEqual(server.build_arxiv_search_query(None), "")


class SearchArxivApiTests(unittest.TestCase):
    def test_fielded_query_reaches_the_api_untouched(self):
        with mock.patch.object(server, "_fetch_with_retries",
                               return_value=ARXIV_ENTRY.decode()) as fr:
            papers = server.search_arxiv_api("au:Shangguan")
        url = fr.call_args[0][0]
        self.assertIn("search_query=au:Shangguan&", url)
        self.assertNotIn("all:", url)
        self.assertEqual(len(papers), 1)
        self.assertEqual(papers[0]["id"], "1802.08364")

    def test_plain_query_is_wrapped_in_all(self):
        with mock.patch.object(server, "_fetch_with_retries",
                               return_value=ARXIV_ENTRY.decode()) as fr:
            server.search_arxiv_api("quasar")
        url = fr.call_args[0][0]
        self.assertIn("search_query=all:quasar&", url)

    def test_quoted_phrase_is_encoded(self):
        with mock.patch.object(server, "_fetch_with_retries",
                               return_value=ARXIV_ENTRY.decode()) as fr:
            server.search_arxiv_api('ti:"dark matter"')
        url = fr.call_args[0][0]
        self.assertIn('search_query=ti:%22dark%20matter%22&', url)


class BuildAdsQueryTests(unittest.TestCase):
    def test_user_example_passes_through_verbatim(self):
        q = 'first_author:"Shangguan" year:2018 abs:"AGN feedback"'
        self.assertEqual(server.build_ads_query(q), q)

    def test_dash_year_range_passes_through(self):
        self.assertEqual(server.build_ads_query("year:2018-2020"),
                         "year:2018-2020")

    def test_id_prefix_is_remapped_to_identifier(self):
        self.assertEqual(server.build_ads_query("id:1802.08364"),
                         "identifier:1802.08364")

    def test_id_remapped_inside_larger_query(self):
        q = "id:1802.08364 year:2018"
        self.assertEqual(server.build_ads_query(q),
                         "identifier:1802.08364 year:2018")

    def test_lowercase_id_is_remapped(self):
        self.assertEqual(server.build_ads_query("ID:1802.08364"),
                         "identifier:1802.08364")

    def test_arxiv_prefix_is_not_touched(self):
        # "arXiv:" must not be mangled by the id: remap.
        self.assertEqual(server.build_ads_query("arXiv:1802.08364"),
                         "arXiv:1802.08364")

    def test_word_containing_id_is_not_touched(self):
        self.assertEqual(server.build_ads_query("rotation"),
                         "rotation")
        self.assertEqual(server.build_ads_query("abs:tidal"),
                         "abs:tidal")

    def test_plain_keywords_unchanged(self):
        self.assertEqual(server.build_ads_query("quasar feedback"),
                         "quasar feedback")

    def test_empty(self):
        self.assertEqual(server.build_ads_query(""), "")
        self.assertEqual(server.build_ads_query(None), "")


class SearchPageEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()

    def test_search_html_serves_the_page_with_injected_buttons(self):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}/search.html") as resp:
            self.assertEqual(resp.status, 200)
            html = resp.read().decode("utf-8")
        self.assertIn("Search Papers", html)
        # The injected action scripts (the save script has no marker comment;
        # its presence is the attach function it defines).
        self.assertIn("arxistantAttachSaveButtons", html)
        self.assertIn("arxistantAttachChatLinks", html)

    def test_old_address_redirects_permanently(self):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.port, timeout=10)
        try:
            conn.request("GET", "/search-arxiv.html")
            resp = conn.getresponse()
            self.assertEqual(resp.status, 301)
            self.assertEqual(resp.getheader("Location"), "/search.html")
        finally:
            conn.close()

    def test_ads_endpoint_maps_id_to_identifier(self):
        ads_response = json.dumps({"response": {"numFound": 1, "docs": [
            {"title": ["T"], "author": ["A"], "abstract": "B",
             "bibcode": "2018ApJ...854..158S", "year": "2018",
             "identifier": ["arXiv:1802.08364"], "citation_count": 5}]}})
        with mock.patch.object(server, "load_ads_token",
                               return_value="tok"), \
                mock.patch.object(server, "_fetch_with_retries",
                                  return_value=ads_response) as fr:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}"
                    "/api/ads/search?q=id%3A1802.08364") as resp:
                data = json.loads(resp.read().decode("utf-8"))
        url = fr.call_args[0][0]
        self.assertIn("q=identifier%3A1802.08364", url)
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["papers"][0]["id"], "1802.08364")

    def test_ads_endpoint_passes_fielded_query_verbatim(self):
        ads_response = json.dumps({"response": {"numFound": 1, "docs": [
            {"title": ["T"], "author": ["A"], "abstract": "B",
             "bibcode": "b", "year": "2018", "identifier": []}]}})
        q = 'first_author:"Shangguan" year:2018 abs:"AGN feedback"'
        with mock.patch.object(server, "load_ads_token",
                               return_value="tok"), \
                mock.patch.object(server, "_fetch_with_retries",
                                  return_value=ads_response) as fr:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{self.port}/api/ads/search?q="
                    + urllib.request.quote(q)) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        url = fr.call_args[0][0]
        # urlencode (quote_plus): ':' -> %3A, '"' -> %22, ' ' -> '+'
        self.assertIn(
            "q=first_author%3A%22Shangguan%22+year%3A2018"
            "+abs%3A%22AGN+feedback%22", url)
        self.assertEqual(data["count"], 1)


class SearchPageHtmlTests(unittest.TestCase):
    HTML = server.SEARCH_HTML

    def test_page_is_renamed(self):
        self.assertIn("<h1>🔍 Search Papers</h1>", self.HTML)
        self.assertIn("<title>Search Papers</title>", self.HTML)
        self.assertNotIn("Search arXiv", self.HTML)

    def test_syntax_hint_swaps_with_selected_source(self):
        self.assertIn('id="syntaxHint"', self.HTML)
        self.assertIn("SYNTAX_HINTS", self.HTML)
        self.assertIn("updateSyntaxHint()", self.HTML)
        self.assertIn("addEventListener('change', updateSyntaxHint)",
                      self.HTML)

    def test_arxiv_examples_are_shown(self):
        for example in ("au:", "ti:", "abs:", "cat:",
                        'ti:"dark matter"', "ANDNOT"):
            self.assertIn(example, self.HTML)

    def test_ads_examples_are_shown(self):
        for example in ("first_author:", "author:", "title:", "abs:",
                        "year:2018", "year:2018-2020", "arXiv:1802.08364",
                        "bibcode:", "property:refereed",
                        'first_author:"Shangguan" year:2018 abs:"AGN feedback"'):
            self.assertIn(example, self.HTML)

    def test_placeholder_mentions_fielded_format(self):
        self.assertIn("au:&quot;Shangguan&quot;", self.HTML)
        self.assertIn("first_author:&quot;Shangguan&quot;", self.HTML)

    def test_navigation_links_use_the_new_address(self):
        for page in (server.DATABASE_VIEWER_HTML, server.CHAT_PAGE_HTML,
                     server.PUBLICATIONS_VIEWER_HTML):
            self.assertIn('href="/search.html"', page)
            self.assertNotIn("/search-arxiv.html", page)
        # The mobile menu builds its items in JavaScript.
        self.assertIn("href: '/search.html'", server.MOBILE_MENU_SCRIPT)
        self.assertNotIn("/search-arxiv.html", server.MOBILE_MENU_SCRIPT)

    def test_extension_popup_links_to_the_new_address(self):
        popup_js = (PROJECT_ROOT / "chrome-extension" / "popup.js").read_text(
            encoding="utf-8")
        popup_html = (PROJECT_ROOT / "chrome-extension" /
                      "popup.html").read_text(encoding="utf-8")
        self.assertIn("openPage('/search.html')", popup_js)
        self.assertNotIn("/search-arxiv.html", popup_js + popup_html)
        # the label is renamed wherever it appears
        self.assertNotIn("Search arXiv", popup_html)

    def test_ranker_generated_page_uses_the_new_address(self):
        import arxiv_ml_ranker
        self.assertIn('<a href="/search.html">🔍 Search</a>',
                      arxiv_ml_ranker.ML_FEATURES_HTML_TEMPLATE
                      if hasattr(arxiv_ml_ranker, "ML_FEATURES_HTML_TEMPLATE")
                      else self._ranker_source())

    def _ranker_source(self):
        return (PROJECT_ROOT / "src" / "arxiv_ml_ranker.py").read_text(
            encoding="utf-8")


if __name__ == "__main__":
    unittest.main()
