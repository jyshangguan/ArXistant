"""Tests for the favicon: ArXistant's tab icon in Chrome.

The logo (the red square with a white "A") was simplified for small sizes
and must be reachable at the URLs browsers request automatically
(/favicon.ico) and via the <link> tags injected into every served page.
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


class FaviconTests(unittest.TestCase):
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
            return resp.headers.get("Content-Type"), resp.read()

    def test_favicon_ico_is_served(self):
        content_type, body = self._get("/favicon.ico")
        self.assertEqual(content_type, "image/x-icon")
        self.assertEqual(body[:4], b"\x00\x00\x01\x00")  # ICO directory magic

    def test_favicon_svg_is_served(self):
        content_type, body = self._get("/favicon.svg")
        self.assertEqual(content_type, "image/svg+xml")
        text = body.decode("utf-8")
        self.assertIn("<svg", text)
        self.assertIn("#B31B1B", text)  # the brand red

    def test_apple_touch_icon_is_served(self):
        content_type, body = self._get("/apple-touch-icon.png")
        self.assertEqual(content_type, "image/png")
        self.assertEqual(body[:8], b"\x89PNG\r\n\x1a\n")

    def test_every_html_page_links_the_favicon(self):
        for page in ("/", "/database.html", "/notebook.html", "/settings.html",
                     "/chat.html", "/search.html"):
            with self.subTest(page=page):
                _, body = self._get(page)
                html = body.decode("utf-8")
                self.assertIn('rel="icon"', html)
                self.assertIn('href="/favicon.svg"', html)
                self.assertEqual(html.count('href="/favicon.svg"'), 1)

    def test_injection_is_idempotent(self):
        once = server.ensure_favicon_link("<html><head><title>t</title></head></html>")
        self.assertIn('href="/favicon.svg"', once)
        twice = server.ensure_favicon_link(once)
        self.assertEqual(twice, once)

    def test_page_without_head_is_left_alone(self):
        fragment = "<p>no head here</p>"
        self.assertEqual(server.ensure_favicon_link(fragment), fragment)


if __name__ == "__main__":
    unittest.main()
