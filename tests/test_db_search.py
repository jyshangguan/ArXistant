"""Tests for the Saved Papers page search format and folded tag bar.

The search box accepts plain keywords plus field:value tokens (tag:, author:,
title:, abs:, note:, year:, id:) that AND together; the tag filter bar is
folded by default and only shows every tag when the title is clicked.
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


class ParseFieldedQueryTests(unittest.TestCase):
    def test_plain_terms(self):
        fields, plain = server.parse_fielded_query("quasar feedback")
        self.assertEqual(plain, ["quasar", "feedback"])
        self.assertTrue(all(not v for v in fields.values()))

    def test_fielded_tokens(self):
        fields, plain = server.parse_fielded_query("tag:lrd author:shangguan")
        self.assertEqual(fields["tag"], ["lrd"])
        self.assertEqual(fields["author"], ["shangguan"])
        self.assertEqual(plain, [])

    def test_quoted_value(self):
        fields, plain = server.parse_fielded_query('title:"dark matter"')
        self.assertEqual(fields["title"], ["dark matter"])

    def test_mixed_fields_and_plain(self):
        fields, plain = server.parse_fielded_query("tag:lrd quasar")
        self.assertEqual(fields["tag"], ["lrd"])
        self.assertEqual(plain, ["quasar"])

    def test_year_range_kept_as_value(self):
        fields, plain = server.parse_fielded_query("year:2020-2024")
        self.assertEqual(fields["year"], ["2020-2024"])

    def test_bare_prefix_while_typing_is_dropped(self):
        fields, plain = server.parse_fielded_query("tag:")
        self.assertEqual(fields["tag"], [])
        self.assertEqual(plain, [])

    def test_unknown_prefix_falls_back_to_plain(self):
        fields, plain = server.parse_fielded_query("https://arxiv.org/abs/x")
        self.assertEqual(plain, ["https://arxiv.org/abs/x"])
        self.assertTrue(all(not v for v in fields.values()))

    def test_arxiv_alias_maps_to_id(self):
        fields, plain = server.parse_fielded_query("arXiv:1802.08364")
        self.assertEqual(fields["id"], ["1802.08364"])
        self.assertEqual(plain, [])

    def test_multiple_values_for_same_field(self):
        fields, _ = server.parse_fielded_query("tag:lrd tag:jwst")
        self.assertEqual(fields["tag"], ["lrd", "jwst"])

    def test_case_insensitive_field_names(self):
        fields, _ = server.parse_fielded_query("TAG:lrd Author:Shangguan")
        self.assertEqual(fields["tag"], ["lrd"])
        self.assertEqual(fields["author"], ["Shangguan"])

    def test_empty_query(self):
        fields, plain = server.parse_fielded_query("")
        self.assertEqual(plain, [])
        self.assertTrue(all(not v for v in fields.values()))


class PaperYearTests(unittest.TestCase):
    def test_new_style_id(self):
        self.assertEqual(server.paper_year_from_key("2609.12040"), 2026)
        self.assertEqual(server.paper_year_from_key("1802.08364"), 2018)
        self.assertEqual(server.paper_year_from_key("0704.0001"), 2007)
        self.assertEqual(server.paper_year_from_key("9501.00001"), 1995)

    def test_old_style_id(self):
        self.assertEqual(server.paper_year_from_key("math/0102001"), 2001)
        self.assertEqual(server.paper_year_from_key("astro-ph/9901001"), 1999)

    def test_bibcode(self):
        self.assertEqual(server.paper_year_from_key("1929PNAS...15..168H"), 1929)
        self.assertEqual(server.paper_year_from_key("2020A&A...641A...6P"), 2020)
        self.assertEqual(server.paper_year_from_key("2023arXiv230711273V"), 2023)

    def test_unparseable(self):
        self.assertIsNone(server.paper_year_from_key("test123"))
        self.assertIsNone(server.paper_year_from_key(""))
        self.assertIsNone(server.paper_year_from_key(None))

    def test_year_value_matches(self):
        self.assertTrue(server._year_value_matches(2020, "2020"))
        self.assertFalse(server._year_value_matches(2019, "2020"))
        self.assertTrue(server._year_value_matches(2022, "2020-2024"))
        self.assertTrue(server._year_value_matches(2020, "2020-2024"))
        self.assertTrue(server._year_value_matches(2024, "2020-2024"))
        self.assertFalse(server._year_value_matches(2025, "2020-2024"))
        # reversed range is tolerated
        self.assertTrue(server._year_value_matches(2022, "2024-2020"))
        # non-years never match
        self.assertFalse(server._year_value_matches(2022, "twenty"))
        self.assertFalse(server._year_value_matches(2022, "20"))


PAPERS = [
    {
        "arxiv_id": "2609.00001", "title": "Quasar feedback in the early universe",
        "authors": "Shangguan, Jinyi", "abstract": "Gas content of hosts.",
        "notes": "follow up on LRDs", "tags": "lrd,jwst",
        "relevance_score": 5, "date_fetched": "2026-09-14",
    },
    {
        "arxiv_id": "1802.08364", "title": "Gas content of quasar hosts",
        "authors": "Hubble, Edwin", "abstract": "Feedback loops in nebulae.",
        "notes": "", "tags": "lrd",
        "relevance_score": 3, "date_fetched": "2018-02-01",
    },
    {
        "arxiv_id": "1929PNAS...15..168H", "title": "Extra-galactic nebulae",
        "authors": "Hubble, Edwin", "abstract": "",
        "notes": "classic paper", "tags": "",
        "relevance_score": 0, "date_fetched": "",
    },
]


def _insert_papers(db_path):
    conn = sqlite3.connect(db_path)
    for p in PAPERS:
        conn.execute(
            "INSERT INTO saved_papers (arxiv_id, title, authors, abstract,"
            " relevance_score, date_fetched, notes, tags)"
            " VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (p["arxiv_id"], p["title"], p["authors"], p["abstract"],
             p["relevance_score"], p["date_fetched"], p["notes"], p["tags"]))
    conn.commit()
    conn.close()


class SavedPapersSearchTests(unittest.TestCase):
    """search_saved_papers() over a seeded temporary database."""

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
        _insert_papers(cls.db_path)

    @classmethod
    def tearDownClass(cls):
        for p in cls.patchers:
            p.stop()
        cls.tmp.cleanup()

    def _ids(self, query):
        return [p["arxiv_id"] for p in server.search_saved_papers(query)]

    def test_empty_query_returns_all(self):
        self.assertEqual(len(self._ids("")), len(PAPERS))
        self.assertEqual(len(self._ids("tag:")), len(PAPERS))

    def test_tag_exact_match(self):
        self.assertEqual(self._ids("tag:lrd"),
                         ["2609.00001", "1802.08364"])
        # exact match only — no substring bleed
        self.assertEqual(self._ids("tag:jwst"), ["2609.00001"])
        self.assertEqual(self._ids("tag:lr"), [])

    def test_author_substring(self):
        self.assertEqual(self._ids("author:shangguan"), ["2609.00001"])
        self.assertEqual(self._ids("author:hubble"),
                         ["1802.08364", "1929PNAS...15..168H"])

    def test_title_and_abs_and_note(self):
        self.assertEqual(self._ids("title:quasar"),
                         ["2609.00001", "1802.08364"])
        self.assertEqual(self._ids("abs:feedback"), ["1802.08364"])
        self.assertEqual(self._ids("note:LRD"), ["2609.00001"])  # case-insensitive

    def test_year_single_and_range(self):
        self.assertEqual(self._ids("year:2026"), ["2609.00001"])
        self.assertEqual(self._ids("year:2018"), ["1802.08364"])
        self.assertEqual(self._ids("year:1929"), ["1929PNAS...15..168H"])
        self.assertEqual(self._ids("year:1929-2026"),
                         ["2609.00001", "1802.08364", "1929PNAS...15..168H"])
        self.assertEqual(self._ids("year:2000-2001"), [])

    def test_id_field_and_alias(self):
        self.assertEqual(self._ids("id:1929PNAS"), ["1929PNAS...15..168H"])
        self.assertEqual(self._ids("arXiv:1802.08364"), ["1802.08364"])

    def test_fields_and_together(self):
        self.assertEqual(self._ids("tag:lrd author:hubble"), ["1802.08364"])
        self.assertEqual(self._ids("tag:lrd tag:jwst"), ["2609.00001"])

    def test_field_plus_plain_term(self):
        # p2 carries the tag and has "quasar" in its title too
        self.assertEqual(self._ids("tag:lrd quasar"),
                         ["2609.00001", "1802.08364"])
        self.assertEqual(self._ids("tag:lrd shangguan"), ["2609.00001"])

    def test_plain_terms_and_across_fields(self):
        # both plain terms must match somewhere (AND, not one big substring)
        self.assertEqual(self._ids("hubble nebulae"),
                         ["1802.08364", "1929PNAS...15..168H"])
        # jwst exists only as a tag, never in the plain-text fields
        self.assertEqual(self._ids("hubble jwst"), [])

    def test_plain_term_matches_arxiv_id(self):
        self.assertEqual(self._ids("1802.08364"), ["1802.08364"])

    def test_quoted_plain_phrase(self):
        self.assertEqual(self._ids('"quasar hosts"'), ["1802.08364"])


class SearchEndpointTests(SavedPapersSearchTests):
    """/api/search serves the fielded query over HTTP."""

    @classmethod
    def setUpClass(cls):
        super().setUpClass()
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever,
                                      daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        super().tearDownClass()

    def _search(self, query):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}"
                f"/api/search?q={urllib.request.quote(query)}") as resp:
            return json.loads(resp.read().decode("utf-8"))

    def test_endpoint_returns_fielded_results(self):
        data = self._search("tag:lrd author:hubble")
        self.assertEqual(data["count"], 1)
        self.assertEqual(data["papers"][0]["arxiv_id"], "1802.08364")
        # full rows come back (the page renders notes/tags/abstract)
        self.assertEqual(data["papers"][0]["tags"], "lrd")

    def test_endpoint_empty_query_returns_all(self):
        data = self._search("")
        self.assertEqual(data["count"], len(PAPERS))

    def test_endpoint_preserves_query(self):
        data = self._search("Tag:LRD")
        self.assertEqual(data["query"], "Tag:LRD")
        self.assertEqual(data["count"], 2)


class DatabasePageHtmlTests(unittest.TestCase):
    """The tag bar folds; the search box documents its format."""

    HTML = server.DATABASE_VIEWER_HTML

    def test_tag_bar_is_a_foldable_details_element(self):
        self.assertIn('<details id="tagBar" class="tag-bar">', self.HTML)
        self.assertIn("<summary class=\"tag-bar-title\">", self.HTML)

    def test_tag_bar_folds_by_default(self):
        self.assertIn("let tagBarOpen = false", self.HTML)
        self.assertIn("bar.open = tagBarOpen;", self.HTML)

    def test_fold_state_survives_rerenders(self):
        self.assertIn("addEventListener('toggle'", self.HTML)

    def test_selected_tags_stay_visible_when_folded(self):
        self.assertIn("tag-bar-selected", self.HTML)
        self.assertIn("selectedLabels.join(', ')", self.HTML)

    def test_clear_button_does_not_toggle_the_fold(self):
        # Clear sits inside the summary; the click must be preventDefault'ed
        # so clearing the filter does not also fold the list.
        self.assertIn("e.preventDefault();", self.HTML)

    def test_search_placeholder_mentions_format(self):
        self.assertIn("tag:lrd", self.HTML)
        self.assertIn("author:shangguan", self.HTML)

    def test_search_hint_documents_all_fields(self):
        for field in ("tag:", "author:", "title:", "abs:", "note:",
                      "year:", "id:"):
            self.assertIn(f"<code>{field}</code>", self.HTML)
        self.assertIn("year:2020-2024", self.HTML)
        self.assertIn('title:"dark matter"', self.HTML)

    def test_search_input_is_debounced(self):
        self.assertIn("searchDebounceTimer", self.HTML)


if __name__ == "__main__":
    unittest.main()
