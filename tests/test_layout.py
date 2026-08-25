"""Regression tests for the daily/recent page layout tweaks.

Covers: undated titles (date kept in data-date), the refresh pill pinned
next to the "..." menu button, pull-down refresh on all platforms, and
the "..." menu offering Daily Papers off the daily/recent pages.
"""

import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import arxiv_db_server as server
import arxiv_daily_ranker_html as ranker


def _render(page_type):
    paper = {
        "id": "2508.11111",
        "title": "A Test Paper",
        "abstract": "An abstract long enough.",
        "authors": ["A. Author"],
        "section": "New submissions",
    }
    return ranker.format_paper_list_html(
        [(80, 8.0, paper)], date_str="2026-08-25", page_type=page_type)


class DailyRecentLayoutTests(unittest.TestCase):
    def test_titles_carry_no_date_but_keep_it_as_data(self):
        html = _render("new")
        # The daily page's data-date is the arXiv release date, not the
        # passed-in date_str, so compute it rather than hard-coding.
        release = ranker.get_arxiv_release_date()
        self.assertIn("<title>arXiv Astro-ph New Papers</title>", html)
        self.assertIn(f'<h1 data-date="{release}">arXiv Astro-ph New Papers</h1>', html)
        self.assertNotIn("arXiv Astro-ph New Papers —", html)

        recent = _render("recent")
        self.assertIn("<title>arXiv Astro-ph Recent Papers</title>", recent)
        self.assertIn('<h1 data-date="2026-08-25">arXiv Astro-ph Recent Papers</h1>', recent)

    def test_refresh_pill_pinned_next_to_menu_button(self):
        html = _render("new")
        # The "..." menu button sits at top: 12px; right: 12px; width: 46px,
        # so the refresh pill is pinned at right: 66px on the same row.
        self.assertIn("top: 12px; right: 66px", html)
        self.assertIn('id="refreshBtn"', html)
        # No legacy nav bar wrapper around the refresh button.
        self.assertNotIn('class="nav-bar"', html)

    def test_pull_down_refresh_enabled_on_all_platforms(self):
        html = _render("new")
        self.assertNotIn("IS_ANDROID", html)
        self.assertIn("touchstart", html)
        self.assertIn("refreshDaily(true)", html)
        self.assertIn("refreshRecent(true)", html)

    def test_save_script_reads_date_from_data_attribute(self):
        html = _render("new")
        self.assertIn("h1.getAttribute('data-date')", html)


class OverflowMenuTests(unittest.TestCase):
    def test_menu_offers_daily_papers_off_the_ranked_pages(self):
        script = server.MOBILE_MENU_SCRIPT
        self.assertIn("var onDaily", script)
        # Recent Papers only on the daily page; Daily Papers everywhere else.
        self.assertIn("label: 'Recent Papers'", script)
        self.assertIn("label: 'Daily Papers'", script)


if __name__ == "__main__":
    unittest.main()
