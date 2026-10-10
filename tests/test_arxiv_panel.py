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


def _run_payload(pathname, payload, host="arxiv.org", scripts=SCRIPTS):
    """Run the real content scripts with a raw harness payload.

    The notebook tests need fixtures beyond meta/click/saved (folder
    snapshots, relay failures, staged typing), so they build the payload
    themselves and call this.
    """
    args = [NODE, str(HARNESS),
            ",".join(str(EXT / n) for n in scripts.split(",")),
            pathname, host, json.dumps(payload)]
    result = subprocess.run(args, capture_output=True, text=True, timeout=30)
    assert result.returncode == 0, result.stderr or result.stdout
    data = json.loads(result.stdout)
    assert not data.get("fatal"), f"content script threw: {data.get('fatal')}"
    return data


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
    return _run_payload(pathname, payload, host=host, scripts=scripts)


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

    def test_enter_keeps_focus_after_an_auto_save(self):
        # The unsaved path does more work (it saves the paper first), so the
        # re-render happens later — focus must still land back in the box.
        payload = {
            "meta": dict(META),
            "__vocab": ["agn"],
            "__type": {"selector": ".arx-tag-input", "value": "agn", "key": "Enter"},
        }
        args = [NODE, str(HARNESS),
                ",".join(str(EXT / n) for n in SCRIPTS.split(",")),
                "/abs/1802.08364", "arxiv.org", json.dumps(payload)]
        result = subprocess.run(args, capture_output=True, text=True, timeout=30)
        self.assertEqual(result.returncode, 0, result.stderr or result.stdout)
        out = json.loads(result.stdout)
        self.assertIsNone(out.get("fatal"), out.get("fatal"))
        self.assertIn("savePaper", out["messages"])
        self.assertEqual(out["tagSent"], ["agn"])
        self.assertEqual(out["focusedClass"], "arx-tag-input")
        self.assertEqual(out["focusedValue"], "")

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


@unittest.skipIf(NODE is None, "node not installed")
class NotebookPanelTests(unittest.TestCase):
    """The 📓 button files the paper into a Notebook folder.

    The server half of the feature (folder rows, /api/notebook* endpoints) is
    covered by tests/test_notebook.py; these tests cover the browsing-panel
    half — the button state, the folder picker, and the relay payloads the
    background worker turns into /api/notebook writes.
    """

    FOLDERS = [
        {"id": 1, "name": "To read", "notes": []},
        {"id": 2, "name": "AGN", "notes": [{"paper_id": "2401.00001"}]},
    ]

    def _run(self, folders=None, click=None, **extra):
        payload = {"meta": dict(META)}
        payload["__notebookFolders"] = self.FOLDERS if folders is None else folders
        if click:
            payload["__click"] = click
        payload.update(extra)
        return _run_payload("/abs/1802.08364", payload)

    def test_the_notebook_button_renders_next_to_save_and_chat(self):
        out = self._run()
        self.assertIn("arx-notebook", out["panelHtml"])
        self.assertIn("📓 Notebook", out["panelHtml"])
        # The folder snapshot loads with the library, so its state is known.
        self.assertIn("notebookFolders", out["messages"])

    def test_opening_the_picker_lists_every_folder(self):
        out = self._run(click=".arx-notebook")
        html = out["panelHtml"]
        self.assertIn("To read", html)
        self.assertIn("AGN", html)
        # One Add button per folder, plus the new-folder row.
        self.assertEqual(html.count("arx-nb-add"), 2)
        self.assertIn("New folder name", html)
        self.assertIn("Create &amp; add", html)

    def test_adding_to_a_folder_sends_the_storage_key(self):
        out = self._run(click=".arx-notebook,.arx-nb-add")
        self.assertIn("notebookNoteAdd", out["messages"])
        payload = out["notebookNoteAddPayload"]
        self.assertEqual(payload["folderId"], 1)
        # The version-less arXiv ID — the same key the Daily page files under.
        self.assertEqual(payload["paper"]["id"], "1802.08364")
        self.assertEqual(payload["paper"]["title"], META["citation_title"])
        self.assertEqual(payload["url"], "https://arxiv.org/abs/1802.08364")
        # A notebook row stands on its own: no save is needed first.
        self.assertNotIn("savePaper", out["messages"])
        self.assertIn("Added to “To read” ✓", out["statusHtml"])

    def test_adding_flips_the_button_and_the_folder_row(self):
        out = self._run(click=".arx-notebook,.arx-nb-add")
        html = out["panelHtml"]
        self.assertIn("✓ Notebook", html)
        self.assertIn("arx-in-notebook", html)
        # The folder the paper was filed into now shows the added state.
        self.assertIn("✓ Added", html)
        self.assertIn('disabled', html)
        # The other folder still offers Add.
        self.assertEqual(html.count('class="arx-nb-add">Add<'), 1)

    def test_a_paper_already_in_a_folder_is_marked_without_opening(self):
        folders = [{"id": 2, "name": "AGN",
                    "notes": [{"paper_id": "1802.08364"}]}]
        out = self._run(folders=folders)
        self.assertIn("✓ Notebook", out["panelHtml"])
        # Opening it still works: the row shows where it already is.
        out = self._run(folders=folders, click=".arx-notebook")
        self.assertIn("✓ Added", out["panelHtml"])

    def test_creating_a_folder_adds_the_paper_in_two_writes(self):
        out = self._run(folders=[],
                        __open=".arx-notebook",
                        __type={"selector": ".arx-nb-new-input",
                                "value": "Quasar feeds"},
                        __click=".arx-nb-new-add")
        self.assertIn("notebookFolderAdd", out["messages"])
        self.assertEqual(out["notebookFolderAddName"], "Quasar feeds")
        payload = out["notebookNoteAddPayload"]
        self.assertIsNotNone(payload)
        self.assertEqual(payload["paper"]["id"], "1802.08364")
        self.assertIn("Added to “Quasar feeds” ✓", out["statusHtml"])
        # The box is cleared on success and the new folder row shows Added.
        self.assertEqual(out["nbInputValue"], "")
        self.assertIn("Quasar feeds", out["panelHtml"])

    def test_filing_keeps_the_paper_out_of_the_library(self):
        # The whole point of the 📓 button: a notebook-only paper must not
        # land in saved_papers, because saves retrain the arXiv ranker and
        # schedule cloud sync. No notebook interaction may send a save.
        for extra in (
            {"__click": ".arx-notebook,.arx-nb-add"},
            {"__open": ".arx-notebook",
             "__type": {"selector": ".arx-nb-new-input", "value": "Maybe"},
             "__click": ".arx-nb-new-add"},
        ):
            out = self._run(**extra)
            self.assertNotIn("savePaper", out["messages"], extra)
            self.assertNotIn("updateTags", out["messages"], extra)
            # The Save button still offers Save — the paper is unsaved.
            self.assertIn("💾 Save", out["panelHtml"])
            self.assertNotIn("✓ Saved", out["panelHtml"])

    def test_enter_in_the_new_folder_input_creates_it(self):
        out = self._run(folders=[],
                        __open=".arx-notebook",
                        __type={"selector": ".arx-nb-new-input",
                                "value": "AGN follow-up", "key": "Enter"})
        self.assertEqual(out["notebookFolderAddName"], "AGN follow-up")
        self.assertIn("notebookNoteAdd", out["messages"])
        # Focus stays in the box, like the tag input after Enter.
        self.assertEqual(out["focusedClass"], "arx-nb-new-input")

    def test_a_folder_name_collision_reuses_the_existing_folder(self):
        # The server answers 409 when the name exists; re-typing an existing
        # name (even in different case) must reuse it, not fail.
        out = self._run(__open=".arx-notebook",
                        __type={"selector": ".arx-nb-new-input",
                                "value": "to READ"},
                        __folderAddFails=True,
                        __click=".arx-nb-new-add")
        payload = out["notebookNoteAddPayload"]
        self.assertIsNotNone(payload)
        self.assertEqual(payload["folderId"], 1)
        self.assertIn("Added to “To read” ✓", out["statusHtml"])
        # The reuse re-reads the folder list rather than trusting a stale one.
        self.assertGreaterEqual(out["messages"].count("notebookFolders"), 2)

    def test_an_empty_notebook_invites_creating_a_folder(self):
        out = self._run(folders=[], click=".arx-notebook")
        self.assertIn("No folders yet", out["panelHtml"])
        self.assertIn("arx-nb-new-input", out["panelHtml"])

    def test_an_unreachable_notebook_degrades_gracefully(self):
        # Every read fails: the rest of the panel must keep working, the
        # picker shows the relayed reason with a Retry button, and the 📓
        # button stays neutral instead of guessing a state.
        out = self._run(__notebookFails=True, click=".arx-notebook")
        self.assertIn("arx-nb-msg", out["panelHtml"])
        self.assertIn("Retry", out["panelHtml"])
        self.assertIn("arx-notebook", out["panelHtml"])
        self.assertNotIn("✓ Notebook", out["panelHtml"])
        self.assertIn("💾 Save", out["panelHtml"])

    def test_opening_the_picker_retries_a_failed_load(self):
        # The first read failed; opening the picker retries and succeeds.
        out = self._run(__notebookFailsOnce=True, click=".arx-notebook")
        self.assertEqual(out["notebookFoldersCalls"], 2)
        self.assertIn("To read", out["panelHtml"])
        self.assertNotIn("arx-nb-retry", out["panelHtml"])

    def test_the_picker_toggles_closed(self):
        # A second click on the 📓 button puts it away again, so it does not
        # block the tag input or the status line.
        out = self._run(click=".arx-notebook,.arx-notebook")
        self.assertNotIn("arx-nb-folders", out["panelHtml"])
        self.assertIn("arx-notebook", out["panelHtml"])

    def test_route_change_closes_the_picker(self):
        # The ✓-rows describe one paper, so SPA navigation to the next paper
        # must close the picker (the harness cannot navigate mid-run, so the
        # reset is checked in the source next to the other per-paper state).
        reset = PANEL_JS.split("state.tags = [];", 1)[1]
        self.assertIn("state.notebookOpen = false;", reset.split("log(", 1)[0])


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

    def test_notebook_relays_target_the_notebook_api(self):
        # Three writes the panel needs: list folders, create one, file the
        # paper. All go through the worker — a page script cannot fetch the
        # local server from an https page (mixed content / PNA).
        block = BACKGROUND_JS.split("case 'notebookFolders':", 1)[1]
        self.assertIn("/api/notebook'", block)
        self.assertIn("/api/notebook/folder/add", block)
        self.assertIn("/api/notebook/note/add", block)

    def test_notebook_add_sends_the_storage_key_as_paper_id(self):
        block = BACKGROUND_JS.split("case 'notebookNoteAdd':", 1)[1]
        block = block.split("case '", 1)[0]
        self.assertIn("paper_id: p.id", block)
        # The row stores the page URL so the Notebook page can link back to
        # journal-only records that have no arXiv landing page.
        self.assertIn("url: String(message.url", block)


if __name__ == "__main__":
    unittest.main()
