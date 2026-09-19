"""Release version and API-version consistency.

The product version is repeated across the extension manifest, the Android
gradle file, the deb builder, the outbound User-Agent strings, and the docs.

The API compatibility version is a separate handshake that must agree in three
places: the server announces it at /api/health, the extension popup requires it
to report the server as running, and start_server.sh uses it to decide whether a
running server is current enough to keep or must be killed and replaced. If
those three drift apart, either a stale server looks healthy while missing new
endpoints, or a perfectly good server is reported as outdated.

Bumping a release means editing the three constants below.
"""

import json
import re
import sys
import threading
import unittest
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import arxiv_db_server as server

VERSION = "0.4.2"
VERSION_CODE = 10
API_VERSION = 4


class ProductVersionTests(unittest.TestCase):
    def test_extension_manifest_version(self):
        manifest = json.loads(
            (PROJECT_ROOT / "chrome-extension" / "manifest.json").read_text(
                encoding="utf-8"))
        self.assertEqual(manifest["version"], VERSION)

    def test_android_version_name_and_code(self):
        gradle = (PROJECT_ROOT / "android" / "app" / "build.gradle").read_text(
            encoding="utf-8")
        self.assertIn(f'versionName "{VERSION}"', gradle)
        m = re.search(r"versionCode\s+(\d+)", gradle)
        self.assertIsNotNone(m)
        # versionCode must increase or Android refuses to install the update,
        # and the in-app update check would never see the new build.
        self.assertEqual(int(m.group(1)), VERSION_CODE)

    def test_deb_builder_default_version(self):
        script = (PROJECT_ROOT / "packaging" / "linux" /
                  "build-deb.sh").read_text(encoding="utf-8")
        self.assertIn("VERSION=${1:-" + VERSION + "}", script)

    def test_user_agent_strings_carry_the_current_version(self):
        for rel in ("src/arxiv_db_server.py", "src/arxiv_daily_ranker_html.py"):
            text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
            self.assertIn(f"ArXistant/{VERSION}", text, rel)

    def test_no_stale_user_agent_version_remains(self):
        # A leftover older User-Agent is easy to miss and misattributes
        # requests in provider logs.
        stale = re.compile(r"ArXistant/\d+\.\d+\.\d+")
        for rel in ("src/arxiv_db_server.py", "src/arxiv_daily_ranker_html.py"):
            text = (PROJECT_ROOT / rel).read_text(encoding="utf-8")
            found = {m.group(0) for m in stale.finditer(text)}
            self.assertEqual(found, {f"ArXistant/{VERSION}"}, rel)

    def test_readme_states_the_current_version(self):
        readme = (PROJECT_ROOT / "README.md").read_text(encoding="utf-8")
        self.assertIn(f"version **{VERSION}**", readme)

    def test_docs_agree_on_the_android_version_code(self):
        android_doc = (PROJECT_ROOT / "docs" / "android.md").read_text(
            encoding="utf-8")
        self.assertIn(f'**v{VERSION}**', android_doc)
        self.assertIn(f"versionCode {VERSION_CODE}", android_doc)


class ApiVersionHandshakeTests(unittest.TestCase):
    """The three declarations of the API version must agree."""

    def test_server_constant(self):
        self.assertEqual(server.SERVER_API_VERSION, API_VERSION)

    def test_extension_popup_agrees_with_the_server(self):
        popup = (PROJECT_ROOT / "chrome-extension" / "popup.js").read_text(
            encoding="utf-8")
        m = re.search(r"const SERVER_API_VERSION = (\d+);", popup)
        self.assertIsNotNone(m, "popup.js no longer declares SERVER_API_VERSION")
        self.assertEqual(int(m.group(1)), server.SERVER_API_VERSION)

    def test_launcher_agrees_with_the_server(self):
        script = (PROJECT_ROOT / "start_server.sh").read_text(encoding="utf-8")
        m = re.search(r'SERVER_API_VERSION="(\d+)"', script)
        self.assertIsNotNone(
            m, "start_server.sh no longer declares SERVER_API_VERSION")
        self.assertEqual(int(m.group(1)), server.SERVER_API_VERSION)

    def test_health_endpoint_announces_the_expected_version(self):
        # This is the exact value the popup and the launcher compare against.
        httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.Handler)
        port = httpd.server_address[1]
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        try:
            with urllib.request.urlopen(
                    f"http://127.0.0.1:{port}/api/health", timeout=15) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        finally:
            httpd.shutdown()
        self.assertTrue(data["success"])
        self.assertEqual(data["api_version"], API_VERSION)


if __name__ == "__main__":
    unittest.main()
