"""Tests for the per-site panel toggle in the extension Settings.

The ArXistant panel on arxiv.org and scixplorer.org is opt-out per site:
Settings → Paper Panel has one checkbox each. Both default to on, including
for a stored settings object that predates the toggle, so nobody's panel
disappears on upgrade.

The behavioural cases run the real content scripts through
tests/js/panel-harness.js; the rest pin the settings plumbing.
"""

import json
import re
import shutil
import subprocess
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
EXT = PROJECT_ROOT / "chrome-extension"
HARNESS = PROJECT_ROOT / "tests" / "js" / "panel-harness.js"
NODE = shutil.which("node")

BACKGROUND_JS = (EXT / "background.js").read_text(encoding="utf-8")
OPTIONS_JS = (EXT / "options.js").read_text(encoding="utf-8")
OPTIONS_HTML = (EXT / "options.html").read_text(encoding="utf-8")
PANEL_JS = (EXT / "content-panel.js").read_text(encoding="utf-8")
ARXIV_JS = (EXT / "content-arxiv.js").read_text(encoding="utf-8")
SCIX_JS = (EXT / "content-scix.js").read_text(encoding="utf-8")

SITES = {
    "arxiv": {
        "scripts": "content-panel.js,content-arxiv.js",
        "pathname": "/abs/1802.08364",
        "host": "arxiv.org",
        "flag": "panelOnArxiv",
        "meta": {"citation_arxiv_id": "1802.08364",
                 "citation_title": "On the Gas Content of AGN Feedback",
                 "citation_author": ["Shangguan, Jinyi"],
                 "citation_abstract": "The interstellar medium matters."},
    },
    "scix": {
        "scripts": "content-panel.js,content-scix.js",
        "pathname": "/abs/1929PNAS...15..168H/abstract",
        "host": "scixplorer.org",
        "flag": "panelOnScix",
        "meta": None,
    },
}


def run_site(site, settings=None):
    cfg = SITES[site]
    payload = {}
    if cfg["meta"] is not None:
        payload["meta"] = cfg["meta"]
    if settings is not None:
        payload["__settings"] = settings
    args = [NODE, str(HARNESS),
            ",".join(str(EXT / n) for n in cfg["scripts"].split(",")),
            cfg["pathname"], cfg["host"], json.dumps(payload)]
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr or result.stdout
    data = json.loads(result.stdout)
    assert not data.get("fatal"), f"content script threw: {data.get('fatal')}"
    return data


@unittest.skipIf(NODE is None, "node not installed")
class PanelToggleBehaviourTests(unittest.TestCase):
    def test_panel_shows_by_default(self):
        for site in SITES:
            self.assertTrue(run_site(site)["panelAttached"], site)

    def test_explicitly_enabled_shows_the_panel(self):
        for site in SITES:
            out = run_site(site, {SITES[site]["flag"]: True})
            self.assertTrue(out["panelAttached"], site)

    def test_disabled_hides_the_panel(self):
        for site in SITES:
            out = run_site(site, {SITES[site]["flag"]: False})
            self.assertFalse(out["panelAttached"], site)

    def test_disabled_panel_makes_no_further_requests(self):
        # Turning the panel off must also stop the work behind it: no library
        # load, no metadata resolve.
        for site in SITES:
            out = run_site(site, {SITES[site]["flag"]: False})
            self.assertEqual(out["messages"], ["getSettings"], site)

    def test_disabled_panel_says_why_in_the_console(self):
        for site in SITES:
            out = run_site(site, {SITES[site]["flag"]: False})
            self.assertIn("panel is disabled for this site",
                          "\n".join(out["logs"]), site)

    def test_missing_flag_defaults_to_enabled(self):
        # A settings object written before the toggle exists has neither key.
        for site in SITES:
            out = run_site(site, {"serverUrl": "http://localhost:8765/daily.html"})
            self.assertTrue(out["panelAttached"], site)

    def test_the_two_sites_are_independent(self):
        # Turning off SciX must not turn off arXiv, or vice versa.
        self.assertTrue(run_site("arxiv", {"panelOnScix": False})["panelAttached"])
        self.assertFalse(run_site("scix", {"panelOnScix": False})["panelAttached"])
        self.assertFalse(run_site("arxiv", {"panelOnArxiv": False})["panelAttached"])
        self.assertTrue(run_site("scix", {"panelOnArxiv": False})["panelAttached"])


class AdapterTests(unittest.TestCase):
    def test_each_adapter_declares_its_own_setting_key(self):
        self.assertIn("settingKey: 'panelOnArxiv'", ARXIV_JS)
        self.assertIn("settingKey: 'panelOnScix'", SCIX_JS)

    def test_panel_checks_the_setting_before_touching_the_page(self):
        self.assertIn("adapter.settingKey", PANEL_JS)
        # The check must gate everything: no panel, no polling, no requests.
        self.assertIn("=== false", PANEL_JS)

    def test_settings_are_read_once_and_reused_for_the_server_origin(self):
        # start() reads settings for the enable check; onRouteChange must not
        # fetch them a second time on the normal path.
        start = PANEL_JS.split("async function start()", 1)[1]
        self.assertIn("send('getSettings')", start)
        self.assertIn("state.serverOrigin = new URL(settings.serverUrl).origin",
                      start)


class SettingsStorageTests(unittest.TestCase):
    def test_both_flags_default_to_enabled(self):
        # Same convention as skipWeekends: absent means on, only an explicit
        # false disables.
        for flag in ("panelOnArxiv", "panelOnScix"):
            self.assertIn(f"{flag}: stored.{flag} !== false", BACKGROUND_JS)
            self.assertIn(f"{flag}: settings.{flag} !== false", BACKGROUND_JS)

    def test_flags_are_persisted_by_save_settings(self):
        block = BACKGROUND_JS.split("async function saveSettings", 1)[1]
        block = block.split("await chrome.storage.local.set", 1)[0]
        self.assertIn("panelOnArxiv", block)
        self.assertIn("panelOnScix", block)


class OptionsPageTests(unittest.TestCase):
    def test_section_with_both_checkboxes_exists(self):
        self.assertIn('id="section-panel"', OPTIONS_HTML)
        self.assertIn("Paper Panel", OPTIONS_HTML)
        self.assertIn('id="panel-arxiv"', OPTIONS_HTML)
        self.assertIn('id="panel-scix"', OPTIONS_HTML)

    def test_checkboxes_default_to_checked(self):
        for ident in ("panel-arxiv", "panel-scix"):
            m = re.search(rf'<input type="checkbox" id="{ident}"([^>]*)>',
                          OPTIONS_HTML)
            self.assertIsNotNone(m, ident)
            self.assertIn("checked", m.group(1), ident)

    def test_the_sites_are_named_next_to_each_checkbox(self):
        self.assertIn("arxiv.org", OPTIONS_HTML)
        self.assertIn("scixplorer.org", OPTIONS_HTML)

    def test_hint_explains_that_a_reload_is_needed(self):
        section = OPTIONS_HTML.split('id="section-panel"', 1)[1]
        section = section.split("</details>", 1)[0]
        self.assertIn("Save Settings", section)
        self.assertIn("reload the tab", section)

    def test_options_script_loads_and_saves_both_flags(self):
        self.assertIn("getElementById('panel-arxiv')", OPTIONS_JS)
        self.assertIn("getElementById('panel-scix')", OPTIONS_JS)
        self.assertIn("settings.panelOnArxiv !== false", OPTIONS_JS)
        self.assertIn("settings.panelOnScix !== false", OPTIONS_JS)
        payload = OPTIONS_JS.split("action: 'saveSettings'", 1)[1]
        payload = payload.split("});", 1)[0]
        self.assertIn("panelOnArxiv", payload)
        self.assertIn("panelOnScix", payload)

    def test_reset_restores_both_to_enabled(self):
        reset = OPTIONS_JS.split("async function resetSettings", 1)[1]
        reset = reset.split("await saveSettings();", 1)[0]
        self.assertIn("panelArxivInput.checked = DEFAULT_SETTINGS.panelOnArxiv",
                      reset)
        self.assertIn("panelScixInput.checked = DEFAULT_SETTINGS.panelOnScix",
                      reset)

    def test_defaults_are_enabled(self):
        defaults = OPTIONS_JS.split("const DEFAULT_SETTINGS = {", 1)[1]
        defaults = defaults.split("};", 1)[0]
        self.assertIn("panelOnArxiv: true", defaults)
        self.assertIn("panelOnScix: true", defaults)


if __name__ == "__main__":
    unittest.main()
