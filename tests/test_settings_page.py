"""Tests for the server-rendered Settings page (/settings.html).

The Chrome extension's options page configures the LLM on the desktop, but
Android (and any browser without the extension) had no settings surface at
all — the LLM behind Chat and the Listen voice digests could not be
configured on the phone. The Settings page offers the three server-side
settings surfaces (LLM, Voice Reading, Cloud Sync) in one place.
"""

import sys
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import arxiv_db_server as server


class SettingsPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        cls.port = cls.httpd.server_address[1]
        cls.thread = threading.Thread(target=cls.httpd.serve_forever, daemon=True)
        cls.thread.start()

    @classmethod
    def tearDownClass(cls):
        cls.httpd.shutdown()
        cls.httpd.server_close()

    def _get(self, path):
        with urllib.request.urlopen(
                f"http://127.0.0.1:{self.port}{path}", timeout=15) as resp:
            return resp.read().decode("utf-8")

    def test_settings_page_is_served_with_all_three_sections(self):
        html = self._get("/settings.html")
        self.assertIn("⚙️ Settings", html)
        # LLM section drives the same endpoints as the extension options.
        self.assertIn("llm-base-url", html)
        self.assertIn("/api/chat/config", html)
        self.assertIn("/api/chat/config/test", html)
        # Voice Reading section.
        self.assertIn("voice-role", html)
        self.assertIn("/api/tts/config", html)
        # Cloud Sync section is embedded (shared with /cloud-sync.html).
        self.assertIn("btn-connect", html)
        self.assertIn("/api/cloud/settings", html)

    def test_settings_page_is_mobile_friendly(self):
        # The "..." menu is injected automatically and links the page.
        html = self._get("/settings.html")
        self.assertIn("arx-menu-btn", html)
        self.assertIn('/settings.html', server.MOBILE_MENU_SCRIPT)

    def test_cloud_sync_page_survived_the_refactor(self):
        # The Cloud Sync form was extracted into a shared section; the
        # standalone page must still render it exactly once, with its nav.
        html = self._get("/cloud-sync.html")
        self.assertIn("☁️ Cloud Sync", html)
        self.assertEqual(html.count('id="provider"'), 1)
        self.assertIn("btn-connect", html)
        self.assertIn("/daily.html", html)

    def test_settings_and_cloud_pages_do_not_duplicate_ids(self):
        # The shared section defines ids once per page; the settings page's
        # own ids must not collide with the cloud section's.
        settings = self._get("/settings.html")
        self.assertEqual(settings.count('id="llm-base-url"'), 1)
        self.assertEqual(settings.count('id="status"'), 1)
        self.assertEqual(settings.count('id="provider"'), 1)
        cloud = self._get("/cloud-sync.html")
        # The standalone page has none of the settings-only ids.
        self.assertEqual(cloud.count('id="llm-base-url"'), 0)
        self.assertEqual(cloud.count('id="voice-role"'), 0)
        self.assertEqual(cloud.count('id="provider"'), 1)


if __name__ == "__main__":
    unittest.main()
