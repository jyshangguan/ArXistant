# Development Log

## 2026-04-21 — LLM Model Upgrade: GLM-4-flash → GLM-4.7-flash

### What was done
- Upgraded default LLM model from `glm-4-flash` to `glm-4.7-flash`
- Updated in 3 locations: `src/config.py` (default + fallback), `config/settings.yaml`, `.env.example`

### Motivation
- GLM-4-flash consistently fails to produce valid JSON for ~14% of batches (batch 2/7 failed in both e2e runs, dropping 6 papers each time)
- GLM-4.7-flash is the newest free model from Zhipu AI with better instruction-following, 200K context (up from 128K), 128K max output (up from 16K), same API endpoint

### Files changed
- `src/config.py` — default `llm_model` and env fallback
- `config/settings.yaml` — `model` value
- `.env.example` — comment showing new default

### Verification needed
- Delete `data/arxistant.db` and re-run `python -m src.main`
- Confirm no "Could not parse analysis response as JSON" warnings
- Confirm 42/42 papers analyzed (not 36/42)

---

## 2026-04-21 — Phase 2: SQLite Storage + Knowledge Tree

### What was done
- Replaced stateless pipeline with persistent SQLite-backed system
- 7 new files, 7 modified files:
  - `src/storage.py` — SQLite schema (5 tables), CRUD for papers/knowledge_tree/paper_tree_links/candidate_nodes, candidate YAML I/O
  - `src/tree.py` — Load knowledge tree from nested YAML, import into DB with level/category inheritance, format tree for LLM prompts
  - `src/analyze.py` — Two-axis analysis: quality score (1-5) + per-topic relevance (1-5), optional candidate node proposals (max 1 per paper, only if quality >= 3)
  - `config/knowledge_tree.yaml` — Initial tree: Galactic Dynamics (3 levels, 5 nodes) and High-Energy Transients (3 levels, 6 nodes)
  - `src/main.py` — Rewired to: DB init → tree import → candidate review → collect → store → analyze → report. Falls back to legacy filter.py when `db_path` is empty
  - `src/report.py` — New `generate_tree_report()` with quality distribution, papers grouped by tree node, candidate proposals section
  - `src/config.py` + `config/settings.yaml` — Added `db_path` and `candidates_path` settings
  - `tests/test_storage.py` (28 tests), `tests/test_tree.py` (12 tests), `tests/test_analyze.py` (14 tests), updated conftest, test_config, test_report

### Key design decisions
- Papers stored in DB with dedup by arxiv_id; quality_score NULL until analyzed
- Knowledge tree uses adjacency list (parent_id), categories inherited from ancestors
- Candidate nodes stored separately until user confirms/rejects via `data/candidates.yaml`
- Tree links only created for relevance >= 3
- Legacy `filter.py` pipeline preserved as fallback when `db_path: ""`

### Test results
- 131 tests pass (67 new), 0 failures
- Tests cover: DB init, tree CRUD, paper CRUD, paper-tree links, candidate CRUD, candidate YAML I/O, tree YAML loader, tree import, tree prompt formatting, analysis parsing, analyze_papers with mocked LLM, tree report generation

### Commit
- `16ec2b6` Add SQLite storage, hierarchical knowledge tree, and two-axis paper analysis

---

## 2026-04-21 — Phase 2 end-to-end run + bug fixes

### End-to-end results
- 42 papers collected (27 from astro-ph.GA, 15 from astro-ph.HE)
- 36/42 analyzed successfully (6 lost to 1 malformed LLM response in batch 2/7)
- Quality distribution: 1 quality-5, 16 quality-4, 18 quality-3, 1 quality-2
- 11 tree nodes imported, papers linked to multiple nodes as expected
- 0 candidate nodes proposed by LLM
- DB created at `data/arxistant.db`, report at `reports/2026-04-21.md`

### Bugs found and fixed
- **Paper duplication in report**: `generate_tree_report()` merged both DB data and in-memory `analysis_results`, doubling every paper. Fixed by removing the `analysis_results` parameter — by the time the report runs, all data is already stored in the DB.
- **`Papers analyzed` count was inflated**: Was `len(papers) + len(new_results)` which double-counted. Fixed to `len(papers)`.

### Open issues from this run
- Batch 2/7 consistently returns malformed JSON from GLM-4-flash (reproducible across two runs). 6 papers lost per run. Needs investigation — possibly the response is too long or contains Unicode that breaks parsing.
- No candidate nodes proposed. The LLM may need a nudge in the system prompt, or quality >= 3 + relevance links to existing nodes may be too strict a filter for proposals.

### Commit
- `460becf` Fix paper duplication in tree report and update dev docs

---

## 2026-04-21 — Phase 1.5: Unit Tests + Score Inflation Fix

### What was done
- Added comprehensive unit tests for all existing modules (67 tests)
- Fixed score inflation in filter.py: stricter scoring prompt with expected distribution, cumulative decision criteria, self-check if >40% scored >= 3
- Switched arXiv categories from astro-ph.EP/SR to astro-ph.GA/HE
- Raised relevance threshold from 3 to 4

### Test results
- 64 tests pass across test_collector, test_config, test_filter, test_llm_client, test_report

### Commit
- `12e7974` Add unit tests, fix score inflation, switch to astro-ph.GA/HE categories

---

## 2026-04-21 — Initial MVP Implementation

### What was done
- Created full MVP pipeline: config → collect → filter → report
- 10 new files:
  - `config/topics.yaml` — 2 research topics (Galactic Dynamics, High-Energy Transients)
  - `config/settings.yaml` — runtime settings (GLM API, arXiv limits, filter params)
  - `src/__init__.py` — package marker
  - `src/config.py` — `Topic` and `Settings` dataclasses, YAML loading, .env support
  - `src/collector.py` — `RawPaper` dataclass, arXiv fetching by category with client-side date filter
  - `src/llm_client.py` — OpenAI-compatible API wrapper (supports Zhipu GLM)
  - `src/filter.py` — batched LLM relevance scoring (6 papers/call), JSON response parsing with fallbacks
  - `src/report.py` — Markdown report generation with summary stats and paper details
  - `src/main.py` — pipeline orchestrator
  - `requirements.txt` — arxiv, PyYAML, openai, python-dotenv
  - `.env.example` — API key template

### Bugs fixed
- arXiv API `submittedDate` query caused HTTP 500. Switched to client-side date filtering.

### Test results
- Fetched 28 papers from astro-ph.EP (17) and astro-ph.SR (11) within 3-day window
- LLM scored 18/28 as relevant (score >= 3)
- Report generated at `reports/2026-04-21.md` (266 lines)

### Commit
- `f86bdf7` Initial MVP: arXiv paper fetch, LLM relevance filter, Markdown daily report
