"""Tests for the Chrome-extension side of the SciXplorer integration.

The overlay on scixplorer.org is the project's first content-script
injection, and the ADS-token box is the settings page's newest credential
section. These tests pin the manifest wiring, the background relays, the
settings-page handlers, and (when Node is available) the content script's
route regex and syntax.
"""

import json
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXT = PROJECT_ROOT / "chrome-extension"

MANIFEST = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
CONTENT_JS = (EXT / "content-scix.js").read_text(encoding="utf-8")
BACKGROUND_JS = (EXT / "background.js").read_text(encoding="utf-8")
OPTIONS_JS = (EXT / "options.js").read_text(encoding="utf-8")
OPTIONS_HTML = (EXT / "options.html").read_text(encoding="utf-8")

NODE = shutil.which("node")


class ManifestTests(unittest.TestCase):
    def test_content_script_registered_for_scixplorer(self):
        scripts = MANIFEST.get("content_scripts", [])
        self.assertEqual(len(scripts), 1)
        self.assertEqual(scripts[0]["matches"], ["https://scixplorer.org/*"])
        self.assertIn("content-scix.js", scripts[0]["js"])
        self.assertIn("content-scix.css", scripts[0]["css"])
        self.assertEqual(scripts[0]["run_at"], "document_idle")

    def test_host_permissions_cover_scixplorer_and_localhost(self):
        hosts = MANIFEST.get("host_permissions", [])
        self.assertIn("https://scixplorer.org/*", hosts)
        self.assertIn("http://localhost:8765/*", hosts)

    def test_no_new_invade_permissions(self):
        # The overlay only reads the URL and talks to the local server;
        # no broad <all_urls>, scripting, or webRequest grants.
        self.assertNotIn("<all_urls>", json.dumps(MANIFEST))
        self.assertNotIn("scripting", MANIFEST.get("permissions", []))
        self.assertNotIn("webRequest", MANIFEST.get("permissions", []))


class ContentScriptTests(unittest.TestCase):
    def test_extracts_bibcode_from_url_not_dom(self):
        self.assertIn("PAPER_ROUTE_RE", CONTENT_JS)
        self.assertIn("location.pathname", CONTENT_JS)
        # Never scrapes scixplorer's own markup for the identifier.
        self.assertNotIn("querySelector('.abstract", CONTENT_JS)

    def test_relays_requests_through_background_worker(self):
        # Mixed-content/PNA-safe: the page script itself never fetches
        # the local http server directly.
        self.assertIn("scixResolve", CONTENT_JS)
        self.assertNotIn("fetch('http://localhost", CONTENT_JS)
        self.assertNotIn("fetch(\"http://localhost", CONTENT_JS)

    def test_panel_survives_spa_navigation(self):
        self.assertIn("setInterval", CONTENT_JS)
        self.assertIn("popstate", CONTENT_JS)

    def test_storage_key_rule_is_displayed_correctly(self):
        self.assertIn("isArxivId", CONTENT_JS)
        self.assertIn("'SciX:'", CONTENT_JS)

    def test_double_injection_guard(self):
        self.assertIn("__arxistantScixPanel", CONTENT_JS)

    def test_route_regex_in_test_matches_file(self):
        # Drift protection: the semantics test below re-types the regex;
        # it must stay byte-identical to the one in content-scix.js.
        self.assertIn(
            "PAPER_ROUTE_RE = /^\\/(?:abs|detail)\\/"
            "([A-Za-z0-9.%&\\-_]{8,30})\\/?$/;",
            CONTENT_JS)

    @unittest.skipIf(NODE is None, "node not installed")
    def test_route_regex_semantics(self):
        # Mirrors PAPER_ROUTE_RE from content-scix.js, tested against
        # path-only strings the way location.pathname provides them.
        script = r"""
const PAPER_ROUTE_RE = /^\/(?:abs|detail)\/([A-Za-z0-9.%&\-_]{8,30})\/?$/;
const cases = [
  ['/abs/1929PNAS...15..168H', '1929PNAS...15..168H'],
  ['/abs/2020A%26A...641A...6P', '2020A&A...641A...6P'],
  ['/abs/2020A&A...641A...6P', '2020A&A...641A...6P'],
  ['/detail/2018ApJS..239...17A/', '2018ApJS..239...17A'],
  ['/abs/2023arXiv230711273V', '2023arXiv230711273V'],
  ['/search?q=black+holes', null],
  ['/', null],
  ['/abs/', null],
  ['/user/libraries/abc123', null],
  ['/help/getting-started', null],
];
let failed = 0;
for (const [path, want] of cases) {
  const m = PAPER_ROUTE_RE.exec(path);
  let got = m ? decodeURIComponent(m[1]) : null;
  if (got !== want) { failed++; console.log('MISMATCH ' + path + ' -> ' + got + ' (want ' + want + ')'); }
}
process.exit(failed ? 1 : 0);
"""
        result = subprocess.run([NODE, "-e", script], capture_output=True,
                                text=True, timeout=20)
        self.assertEqual(result.returncode, 0,
                         "route regex mismatches:\n" + result.stdout)

    @unittest.skipIf(NODE is None, "node not installed")
    def test_content_script_syntax(self):
        result = subprocess.run([NODE, "--check", str(EXT / "content-scix.js")],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


class BackgroundRelayTests(unittest.TestCase):
    def test_panel_relay_actions_exist(self):
        for action in ("scixSavedPapers", "scixResolve", "scixSavePaper",
                       "scixDeletePaper"):
            self.assertIn(f"case '{action}':", BACKGROUND_JS)

    def test_save_relay_sends_storage_key_shape(self):
        # The relay must use the resolver's key (paper.id), never the raw
        # bibcode, so saves stay unified with daily-page saves.
        self.assertIn("arxiv_id: p.id", BACKGROUND_JS)

    def test_token_relay_actions_exist(self):
        for action in ("getAdsToken", "saveAdsToken", "testAdsToken"):
            self.assertIn(f"case '{action}':", BACKGROUND_JS)

    def test_relay_errors_are_reported_not_thrown(self):
        # The panel shows errors inline; the worker must not reject the
        # message promise for ordinary server failures.
        self.assertIn("return { success: false, error: error.message }",
                      BACKGROUND_JS)


class OptionsPageTests(unittest.TestCase):
    def test_ads_token_section_exists(self):
        self.assertIn('id="section-ads"', OPTIONS_HTML)
        self.assertIn('id="ads-token"', OPTIONS_HTML)
        self.assertIn('id="btn-ads-save"', OPTIONS_HTML)
        self.assertIn('id="btn-ads-test"', OPTIONS_HTML)
        self.assertIn('id="btn-ads-clear"', OPTIONS_HTML)

    def test_token_input_is_password_field(self):
        self.assertIn('type="password" id="ads-token"', OPTIONS_HTML)

    def test_token_handlers_wired(self):
        self.assertIn("saveAdsToken", OPTIONS_JS)
        self.assertIn("testAdsToken", OPTIONS_JS)
        self.assertIn("clearAdsToken", OPTIONS_JS)
        self.assertIn("await loadAdsToken();", OPTIONS_JS)

    def test_save_requires_nonempty_token(self):
        self.assertIn("Paste a token first", OPTIONS_JS)

    def test_token_test_runs_after_save(self):
        self.assertIn("await testAdsToken();", OPTIONS_JS)

    def test_token_help_mentions_where_to_get_it(self):
        self.assertIn("ui.adsabs.harvard.edu", OPTIONS_HTML)


if __name__ == "__main__":
    unittest.main()
