# ArXistant Developer Guide

Comprehensive reference for the ArXistant codebase. For setup instructions, see [README.md](README.md).

## Overview

ArXistant is an AI research assistant that monitors arXiv, filters papers by research interests, and provides analysis. It has two modes:

- **CLI Pipeline** (`python -m src.main`) — fetch, filter, analyze, generate Markdown reports
- **Feishu Bot** (`python -m src.bot.server`) — interactive bot with commands, cards, and scheduled reports

Tech stack: Python 3.10+, OpenAI-compatible LLM API (currently GLM), SQLite, Feishu (lark-oapi).

## Architecture

```
arXiv API ──► collector ──► storage (SQLite)
                              │
                              ▼
                         filter (keyword/LLM)
                              │
                              ▼
                         analyze (LLM) ──► quality_score + tree_links
                              │
                              ▼
                    report (Markdown) / Feishu card
```

**Module layout:**

```
src/
├── config.py          Settings, Topic loading
├── collector.py       arXiv paper fetching
├── filter.py          keyword pre-filter + LLM relevance scoring
├── analyze.py         paper analysis (two-axis: quality + tree relevance)
├── storage.py         SQLite CRUD, schema migrations
├── tree.py            knowledge tree import/export
├── report.py          Markdown report generation
├── llm_client.py      OpenAI-compatible LLM wrapper
├── main.py            pipeline orchestrator
├── bot/               Feishu bot service
│   ├── server.py         FastAPI + WebSocket entry point
│   ├── command_router.py  /command parsing
│   ├── command_handler.py command execution
│   ├── card_builder.py    Feishu interactive cards
│   ├── feishu_client.py   Feishu API client
│   ├── conversation.py    multi-turn LLM conversation
│   ├── scheduler.py       daily cron fetch push
│   ├── preference_store.py user preference tracking
│   ├── session_store.py   chat history
│   ├── debug.py           error recording
│   ├── build_engine.py    interactive tree building
│   └── prompts.py         bot system prompt
└── tools/             paper-level analysis tools
    ├── scan_paper.py      quick relevance scan
    ├── read_paper.py      executive reading summary
    ├── html_parser.py     arXiv HTML → structured text
    ├── types.py           data classes for tool outputs
    ├── prompts.py         tool system prompts
    ├── search_references.py  (stub)
    └── analyze_figure.py     (stub)
```

## Module Reference

### src/config.py

Loads YAML configuration and merges environment variable overrides.

**Dataclasses:**

```python
@dataclass
class Topic:
    name: str
    description: str
    keywords: list[str]          # default []
    categories: list[str]        # default []

@dataclass
class Settings:
    # LLM
    llm_provider: str            # "openai_compatible"
    llm_model: str               # default "glm-4.7-flash"
    llm_base_url: str            # default Zhipu URL
    llm_api_key: str
    llm_temperature: float        # default 0.1
    # arXiv
    max_results: int             # default 100
    days_back: int               # default 3
    # Filter
    batch_size: int              # default 6
    batch_delay: float           # default 5
    relevance_threshold: int     # default 4
    pre_filter_max: int          # default 30
    # Database
    db_path: str                 # default "data/arxistant.db"
    # Reading
    max_text_chars: int          # default 80000
    executive_read_max_chars: int # default 30000
    html_timeout: int            # default 30
    # Feishu + Bot
    feishu_app_id: str
    feishu_app_secret: str
    feishu_verification_token: str
    feishu_encrypt_key: str
    feishu_bot_name: str         # default "ArXistant"
    target_chat_id: str
    session_max_messages: int    # default 20
    report_cron: str             # default "30 10 * * 1-5"
```

**Functions:**
- `load_topics(path) -> list[Topic]` — from YAML
- `load_settings(path) -> Settings` — from YAML + .env overrides (GLM_API_KEY, LLM_MODEL, LLM_BASE_URL, FEISHU_*)

### src/collector.py

Fetches papers from arXiv. Two modes:
- **Search API** (`arxiv.Search`) for recent papers by category
- **Listing page** (`arxiv.org/list/{cat}/recent`) for specific dates (reliable for today's papers)

```python
@dataclass
class RawPaper:
    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    published: datetime
    categories: list[str]
    primary_category: str
    pdf_url: str
    entry_url: str

def collect_papers(topics, settings, target_date=None) -> list[RawPaper]
def _collect_papers_by_date(categories, target_date) -> list[RawPaper]
def _fetch_listing_ids(category, target_date) -> list[str]
```

### src/filter.py

Two filtering subsystems:

1. **`keyword_pre_filter(papers, conn, max_papers=30)`** — fast, no LLM. Matches paper titles/abstracts against knowledge tree node names and descriptions. Used by `/fetch` and scheduler.

2. **`filter_papers(papers, topics, settings)`** — LLM-based batch scoring (legacy pipeline).

```python
@dataclass
class PreFilteredPaper:
    paper: StoredPaper
    match_count: int
    matched_keywords: list[str]
    status: str              # "new", "scanned", "read"

@dataclass
class RelevantPaper:
    paper: RawPaper
    score: int               # 1-5
    matched_topic: str
    reason: str
```

### src/analyze.py

LLM-based paper analysis with two-axis scoring.

```python
@dataclass
class AnalysisResult:
    paper: RawPaper
    quality_score: int       # 1-5
    quality_reason: str
    tree_links: list[dict]   # [{node_name, relevance_score, relevance_reason}]
    candidate_node: dict | None

def analyze_papers(papers, tree_prompt, settings) -> list[AnalysisResult]
```

### src/tree.py

Knowledge tree management.

```python
def load_tree_yaml(path) -> list[dict]
def import_tree_from_yaml(conn, tree_path) -> int
def import_tree_from_yaml_force(conn, tree_path) -> int   # clears existing
def format_tree_for_prompt(conn) -> str
def derive_topics_from_tree(conn) -> list[Topic]
def build_category_groups(conn) -> dict[str, str]           # short→group_name
def get_root_categories(conn) -> list[str]
```

### src/storage.py

SQLite wrapper with schema v4, migrations, and full CRUD for all tables.

**Key dataclasses:** `StoredPaper`, `TreeNode`, `PaperTreeLink`, `CandidateNode`

**Key functions:**

| Category | Functions |
|----------|-----------|
| Init | `init_db(db_path) -> Connection` |
| Papers | `paper_exists()`, `insert_paper()`, `insert_papers_batch()`, `get_paper()`, `get_recent_papers(days_back=3, target_date=None)`, `get_unanalyzed_papers()`, `get_analyzed_papers()`, `count_papers()`, `update_paper_analysis()` |
| Tree | `insert_tree_node()`, `get_tree_node_by_name()`, `get_all_tree_nodes()`, `get_tree_children()`, `count_tree_nodes()`, `clear_all_tree_nodes()` |
| Links | `upsert_paper_tree_link()`, `get_links_for_paper()`, `get_papers_for_node()` |
| Candidates | `insert_candidate()`, `get_pending_candidates()`, `confirm_candidate()`, `reject_candidate()`, `write_candidates_yaml()`, `read_candidates_yaml()` |
| Notes | `get_reading_note()`, `upsert_reading_note()`, `delete_reading_note()` |
| Sessions | `get_build_session()`, `upsert_build_session()`, `delete_build_session()` |

### src/llm_client.py

Thin wrapper around `openai.OpenAI`. Configured with 120s timeout and `max_retries=0` (retry is handled by callers).

```python
def create_client(settings) -> OpenAI
def chat_completion(client, model, system_prompt, user_prompt, temperature=0.1) -> str
def chat_completion_messages(client, model, messages, temperature=0.1) -> str
```

### src/main.py

Pipeline orchestrator.

```python
def collect_and_store(conn, settings, topics=None, target_date=None) -> dict
    # Returns {"papers_collected": int, "papers_new": int}

def run_collect_and_analyze(conn, settings, topics=None) -> dict
    # Collect, store, analyze, generate report. Returns stats dict.
```

### src/bot/

Feishu bot service. See [Feishu Bot Commands](#feishu-bot-commands) for command details.

### src/tools/

Paper-level analysis tools.

```python
# scan_paper.py
def scan_paper(arxiv_id, settings, db_conn) -> ScanResult

# read_paper.py
def read_paper(arxiv_id, settings, db_conn) -> ReadingNote

# html_parser.py
def fetch_and_parse(arxiv_id, timeout=30) -> ParsedPaper

# types.py dataclasses:
ScanResult, ReadingNote, TreeLink, TreeConnection, ParsedPaper, FigureInfo
```

## Configuration

### config/settings.yaml

| Section | Key | Default |
|---------|-----|---------|
| llm | provider, model, base_url, temperature | openai_compatible, glm-4.7-flash, Zhipu URL, 0.1 |
| arxiv | max_results, days_back | 100, 3 |
| filter | batch_size, batch_delay, relevance_threshold, pre_filter_max | 6, 5, 4, 30 |
| database | path | data/arxistant.db |
| reading | max_text_chars, executive_read_max_chars, html_timeout | 80000, 30000, 30 |
| feishu | app_id, app_secret, verification_token, encrypt_key | from .env |
| bot | target_chat_id, session_max_messages, report_cron | "", 20, "30 10 * * 1-5" |

### config/topics.yaml

```yaml
topics:
  - name: Topic Name
    description: Detailed description
    keywords: [keyword1, keyword2]
    categories: [astro-ph.GA, astro-ph.HE]
```

### config/knowledge_tree.yaml

```yaml
tree:
  - name: Parent Node
    description: Description
    categories: [astro-ph.GA]
    children:
      - name: Child Node
        description: Description
        categories: [astro-ph.GA]
```

### .env

```
GLM_API_KEY=your-key
LLM_MODEL=glm-5-turbo           # optional override
LLM_BASE_URL=...                # optional override
FEISHU_APP_ID=...
FEISHU_APP_SECRET=...
FEISHU_VERIFICATION_TOKEN=...
```

## Database Schema

Schema version: **4**. Tables:

| Table | Purpose |
|-------|---------|
| `schema_version` | Tracks applied migrations |
| `knowledge_tree` | Hierarchical research interests (id, name, description, parent_id, level, status, source, categories, created_at) |
| `papers` | arXiv papers (arxiv_id PK, title, authors, abstract, published, categories, primary_category, pdf_url, entry_url, quality_score, quality_reason, first_seen_at, last_analyzed_at) |
| `paper_tree_links` | Many-to-many paper↔tree (paper_id, tree_node_id, relevance_score, relevance_reason, linked_at) |
| `candidate_nodes` | Proposed new tree nodes (id, name, description, parent_id, status, source_paper_ids, proposed_at, reviewed_at) |
| `reading_notes` | Executive reading summaries (arxiv_id UNIQUE, title, full_text_hash, summary, key_findings, methodology, results, tree_connections, unfamiliar_concepts, raw_notes, created_at, updated_at) |
| `chat_sessions` | Feishu chat sessions (chat_id PK, created_at, updated_at) |
| `session_messages` | Chat history (id, chat_id, role, content, created_at) |
| `user_preferences` | Learned preference weights (id, tree_node_id UNIQUE, weight REAL, interaction_count, updated_at) |
| `build_sessions` | Tree building state (chat_id PK, stage, interests, tree_yaml, created_at, updated_at) |

**reading_notes column reuse mapping** (non-obvious):
- DB `summary` stores tool's `background`
- DB `methodology` stores tool's `evaluation`
- DB `results` stores tool's `authors`

## Feishu Bot Commands

| Command | Handler | Description |
|---------|---------|-------------|
| `/scan <arxiv_id>` | `_handle_scan` | Quick scan → quality score + tree links |
| `/read <arxiv_id>` | `_handle_read` | Full reading → structured notes (auto-scans if needed) |
| `/fetch [date]` | `_handle_fetch` | Collect papers → keyword-filtered list with [Scan]/[Read] buttons |
| `/report [cat]` | `_handle_report` | All recent papers grouped by category with status |
| `/tree` | `_handle_tree` | Display knowledge tree |
| `/build` | `_handle_build` | Interactive tree generation from interests |
| `/help` | `_handle_help` | Command reference |
| `/prefs` | `_handle_prefs` | Show learned preference weights |
| `/reset` | `_handle_reset` | Clear chat session |
| `/debug [on\|off]` | `_handle_debug` | Show errors or toggle verbose mode |
| *free text* | `_handle_chat` | Multi-turn LLM conversation |

**Card callbacks:** `read`, `scan`, `report`, `build_accept`, `build_reject`

## Tools API

```python
from src.tools.scan_paper import scan_paper
result = scan_paper("2604.12345v1", settings, db_conn)
# ScanResult(arxiv_id, title, quality_score, quality_reason,
#            tree_links=[TreeLink(node_name, relevance_score, relevance_reason)],
#            recommend_reading, rationale)

from src.tools.read_paper import read_paper
note = read_paper("2604.12345v1", settings, db_conn)
# ReadingNote(arxiv_id, title, authors, background, key_findings,
#             evaluation, tree_connections=[TreeConnection(node_name, connection)],
#             cached)

from src.tools.html_parser import fetch_and_parse, PaperHtmlUnavailableError
parsed = fetch_and_parse("2604.12345v1", timeout=30)
# ParsedPaper(arxiv_id, title, abstract, sections, figures,
#              full_text_markdown, full_text_hash)
```

## Extension Points

**New bot command:** add pattern in `command_router._PATTERNS`, handler in `command_handler.py`, card in `card_builder.py`.

**New tool:** create module in `src/tools/`, register patterns in `conversation.py` (`_SCAN_PATTERN`/`_READ_PATTERN`) + handler in `_execute_tool`.

**New LLM provider:** change `llm_base_url` and `llm_api_key` in settings. The `openai` package handles any OpenAI-compatible API.

**New DB table:** add to `_SCHEMA_SQL`, bump `SCHEMA_VERSION`, add migration in `_MIGRATIONS`.

**New scheduled job:** add APScheduler job in `scheduler.py`.

## Testing

```bash
conda run -n llm python -m pytest tests/ -v
```

**Fixtures** (in `tests/conftest.py`): `sample_topic`, `sample_topics`, `sample_paper`, `sample_papers`, `sample_settings`, `bot_settings`, `db_conn` (in-memory), `db_conn_with_tree`, `sample_html`

## Running

```bash
# CLI pipeline
conda run -n llm python -m src.main

# Feishu bot
conda run -n llm uvicorn src.bot.server:app --host 0.0.0.0 --port 8000
```

**Logs:** `data/logs/bot.log` (rotating, 5MB, 3 backups)
