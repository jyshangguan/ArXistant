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

## Phase 3: Candidate Review Workflow (in progress)

- [ ] Run pipeline end-to-end with real arXiv data
- [ ] Review and confirm/reject candidate nodes in `data/candidates.yaml`
- [ ] Verify tree growth across multiple runs
- [ ] Verify idempotency (second run skips already-analyzed papers)

## Future Plans

- [ ] Detailed paper reading (title/abstract → full text analysis)
- [ ] Paper-level and topic-level knowledge notes
- [ ] User feedback loop (accept/reject papers → adapt scoring)
- [ ] Feishu/WeChat report delivery
- [ ] Vector database for semantic paper search
- [ ] Multi-user support
