"""Tests for the LLM settings connection test endpoint.

The settings panel must be able to tell the user, right after saving, whether
the stored base URL + model + API key actually work — instead of letting a
missing/stale key surface later as a provider 401 mid-chat.
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

CONFIGURED = {"base_url": "https://llm.example/v1", "model": "test-model"}


class ChatConfigTestEndpointTests(unittest.TestCase):
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

    def test_requires_base_url_and_model(self):
        with mock.patch.object(server, "load_chat_config",
                               return_value={"base_url": "", "model": ""}):
            status, data = self._post("/api/chat/config/test", {})
        self.assertEqual(status, 400)
        self.assertIn("base URL", data["error"])

    def test_reports_missing_api_key(self):
        with mock.patch.object(server, "load_chat_config",
                               return_value=dict(CONFIGURED)), \
                mock.patch.object(server, "get_chat_api_key", return_value=""):
            status, data = self._post("/api/chat/config/test", {})
        self.assertEqual(status, 400)
        self.assertIn("No API key", data["error"])

    def test_auth_failure_gets_actionable_message(self):
        error = urllib.error.HTTPError(
            "https://llm.example/v1/chat/completions", 401,
            "Unauthorized", {}, None)
        with mock.patch.object(server, "load_chat_config",
                               return_value=dict(CONFIGURED)), \
                mock.patch.object(server, "get_chat_api_key",
                                  return_value="stale-key"), \
                mock.patch.object(server, "_chat_completion",
                                  side_effect=error):
            status, data = self._post("/api/chat/config/test", {})
        self.assertEqual(status, 502)
        self.assertIn("401", data["error"])
        self.assertIn("authentication failed", data["error"])

    def test_success_when_provider_answers(self):
        with mock.patch.object(server, "load_chat_config",
                               return_value=dict(CONFIGURED)), \
                mock.patch.object(server, "get_chat_api_key",
                                  return_value="good-key"), \
                mock.patch.object(server, "_chat_completion",
                                  return_value={"choices": []}):
            status, data = self._post("/api/chat/config/test", {})
        self.assertEqual(status, 200)
        self.assertTrue(data["success"])
        self.assertEqual(data["model"], "test-model")


class ChatPageOffersConnectionTest(unittest.TestCase):
    def test_settings_panel_has_test_button_and_autotest_after_save(self):
        html = server.CHAT_PAGE_HTML
        self.assertIn('onclick="testConnection()"', html)
        self.assertIn("/api/chat/config/test", html)
        # Saving settings verifies the credentials immediately afterwards.
        self.assertIn("await testConnection();", html)

    def test_status_shows_where_the_key_comes_from(self):
        html = server.CHAT_PAGE_HTML
        self.assertIn("key in local file", html)
        self.assertIn("key from OS keychain", html)


if __name__ == "__main__":
    unittest.main()
