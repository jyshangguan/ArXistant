<p align="center">
  <img src="doc/logo0.5.jpg" width="200" alt="ArXistant logo">
</p>

<h1 align="center">ArXistant</h1>

<p align="center">
  <strong>AI Research Assistant for arXiv Literature Monitoring</strong>
</p>

<p align="center">
  Monitors arXiv, filters papers by your research interests, analyzes them with LLM, and delivers reports via Feishu.
</p>

---

## Features

- **Automated paper fetching** — Collects new papers from arXiv categories on schedule (weekdays 10:30 AM)
- **Smart filtering** — Keyword matching against your knowledge tree for instant relevance sorting
- **Deep analysis** — Quick scan (quality score + tree links) and executive reading notes with full-text parsing
- **Understanding verification** — Multi-stage pipeline that verifies genuine comprehension of scientific papers (logic chains, Feynman test, gap identification)
- **Interactive knowledge tree** — Build and refine your research interest hierarchy through conversation
- **Feishu integration** — Interactive bot with card-based UI, button actions, and scheduled reports
- **Preference learning** — Adapts to your research interests over time

This is not just a summary bot — it acts like a research assistant performing first-pass scientific judgment.

## Architecture

```
arXiv API ──► collector ──► storage (SQLite)
                              │
                              ▼
                    keyword filter (fast, no LLM)
                              │
                              ▼
                    LLM scan / read / verify
                              │
                              ▼
                    Feishu cards / Markdown reports
```

**Tech stack:** Python 3.10+, OpenAI-compatible LLM API (GLM), SQLite, Feishu (lark-oapi WebSocket)

## Project Structure

```
ArXistant/
├── config/
│   ├── knowledge_tree.yaml   # Hierarchical research interests
│   ├── settings.yaml         # Runtime settings (LLM, arXiv, Feishu)
│   └── topics.yaml           # Topic definitions
├── dev/                      # Development notes
├── doc/
│   ├── DOCS.md               # Developer guide
│   └── logo0.5.jpg           # App logo
├── docs/                     # GitHub Pages site
├── reports/                  # Generated reports (git-ignored)
├── src/
│   ├── config.py             # Settings, Topic loading
│   ├── collector.py          # arXiv paper fetching
│   ├── filter.py             # Keyword pre-filter + LLM scoring
│   ├── analyze.py            # Two-axis paper analysis
│   ├── storage.py            # SQLite CRUD, schema migrations
│   ├── tree.py               # Knowledge tree management
│   ├── report.py             # Markdown report generation
│   ├── llm_client.py         # OpenAI-compatible LLM wrapper
│   ├── main.py               # CLI pipeline orchestrator
│   ├── bot/                  # Feishu bot service
│   │   ├── server.py         # WebSocket entry point
│   │   ├── command_router.py # Command parsing
│   │   ├── command_handler.py# Command execution
│   │   ├── card_builder.py   # Interactive card UI
│   │   ├── feishu_client.py  # Feishu API client
│   │   ├── scheduler.py      # Cron-based scheduled reports
│   │   └── ...
│   └── tools/                # Paper analysis tools
│       ├── scan_paper.py     # Quick relevance scan
│       ├── read_paper.py     # Executive reading summary
│       ├── html_parser.py    # arXiv HTML parsing
│       ├── understanding_verifier.py  # Understanding verification pipeline
│       └── ...
├── tests/
├── .env.example
├── requirements.txt
└── README.md
```

## Setup

### Prerequisites

- Python 3.10+
- A Feishu (Lark) app with bot permissions
- An OpenAI-compatible LLM API key (e.g., Zhipu GLM)

### Install

```bash
conda activate llm
pip install -r requirements.txt
```

### Configure

1. Copy `.env.example` to `.env` and fill in your API keys
2. Edit `config/settings.yaml` for your preferences
3. Edit `config/knowledge_tree.yaml` for your research interests

### Run

**CLI pipeline** (one-shot fetch + analyze):

```bash
conda activate llm
python -m src.main
```

**Feishu bot** (interactive, long-running):

```bash
conda activate llm
python -m src.bot.server
```

## Feishu Bot Commands

| Command | Description |
|---------|-------------|
| `/fetch [date]` | Collect papers and show keyword-filtered list |
| `/scan <arxiv_id>` | Quick scan — quality score + tree relevance |
| `/read <arxiv_id>` | Full reading — executive summary + understanding verification |
| `/report [cat]` | Show all recent papers with status badges |
| `/tree` | Display knowledge tree |
| `/build` | Interactive tree generation from interests |
| `/prefs` | Show learned preference weights |
| `/help` | Command reference |

## License

Private project.
