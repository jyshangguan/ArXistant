# dev_sci — SciXplorer (scixplorer.org) integration plan

Status: **proposed, awaiting approval** · Branch: `dev_sci` (from `master` @ 836af23)
Decisions confirmed with the user: scope = **both** (overlay on scixplorer.org **and** in-app
search), saved SciX papers keep today's fields with the **bibcode stored in `arxiv_id`** as the
key when no arXiv ID exists (no new columns).

---

## 1. Investigation findings (verified)

| Fact | Consequence for design |
|---|---|
| scixplorer.org is a React SPA behind **AWS WAF** (`x-amzn-waf-action: challenge`, HTTP 202 for non-browser clients) | The Python server can **never scrape scixplorer.org pages**. All page-side intelligence must live in a Chrome extension **content script** (runs inside the already-rendered page). |
| `https://api.scixplorer.org/v1/search/query` is a working mirror of the ADS API; the user's ADS token works on it (verified: `bibcode:1929PNAS...15..168H` and `identifier:2307.11273` both resolve, with abstract/title/authors/year/doi/citation_count) | Metadata resolution = one server-side endpoint reusing the existing ADS plumbing (`_fetch_with_retries`, `load_ads_token`, 429/Retry-After backoff). No new auth. |
| scixplorer paper URLs are `/abs/{bibcode}` (the app already builds these links in `scixUrlFor`); `/detail/{bibcode}` also observed | The content script extracts the bibcode **from the URL** (stable), never from the DOM (fragile). Support both route shapes. |
| `saved_papers` is keyed by `arxiv_id` (UNIQUE) and holds only title/authors/abstract/score/dates + user notes/tags/highlights; SciX metadata (bibcode/DOI/year/citations) is never persisted today | Bibcode-as-key is a data-model change; all key-typed flows must tolerate a second ID shape. Sync snapshots stay **byte-compatible** (no new columns). |
| The Chrome extension has **no content scripts** and host_permissions only for `http://localhost:8765/*` | The overlay is the first third-party-page injection in the project — new manifest permissions, new code surface, new failure modes. |
| Chat pins papers via `chat.html?paper={id}` → `pinFromUrl` (library lookup → arXiv-API fallback); grounding = full text (arXiv/ar5iv) or abstract fallback; reader is an iframe of `/api/chat/fulltext` | Bibcode papers need: resolve fallback in `pinFromUrl`, an abstract-only reader page, and guards on the arXiv-only paths (`/api/chat/pdf`, `scixUrlFor` regex, `paperAbsLink`). |
| `android/app/src/main/python/` is a **derived copy** of `src/` (regenerate with `android/copy_python.sh`; chat UI is stripped there) | Server changes must be mirrored by re-running the copy script on the branch. |
| Existing uncommitted master change (`_llm_net_error`) | Committed on `dev_sci` as its own commit (52a08a3). |

## 2. Design

**Key rule (shared core):** a paper's storage key is its **arXiv ID when one exists**, otherwise
its **bibcode** (stored in the existing `arxiv_id` column). Rationale: no duplicate rows when the
same paper is saved from the Daily page (by arXiv ID) and from scixplorer (by bibcode); no schema
migration; sync/ML/notes/tags/highlights keep working unchanged (they treat the key as an opaque
string; the ML ranker only reads title+abstract).

**Server (src/arxiv_db_server.py):**
- `GET /api/scix/resolve?bibcode=…` / `?identifier=…` → normalized paper JSON
  `{id, title, authors, abstract, year, bibcode, doi, citation_count, source:'scix'}` via
  `api.scixplorer.org` (`q=bibcode:…` / `q=identifier:…`), reusing `_fetch_with_retries` +
  ADS token. Key rule applied here: returned `id` = arXiv ID if the record has one, else bibcode.
- `/api/chat/fulltext?arxiv_id={key}`: when the key is not an arXiv ID, serve a generated
  same-origin HTML "abstract card" (title, authors, abstract, link out to scixplorer) so the
  reader pane, selection-based highlighting, and the `innerText.length ≥ 200` load-poll all work.
- Chat page JS: `pinFromUrl` gains a third fallback (`/api/scix/resolve`) after the arXiv lookup;
  `paperIdText` shows `SciX:{bibcode}` for bibcode keys; `paperScixLink` uses the bibcode
  directly; `paperAbsLink` and the PDF view are hidden for non-arXiv keys (same pattern as local
  PDFs); `scixUrlFor` gets a bibcode branch; the reader shows an "abstract-only" hint.
- Search page: ADS/SciX rows without an arXiv ID render the bibcode as a link to
  `scixplorer.org/abs/{bibcode}` and **gain save/chat/tag buttons** (today: "No arXiv ID —
  cannot save to DB"). The injected `SAVE_BUTTON_SCRIPT` / `CHAT_LINK_SCRIPT` / tag script switch
  from parsing `arxiv.org/abs/…` hrefs to a `data-paper-id` (+ optional `data-paper-href`)
  attribute on the `.paper` card, emitted by every renderer (daily HTML stays source-compatible —
  the ranker's generated markup gets the attribute in a follow-up or falls back to the old
  href-parsing path).

**Chrome extension (chrome-extension/):**
- `manifest.json`: add `content_scripts` for `https://scixplorer.org/*` (js: `content-scix.js`,
  css: `content-scix.css`, `run_at: document_idle`) + host permission for
  `https://scixplorer.org/*`. localhost stays the only other host.
- `content-scix.js`: URL watcher (poll `location.pathname` ~500 ms — SPA-proof, cheap) detects a
  paper route (`/abs/{bibcode}`, `/detail/{bibcode}`, plus a conservative 19-char bibcode regex).
  On a paper page it renders a small fixed ArXistant panel: paper title, **▸ Show abstract**
  (fetched once from the local server's resolve endpoint), **💾 Save/✓ Saved** (toggle via
  `/api/save` + `/api/delete`, saved-state from `/api/papers`), **💬 Chat** (opens
  `{serverUrl}/chat.html?paper={key}` in a new tab). Server URL comes from extension settings via
  `chrome.runtime.sendMessage` (the service worker already exposes settings; add one small
  `getSettings`-style handler reuse). Errors surface inline in the panel ("server offline",
  "no ADS token", rate-limit message).
- No DOM scraping of scixplorer content beyond the URL; the panel is fully self-contained
  (WAF-safe, resilient to scixplorer redesigns).

**Not in scope (v1):** scixplorer.org *search-results-list* augmentation (fragile DOM), fetching
publisher full text for non-arXiv papers, Android UI for the overlay (Android has no extension —
it benefits from the server-side leg automatically via the WebView app after copy regen).

## 3. Implementation phases

1. **Server core** — `/api/scix/resolve` + key-rule helper + unit tests (mocked
   `_fetch_with_retries`, following `tests/test_chat_config.py`'s ThreadingHTTPServer pattern).
2. **Chat support for bibcode papers** — resolve fallback, abstract-card fulltext route, label /
   link / PDF guards, abstract-only hint. Tests: pin-from-URL with a bibcode, fulltext route
   serving an abstract card, no arXiv fetch attempted for bibcode keys.
3. **In-app search leg** — save/chat/tag on non-arXiv ADS rows via `data-paper-id`; search-page
   tests + a regression test that arXiv-keyed flows are unchanged.
4. **Extension overlay** — manifest + content script + panel CSS; manual QA on the live site
   (permission re-grant, save round-trip, chat open, SPA navigation, server-offline state).
5. **Cross-cutting** — regenerate `android/app/src/main/python` (`copy_python.sh`), update
   `docs/user-guide.md` + `docs/how-it-works.md` + README highlights, full test-suite run,
   release-notes draft.

Each phase lands as one focused commit on `dev_sci`.

## 4. Risk assessment

| # | Risk | Severity | Likelihood | Mitigation |
|---|---|---|---|---|
| R1 | **Duplicate records**: same paper saved under both its arXiv ID (Daily page) and bibcode (scixplorer) → double ML preference counts, twice in library | Medium | Medium without guard | Key rule enforced at resolve time: arXiv ID always preferred when present. Add a dedupe safety in `/api/save` (if key looks like `YYYYarXivNNNNNNNNN…` and an arXiv-keyed row exists, re-key). |
| R2 | **scixplorer.org URL/route changes** break the overlay's paper detection | Medium | Medium | Bibcode comes from the URL only; match `/abs/`, `/detail/`, and a generic bibcode regex; panel fails soft (simply doesn't appear), never breaks the host page. |
| R3 | **New extension host permission** triggers Chrome's re-consent warning on reload; unpacked-extension users must re-approve | Low | Certain | Documented in the update notes; the permission is read-only for scixplorer.org and localhost fetches stay as-is. |
| R4 | **SciX/ADS API rate limits or downtime** (resolve + save flows) | Low-Med | Medium | Existing `_fetch_with_retries` backoff + 429/`Retry-After` handling is reused; resolve is 1 request per paper page view, cached in the panel; clear inline error text. |
| R5 | **Bibcode keys flowing into arXiv-only code paths** (`/api/chat/pdf`, ar5iv fulltext, `normalizeId`, viewer links) | Medium | Medium | Explicit `isArxivId()` helper gates every arXiv-network path; PDF/arXiv links hidden for bibcode keys; `normalizeId` regex verified safe for bibcodes (case-sensitive `v\d+$` cannot match trailing-uppercase-author-letter codes). |
| R6 | **Cloud-sync schema drift** between old and new clients | Low | Low | No new columns → snapshot format unchanged; bibcode keys are opaque strings to the merge; old clients sync them harmlessly (links on old UIs may 404, data intact). |
| R7 | **Chat quality for non-arXiv papers** is abstract-grounded only (no full text) | Low | Certain | Reader pane states "Abstract-only — no arXiv full text for this paper"; prompt already falls back to abstract. |
| R8 | **Injected-script refactor on shared pages** (search page button wiring) regresses Daily/Recent/Database pages | Medium | Low | `data-paper-id` is additive with a fallback to the current href parsing; regression tests cover the existing pages; the daily-page HTML generator is untouched in v1. |
| R9 | **Extension service-worker/panel messaging** complexity (settings lookup, CSP of scixplorer.org) | Low | Medium | Panel fetches localhost directly (server already sends `Access-Control-Allow-Origin: *`); messaging limited to one settings request; no `eval`/remote code (MV3-compliant). |
| R10 | **Android copy drift** if `copy_python.sh` is forgotten | Low | Medium | Copy regen is an explicit phase-5 step; `strip_chat.py` is idempotent. |
| R11 | Server-side fetch of scixplorer.org HTML (accidental future misuse) | Low | Low | Never implemented; WAF makes it impossible anyway — resolve uses the JSON API only. |

**Rollback:** everything is branch-isolated on `dev_sci`. No destructive schema change (no
migration, no column drops). Saved bibcode-keyed rows created on the branch remain valid
records on old versions (their arXiv links 404, nothing breaks). The extension overlay can be
disabled by removing the manifest entry without server changes.

## 5. Open items to verify during implementation

- Exact scixplorer paper-route shapes in a real browser (`/abs/` vs `/detail/` vs query-param
  variants) — drive with the live site.
- Whether `identifier:` lookup should prefer the published (journal) record over the arXiv record
  for papers that exist in both (today it returned the arXiv record) — key rule keeps the arXiv ID
  either way; only the displayed metadata differs.
- Whether the daily-page generator (in `arxiv_daily_ranker_html.py`) should emit `data-paper-id`
  in the same phase or a fast-follow (v1 keeps href parsing as fallback there).
