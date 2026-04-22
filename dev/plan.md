# Development Plan

## Phase 1: MVP — Daily Report Pipeline (done)

- [x] Create config files and skeleton (`config/topics.yaml`, `config/settings.yaml`, `src/__init__.py`, `src/config.py`, `requirements.txt`, `.env.example`)
- [x] Implement collector module (`src/collector.py`)
- [x] Implement LLM client module (`src/llm_client.py`)
- [x] Implement filter module (`src/filter.py`)
- [x] Implement report module (`src/report.py`)
- [x] Implement main pipeline (`src/main.py`)
- [x] End-to-end test: 28 papers fetched, 18 relevant, report generated

## Phase 1.5: Tests + Fixes (done)

- [x] Add unit tests for all modules (67 tests)
- [x] Fix score inflation: stricter system prompt, raised relevance threshold to 4
- [x] Switch categories to astro-ph.GA and astro-ph.HE
- [x] Add score distribution anti-inflation self-check

## Phase 2: SQLite Storage + Hierarchical Knowledge Tree (done)

- [x] Design database schema (5 tables: schema_version, knowledge_tree, papers, paper_tree_links, candidate_nodes)
- [x] Create storage module (`src/storage.py`) — DB init, CRUD, candidate YAML I/O
- [x] Create tree module (`src/tree.py`) — YAML loader, DB import, prompt formatting
- [x] Create analysis module (`src/analyze.py`) — quality + per-topic relevance scoring, candidate proposals
- [x] Add `db_path` and `candidates_path` to Settings and `config/settings.yaml`
- [x] Create initial knowledge tree (`config/knowledge_tree.yaml`) — 2 roots, 11 nodes
- [x] Update pipeline (`src/main.py`) — dual-mode: tree-aware (default) or legacy fallback
- [x] Update report (`src/report.py`) — tree-aware report with papers grouped by node, candidate proposals section
- [x] Write tests (67 new tests, 131 total)
- [x] Verify: `pytest tests/ -v` all pass

## Phase 3: Hardening and Candidate Review (in progress)

- [x] Run pipeline end-to-end with real arXiv data (36/42 analyzed, report generated)
- [x] Fix paper duplication bug in tree report
- [x] Verify idempotency (second run skips already-analyzed papers)
- [x] Fix GLM-4-flash batch parse failure (~14% papers dropped per run)
  - [x] Upgrade model to GLM-4.7-flash (better instruction-following, 200K context, 128K max output)
  - [x] Add rate-limit retry with exponential backoff in `analyze_papers`
  - [x] ~~Consider reducing batch size from 6 to 4 for longer abstracts~~ — unnecessary with GLM-4.7-flash
  - [ ] Log raw LLM response on parse failure for debugging (minor, `_parse_analysis_response` returns `[]` silently)
- [ ] Tune candidate node proposal prompt (currently 0 proposals generated) — low priority, revisit after 100+ papers analyzed with new model
- [ ] Run pipeline multiple days to verify tree growth and candidate review workflow
- [x] Verify re-analysis of papers that failed in previous runs — already works: `quality_score IS NULL` query picks up failed papers on next run

## Phase 4: Quality Improvements

- [ ] Paper cross-references in report (avoid repeating full entry under each node)
- [x] Upgrade LLM model (user requested GLM-5 or equivalent for speed) — done: GLM-4.7-flash
- [ ] Add abstract truncation in LLM prompt for very long abstracts (current prompt can get very large)
- [ ] Score inflation check: quality distribution may still be top-heavy (1 quality-5, 16 quality-4 out of 36)

## Future Plans

- [x] Detailed paper reading (title/abstract → full text analysis) — done: scan_paper + read_paper in Phase 5
- [x] Paper-level and topic-level knowledge notes — done: read_paper stores structured notes in DB (Phase 5)
- [ ] User feedback loop (accept/reject papers → adapt scoring)
- [ ] Feishu/WeChat report delivery
- [ ] Vector database for semantic paper search
- [ ] Multi-user support

## Phase 5: Multi-Level Paper Reading Tools (done)

### Phase 5A: Foundation (done)
- [x] Create `src/tools/` package with shared types (`ScanResult`, `ReadingNote`, `ParsedPaper`, `FigureInfo`)
- [x] Create `html_parser.py` — fetch arXiv HTML, extract structured text + figure metadata
- [x] Add `beautifulsoup4`, `lxml`, `requests` to requirements.txt
- [x] Write tests for HTML parser (16 tests)

### Phase 5B: Tool 1 — scan_paper (done)
- [x] Create `prompts.py` with `SCAN_PAPER_PROMPT` and `READ_PAPER_PROMPT`
- [x] Create `scan_paper.py` — quick relevance scan using arXiv API + LLM
- [x] Write tests for scan_paper (8 tests)

### Phase 5C: Tool 2 — read_paper (done)
- [x] Add reading settings (`max_text_chars`, `html_timeout`) to config
- [x] Schema V2 migration: add `reading_notes` table to storage.py
- [x] Add `reading_notes` CRUD (`get_reading_note`, `upsert_reading_note`, `delete_reading_note`)
- [x] Create `read_paper.py` — full-text reading with structured notes + DB caching
- [x] Write tests for read_paper (11 tests)

### Phase 5D: Deferred tool stubs (done)
- [x] Create `analyze_figure.py` stub (raises NotImplementedError)
- [x] Create `search_references.py` stub (raises NotImplementedError)

### Phase 5E: MCP integration (not started)
- [ ] Create `src/mcp_server.py` wrapping all 4 tools
- [ ] Wire up scan_paper and read_paper as MCP tools
- [ ] Test end-to-end with real paper

## Phase 6: Fast Fetch + On-Demand Analysis (done)

- [x] Add `collect_and_store()` in `src/main.py` — arXiv collect + store, no LLM
- [x] Add `keyword_pre_filter()` in `src/filter.py` — extract keyword phrases from tree nodes, match against papers
- [x] Add `get_recent_papers()` in `src/storage.py` — papers with `is_analyzed`/`is_read` flags
- [x] Redesign `_handle_fetch()` — fast pipeline: collect → keyword filter → list card with [Scan]/[Read] buttons
- [x] Update `_handle_report()` — show all recent papers with status badges (NEW/SCANNED/READ), sorted by status
- [x] Add `build_fetch_list_card()` in `card_builder.py` — relevant papers with per-paper buttons
- [x] Update `build_report_card()` — status indicators, conditional quality display
- [x] Revert aggressive rate-limit workarounds in `analyze.py` (max_retries 5→3, base_delay 30→15)
- [x] Add `pre_filter_max: int = 30` setting, revert `batch_delay` to 5
- [x] Keep `run_collect_and_analyze()` for scheduler (overnight batch still does LLM analysis)
- [x] Update help card descriptions
- [x] All 251 tests pass
