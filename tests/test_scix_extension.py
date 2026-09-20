"""Tests for the Chrome-extension side of the SciXplorer integration.

The overlay on scixplorer.org is the project's first content-script
injection, and the ADS-token box is the settings page's newest credential
section. These tests pin the manifest wiring, the background relays, the
settings-page handlers, and (when Node is available) the content script's
route regex and syntax.
"""

import json
import re
import shutil
import subprocess
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXT = PROJECT_ROOT / "chrome-extension"

MANIFEST = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
CONTENT_JS = (EXT / "content-scix.js").read_text(encoding="utf-8")
PANEL_JS = (EXT / "content-panel.js").read_text(encoding="utf-8")
# Content scripts load in manifest order: the shared panel module first,
# then the site adapter that calls ArXistantPanel.init().
SCIX_SCRIPTS = "content-panel.js,content-scix.js"
BACKGROUND_JS = (EXT / "background.js").read_text(encoding="utf-8")
OPTIONS_JS = (EXT / "options.js").read_text(encoding="utf-8")
OPTIONS_HTML = (EXT / "options.html").read_text(encoding="utf-8")

NODE = shutil.which("node")
HARNESS = PROJECT_ROOT / "tests" / "js" / "panel-harness.js"

# URL forms the route matcher must handle. The sub-page segments are the ones
# a real scixplorer.org paper page actually uses; they are the reason the
# panel silently never appeared.
_ROUTE_CASES_JS = r"""
const cases = [
  // Plain and trailing-slash forms.
  ['/abs/1929PNAS...15..168H', '1929PNAS...15..168H'],
  ['/abs/1929PNAS...15..168H/', '1929PNAS...15..168H'],
  ['/detail/2018ApJS..239...17A', '2018ApJS..239...17A'],
  // Sub-page segments (ADS Classic route shape, inherited by SciXplorer).
  ['/abs/1929PNAS...15..168H/abstract', '1929PNAS...15..168H'],
  ['/abs/1998AJ....116.1009R/citations', '1998AJ....116.1009R'],
  ['/abs/1998AJ....116.1009R/references', '1998AJ....116.1009R'],
  ['/abs/1998AJ....116.1009R/metrics', '1998AJ....116.1009R'],
  ['/abs/1998AJ....116.1009R/graphics', '1998AJ....116.1009R'],
  ['/abs/1998AJ....116.1009R/exportcitation', '1998AJ....116.1009R'],
  // Ampersand-bearing bibcodes, percent-encoded and literal.
  ['/abs/2020A%26A...641A...6P/abstract', '2020A&A...641A...6P'],
  ['/abs/2020A&A...641A...6P/abstract', '2020A&A...641A...6P'],
  // Other identifier forms.
  ['/abs/2023arXiv230711273V', '2023arXiv230711273V'],
  ['/abs/arXiv:1802.08364', 'arXiv:1802.08364'],
  // Non-paper routes and junk must not open a panel.
  ['/search?q=black+holes', null],
  ['/', null],
  ['/abs/', null],
  ['/abs/x', null],
  ['/user/libraries/abc123', null],
  ['/help/getting-started', null],
  ['/scixblog/openapi-docs', null],
];
let failed = 0;
for (const [path, want] of cases) {
  const got = currentBibcode(path);
  if (got !== want) {
    failed++;
    console.log('MISMATCH ' + path + ' -> ' + got + ' (want ' + want + ')');
  }
}
process.exit(failed ? 1 : 0);
"""


class ManifestTests(unittest.TestCase):
    def test_content_script_registered_for_scixplorer(self):
        scripts = MANIFEST.get("content_scripts", [])
        scix = next(s for s in scripts
                    if "content-scix.js" in s.get("js", []))
        self.assertEqual(sorted(scix["matches"]),
                         ["https://scixplorer.org/*",
                          "https://www.scixplorer.org/*"])
        # The shared panel module must load before the adapter that calls it.
        self.assertEqual(scix["js"], ["content-panel.js", "content-scix.js"])
        self.assertIn("content-panel.css", scix["css"])
        self.assertEqual(scix["run_at"], "document_idle")

    def test_host_permissions_cover_scixplorer_and_localhost(self):
        hosts = MANIFEST.get("host_permissions", [])
        self.assertIn("https://scixplorer.org/*", hosts)
        # www.scixplorer.org resolves too; without it the script never loads
        # for a user who happens to land on the www host.
        self.assertIn("https://www.scixplorer.org/*", hosts)
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
        self.assertIn("location.pathname", PANEL_JS)
        # Never scrapes scixplorer's own markup for the identifier.
        self.assertNotIn("querySelector('.abstract", CONTENT_JS)
        self.assertNotIn("querySelector('.abstract", PANEL_JS)

    def test_relays_requests_through_background_worker(self):
        # Mixed-content/PNA-safe: the page script itself never fetches
        # the local http server directly.
        self.assertIn("scixResolve", CONTENT_JS)
        for src in (CONTENT_JS, PANEL_JS):
            self.assertNotIn("fetch('http://localhost", src)
            self.assertNotIn("fetch(\"http://localhost", src)

    def test_panel_survives_spa_navigation(self):
        # scixplorer is a SPA: the adapter asks for polling and the shared
        # panel implements it.
        self.assertIn("poll: true", CONTENT_JS)
        self.assertIn("setInterval", PANEL_JS)
        self.assertIn("popstate", PANEL_JS)

    def test_storage_key_rule_is_displayed_correctly(self):
        self.assertIn("isArxivId", PANEL_JS)
        self.assertIn("'SciX:'", CONTENT_JS)

    def test_double_injection_guard(self):
        self.assertIn("__arxistantScixPanel", CONTENT_JS)

    def test_panel_attaches_under_body(self):
        # Appending to documentElement makes the panel a sibling of <head>
        # and <body>, which is invalid placement some page CSS misplaces.
        self.assertIn(
            "(document.body || document.documentElement).appendChild(panel)",
            PANEL_JS)
        self.assertNotIn("document.documentElement.appendChild(panel)",
                         PANEL_JS)

    def test_panel_is_recreated_if_the_spa_removes_it(self):
        self.assertIn("!panel.isConnected", PANEL_JS)

    def test_failures_are_diagnosable_from_the_console(self):
        # The panel is invisible when the route does not match, so the script
        # must report what it decided rather than failing silently.
        self.assertIn("[ArXistant ", PANEL_JS)
        self.assertIn("not a paper page", PANEL_JS)
        self.assertIn("panel attached", PANEL_JS)

    def test_route_is_checked_immediately_not_only_on_the_first_poll(self):
        self.assertIn("onRouteChange().catch", PANEL_JS)

    # --- Route matching -------------------------------------------------
    # The regexes are EXTRACTED from the source rather than retyped here.
    # The previous version of this test re-declared the pattern, so it agreed
    # with the buggy source instead of with reality: scixplorer.org paper
    # pages carry a sub-page segment (/abs/<bibcode>/abstract, inherited from
    # ADS Classic) and the old pattern was anchored at end-of-path, so it
    # matched nothing and the panel never appeared — while this test passed.

    @staticmethod
    def _extract_regex(name):
        m = re.search(rf"^\s*const {name} = (/.*);$", CONTENT_JS, re.M)
        if not m:
            raise AssertionError(f"{name} not found in content-scix.js")
        return m.group(1)

    @unittest.skipIf(NODE is None, "node not installed")
    def test_route_matching_accepts_subpages_and_rejects_non_papers(self):
        script = (
            "const PAPER_ROUTE_RE = " + self._extract_regex("PAPER_ROUTE_RE") + ";\n"
            "const BIBCODE_RE = " + self._extract_regex("BIBCODE_RE") + ";\n"
            # A faithful copy of currentBibcode()'s decode-and-validate step.
            "function currentBibcode(pathname) {\n"
            "  const m = PAPER_ROUTE_RE.exec(pathname);\n"
            "  if (!m) return null;\n"
            "  let raw;\n"
            "  try { raw = decodeURIComponent(m[1]); } catch (e) { raw = m[1]; }\n"
            "  if (!BIBCODE_RE.test(raw)) return null;\n"
            "  return raw;\n"
            "}\n"
            + _ROUTE_CASES_JS
        )
        result = subprocess.run([NODE, "-e", script], capture_output=True,
                                text=True, timeout=20)
        self.assertEqual(result.returncode, 0,
                         "route matching failures:\n" + result.stdout)

    def test_route_matcher_does_not_anchor_at_end_of_path(self):
        # Regression guard for the specific defect: an end-of-path anchor is
        # what made every real paper URL fail to match.
        pattern = self._extract_regex("PAPER_ROUTE_RE")
        self.assertFalse(pattern.rstrip("/").endswith("$"),
                         "PAPER_ROUTE_RE must not be anchored at end-of-path; "
                         "paper URLs carry sub-page segments like /abstract")

    @unittest.skipIf(NODE is None, "node not installed")
    def test_content_script_syntax(self):
        result = subprocess.run([NODE, "--check", str(EXT / "content-scix.js")],
                                capture_output=True, text=True, timeout=20)
        self.assertEqual(result.returncode, 0, result.stderr)


class PanelBehaviourTests(unittest.TestCase):
    """Run the REAL content script under a stub DOM and assert what it does.

    A route-regex unit test passed while the panel never appeared in the
    browser, because the test agreed with the source instead of with reality.
    These tests execute the shipped script (tests/js/panel-harness.js) so the
    whole path — route match, resolve relay, render, attach — is covered.
    """

    BIB = "1929PNAS...15..168H"

    def setUp(self):
        if NODE is None:
            self.skipTest("node not installed")

    def _run(self, pathname, host="scixplorer.org", meta=None, script=None):
        scripts = script or ",".join(str(EXT / n) for n in SCIX_SCRIPTS.split(","))
        args = [NODE, str(HARNESS), scripts, pathname, host,
                json.dumps(meta or {})]
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        data = json.loads(result.stdout)
        self.assertIsNone(data.get("fatal"), f"script threw: {data.get('fatal')}")
        return data

    def test_panel_appears_on_a_suffixed_paper_route(self):
        # The reported bug: SciXplorer paper pages carry a sub-page segment,
        # and the panel never appeared on them.
        out = self._run(f"/abs/{self.BIB}/abstract")
        self.assertTrue(out["panelAttached"])
        self.assertEqual(out["resolveIdentifier"], self.BIB)
        self.assertIn("scixResolve", out["messages"])

    def test_panel_attaches_under_body(self):
        out = self._run(f"/abs/{self.BIB}/abstract")
        self.assertEqual(out["panelParent"], "body")
        self.assertTrue(out["panelVisible"])

    def test_panel_renders_the_resolved_paper(self):
        out = self._run(f"/abs/{self.BIB}")
        html = out["panelHtml"]
        self.assertIn("SciX:" + self.BIB, html)
        self.assertIn("A Relation between Distance and Radial Velocity", html)
        self.assertIn("arx-save", html)
        self.assertIn("arx-chat", html)
        self.assertIn("Show abstract", html)

    def test_no_panel_on_non_paper_routes(self):
        for path in ("/search?q=black+holes", "/", "/user/libraries/abc123",
                     "/help/getting-started"):
            out = self._run(path)
            self.assertFalse(out["panelAttached"], path)
            self.assertEqual(out["messages"], [], path)

    def test_already_saved_paper_shows_the_saved_state(self):
        out = self._run(f"/abs/{self.BIB}/abstract",
                        meta={"__savedIds": [self.BIB]})
        self.assertIn("✓ Saved", out["panelHtml"])
        self.assertIn("arx-saved", out["panelHtml"])

    def test_resolve_failure_is_reported_not_silent(self):
        out = self._run(f"/abs/{self.BIB}/abstract", meta={"__resolveFails": True})
        self.assertTrue(out["panelAttached"])
        self.assertIn("SciX has no record", out["panelHtml"])

    def test_diagnostics_explain_what_the_script_decided(self):
        out = self._run(f"/abs/{self.BIB}/abstract")
        joined = "\n".join(out["logs"])
        self.assertIn("content script active", joined)
        self.assertIn("paper page, identifier " + self.BIB, joined)
        self.assertIn("panel attached", joined)


class BackgroundRelayTests(unittest.TestCase):
    def test_panel_relay_actions_exist(self):
        for action in ("savedPapers", "scixResolve", "arxivResolve",
                       "savePaper", "deletePaper"):
            self.assertIn(f"case '{action}':", BACKGROUND_JS)

    def test_no_stale_scix_prefixed_panel_relays_remain(self):
        for old_name in ("scixSavedPapers", "scixSavePaper", "scixDeletePaper"):
            self.assertNotIn(old_name, BACKGROUND_JS)
            self.assertNotIn(old_name, PANEL_JS)
            self.assertNotIn(old_name, CONTENT_JS)

    def test_every_relay_the_panel_sends_is_handled(self):
        # A renamed relay on one side only fails silently at runtime: send()
        # resolves with "Unknown action" and the panel just shows an error.
        # This is the desync that the stub-DOM harness also caught.
        sent = set(re.findall(r"send\('([A-Za-z]+)'", PANEL_JS))
        sent |= set(re.findall(r"send\('([A-Za-z]+)'", CONTENT_JS))
        self.assertTrue(sent, "no relay calls found — extraction broke")
        handled = set(re.findall(r"case '([A-Za-z]+)':", BACKGROUND_JS))
        missing = sent - handled
        self.assertEqual(missing, set(),
                         f"content scripts send unhandled actions: {missing}")

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
