"""Tests for the arxiv.org page panel and the shared panel module.

The panel is the same component on both sites: content-panel.js implements it
and each site supplies a thin adapter. arxiv.org's /abs/ pages carry complete
citation_* metadata, so the arXiv adapter reads the paper straight off the page
— no ADS token, no network round-trip, and no exposure to arXiv's rate limiter.

The behavioural tests run the real scripts under tests/js/panel-harness.js.
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

MANIFEST = json.loads((EXT / "manifest.json").read_text(encoding="utf-8"))
PANEL_JS = (EXT / "content-panel.js").read_text(encoding="utf-8")
ARXIV_JS = (EXT / "content-arxiv.js").read_text(encoding="utf-8")
SCIX_JS = (EXT / "content-scix.js").read_text(encoding="utf-8")
BACKGROUND_JS = (EXT / "background.js").read_text(encoding="utf-8")

SCRIPTS = "content-panel.js,content-arxiv.js"

# Real values from an arxiv.org /abs/ page (arXiv:1802.08364).
META = {
    "citation_arxiv_id": "1802.08364",
    "citation_title": "On the Gas Content and Efficiency of AGN Feedback "
                      "in Low-redshift Quasars",
    "citation_author": ["Shangguan, Jinyi", "Ho, Luis C.", "Xie, Yanxia"],
    "citation_abstract": "The interstellar medium is crucial to understanding "
                         "the physics of active galaxies.",
    "citation_date": "2018/02/23",
    "citation_doi": "10.3847/1538-4357/aaa9be",
}


def _run(pathname, meta=META, click=None, saved=None,
         host="arxiv.org", scripts=SCRIPTS):
    """Run the real content scripts against a stub page.

    meta=None means the page carries no citation_* tags at all, which is how
    the server fallback path is exercised.
    """
    payload = {}
    if meta is not None:
        payload["meta"] = meta
    if click:
        payload["__click"] = click
    if saved:
        payload["__savedIds"] = saved
    args = [NODE, str(HARNESS),
            ",".join(str(EXT / n) for n in scripts.split(",")),
            pathname, host, json.dumps(payload)]
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr or result.stdout
    data = json.loads(result.stdout)
    assert not data.get("fatal"), f"content script threw: {data.get('fatal')}"
    return data


def _run_with_tags(click=".arx-tag-add", saved=None, tags_by_id=None,
                   vocab=("agn", "lrd"), value="agn"):
    """Type a tag, then run a click sequence (default: Add).

    Adding a tag to an unsaved paper saves it, so a following .arx-save click
    would mean "remove" — the default sequence stops at Add.
    """
    payload = {
        "meta": dict(META),
        "__vocab": list(vocab),
        "__type": {"selector": ".arx-tag-input", "value": value},
        "__click": click,
    }
    if saved:
        payload["__savedIds"] = list(saved)
    if tags_by_id:
        payload["__tagsById"] = tags_by_id
    args = [NODE, str(HARNESS),
            ",".join(str(EXT / n) for n in SCRIPTS.split(",")),
            "/abs/1802.08364", "arxiv.org", json.dumps(payload)]
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr or result.stdout
    data = json.loads(result.stdout)
    assert not data.get("fatal"), f"content script threw: {data.get('fatal')}"
    return data


class ManifestTests(unittest.TestCase):
    def test_arxiv_abs_pages_get_the_panel(self):
        entry = next(s for s in MANIFEST["content_scripts"]
                     if "content-arxiv.js" in s.get("js", []))
        self.assertEqual(entry["matches"], ["https://arxiv.org/abs/*"])
        # Shared module first: the adapter calls window.ArXistantPanel.init().
        self.assertEqual(entry["js"], ["content-panel.js", "content-arxiv.js"])
        self.assertIn("content-panel.css", entry["css"])

    def test_match_is_scoped_to_abs_pages(self):
        # Scoping to /abs/* keeps the script off arXiv's list and search pages,
        # where there is no single paper to act on.
        entry = next(s for s in MANIFEST["content_scripts"]
                     if "content-arxiv.js" in s.get("js", []))
        self.assertNotIn("https://arxiv.org/*", entry["matches"])

    def test_no_host_permission_is_requested_for_arxiv(self):
        # The script reads the page DOM and messages the worker; it never
        # fetches arxiv.org. Injecting via content_scripts needs no host
        # permission, so the user is not asked to grant one.
        self.assertNotIn("https://arxiv.org/*", MANIFEST["host_permissions"])

    def test_both_sites_share_one_panel_module(self):
        for adapter in (ARXIV_JS, SCIX_JS):
            self.assertIn("window.ArXistantPanel.init({", adapter)
        self.assertIn("window.ArXistantPanel = {", PANEL_JS)

    def test_adapters_use_distinct_injection_guards(self):
        guards = re.findall(r"guard: '([^']+)'", ARXIV_JS + SCIX_JS)
        self.assertEqual(len(guards), 2)
        self.assertEqual(len(set(guards)), 2, "guards must differ per site")


class ArxivRouteTests(unittest.TestCase):
    @unittest.skipIf(NODE is None, "node not installed")
    def test_route_matching(self):
        pattern = re.search(r"^\s*const ABS_ROUTE_RE = (/.*);$", ARXIV_JS, re.M)
        self.assertIsNotNone(pattern, "ABS_ROUTE_RE not found")
        script = (
            "const ABS_ROUTE_RE = " + pattern.group(1) + ";\n"
            "function stripVersion(id){return String(id||'').replace(/v\\d+$/,'');}\n"
            "function parse(pathname) {\n"
            "  const m = ABS_ROUTE_RE.exec(pathname);\n"
            "  if (!m) return null;\n"
            "  let raw = m[1];\n"
            "  try { raw = decodeURIComponent(raw); } catch (e) {}\n"
            "  raw = raw.replace(/\\/+$/, '');\n"
            "  return stripVersion(raw) || null;\n"
            "}\n"
            "const cases = [\n"
            "  ['/abs/1802.08364', '1802.08364'],\n"
            "  ['/abs/1802.08364v1', '1802.08364'],\n"
            "  ['/abs/2609.12040v12', '2609.12040'],\n"
            "  ['/abs/math/0102001', 'math/0102001'],\n"
            "  ['/abs/math/0102001v2', 'math/0102001'],\n"
            "  ['/abs/1802.08364/', '1802.08364'],\n"
            "  ['/list/astro-ph.GA/recent', null],\n"
            "  ['/pdf/1802.08364', null],\n"
            "  ['/html/1802.08364v1', null],\n"
            "  ['/', null],\n"
            "  ['/abs/', null],\n"
            "];\n"
            "let failed = 0;\n"
            "for (const [p, want] of cases) {\n"
            "  const got = parse(p);\n"
            "  if (got !== want) { failed++; console.log('MISMATCH ' + p + ' -> ' + got + ' (want ' + want + ')'); }\n"
            "}\n"
            "process.exit(failed ? 1 : 0);\n"
        )
        result = subprocess.run([NODE, "-e", script], capture_output=True,
                                text=True, timeout=20)
        self.assertEqual(result.returncode, 0,
                         "route mismatches:\n" + result.stdout)


@unittest.skipIf(NODE is None, "node not installed")
class ArxivPanelBehaviourTests(unittest.TestCase):
    def test_panel_appears_on_an_abs_page(self):
        out = _run("/abs/1802.08364")
        self.assertTrue(out["panelAttached"])
        self.assertEqual(out["panelParent"], "body")

    def test_metadata_comes_from_the_page_not_the_network(self):
        # The whole point of using citation_* tags: no resolve relay, so no
        # ADS token and no arXiv API call (which is rate-limited).
        out = _run("/abs/1802.08364")
        self.assertNotIn("arxivResolve", out["messages"])
        self.assertNotIn("scixResolve", out["messages"])
        html = out["panelHtml"]
        self.assertIn("arXiv:1802.08364", html)
        self.assertIn(META["citation_title"], html)
        self.assertIn("2018", html)

    def test_version_suffix_is_stripped_from_the_key(self):
        # The Daily page stores version-less IDs; a versioned key would create
        # a duplicate row for the same paper.
        out = _run("/abs/1802.08364v1")
        self.assertIn("arXiv:1802.08364<", out["panelHtml"])
        self.assertNotIn("1802.08364v1", out["panelHtml"])

    def test_save_sends_the_same_key_the_daily_page_uses(self):
        out = _run("/abs/1802.08364v1", click=".arx-save")
        self.assertIn("savePaper", out["messages"])
        self.assertEqual(out["saveKey"], "1802.08364")
        paper = out["savePayload"]
        self.assertEqual(paper["title"], META["citation_title"])
        self.assertEqual(paper["authors"], META["citation_author"])
        self.assertEqual(paper["abstract"], META["citation_abstract"])
        self.assertTrue(paper["is_arxiv"])

    def test_saved_paper_click_removes_it(self):
        out = _run("/abs/1802.08364", click=".arx-save",
                   saved=["1802.08364"])
        self.assertIn("deletePaper", out["messages"])
        self.assertEqual(out["deleteKey"], "1802.08364")
        self.assertIsNone(out["saveKey"])

    def test_old_style_ids_are_supported(self):
        out = _run("/abs/math/0102001v2", meta={
            "citation_arxiv_id": "math/0102001",
            "citation_title": "An old-style paper",
            "citation_author": ["Author, A."],
            "citation_abstract": "Abstract.",
        })
        self.assertTrue(out["panelAttached"])
        self.assertIn("arXiv:math/0102001", out["panelHtml"])

    def test_missing_meta_tags_fall_back_to_the_server(self):
        out = _run("/abs/1802.08364", meta=None)
        self.assertTrue(out["panelAttached"])
        self.assertIn("arxivResolve", out["messages"])

    def test_no_panel_off_abs_pages(self):
        for path in ("/list/astro-ph.GA/recent", "/", "/pdf/1802.08364"):
            out = _run(path)
            self.assertFalse(out["panelAttached"], path)

    def test_no_abstract_ui_the_page_already_shows_it(self):
        out = _run("/abs/1802.08364")
        self.assertNotIn("Show abstract", out["panelHtml"])
        self.assertNotIn("arx-abstract", out["panelHtml"])
        # The abstract is still carried for the save payload: it feeds ML
        # training and Chat grounding, it is just not displayed.
        self.assertEqual(out["savePayload"] if out.get("savePayload") else None,
                         None)

    def test_abstract_is_still_sent_when_saving(self):
        out = _run("/abs/1802.08364", click=".arx-save")
        self.assertEqual(out["savePayload"]["abstract"], META["citation_abstract"])

    def test_tagging_saves_the_paper_in_one_write(self):
        out = _run_with_tags()
        self.assertIn("savePaper", out["messages"])
        self.assertEqual(out["saveKey"], "1802.08364")
        self.assertEqual(out["tagSent"], ["agn"])
        self.assertEqual(out["tagChips"], ["agn"])
        self.assertIn("✓ Saved", out["panelHtml"])
        # One write: the tag rides along with the save.
        self.assertNotIn("updateTags", out["messages"])

    def test_saving_without_tagging_still_sends_the_tag_list(self):
        out = _run("/abs/1802.08364", click=".arx-save")
        self.assertIn("savePaper", out["messages"])
        self.assertEqual(out["tagSent"], [])

    def test_saved_paper_shows_its_existing_tags(self):
        # No clicks: for a saved paper the Save button means "remove", so this
        # only inspects the initial render.
        out = _run_with_tags(click=None, saved=["1802.08364"],
                             tags_by_id={"1802.08364": ["lrd"]})
        self.assertEqual(out["tagChips"], ["lrd"])
        self.assertIn("✓ Saved", out["panelHtml"])

    def test_saved_paper_tag_change_persists_without_re_saving(self):
        out = _run_with_tags(click=".arx-tag-add", saved=["1802.08364"],
                             tags_by_id={"1802.08364": ["lrd"]})
        self.assertIn("updateTags", out["messages"])
        self.assertEqual(out["updateTagsPayload"]["tags"], ["lrd", "agn"])
        # The paper must not be re-saved or removed by a tag edit.
        self.assertNotIn("savePaper", out["messages"])
        self.assertNotIn("deletePaper", out["messages"])


class BackgroundRelayTests(unittest.TestCase):
    def test_arxiv_relay_normalises_the_identifier(self):
        # The fallback must strip a version suffix before matching, and must
        # report a clear error rather than an empty panel.
        self.assertIn("replace(/v\\d+$/, '')", BACKGROUND_JS)
        self.assertIn("arXiv has no record for", BACKGROUND_JS)

    def test_arxiv_relay_uses_the_token_free_endpoint(self):
        block = BACKGROUND_JS.split("case 'arxivResolve':", 1)[1]
        block = block.split("case '", 1)[0]
        self.assertIn("/api/arxiv/search", block)
        self.assertNotIn("/api/scix/resolve", block)


if __name__ == "__main__":
    unittest.main()
