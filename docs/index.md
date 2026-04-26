<p align="center">
  <img src="logo0.5.jpg" width="240" alt="ArXistant logo">
</p>

<h1 align="center">ArXistant</h1>

<p align="center">
  <strong>AI Research Assistant for arXiv Literature Monitoring</strong><br>
  Monitors arXiv, filters papers by your research interests, and delivers research-oriented analysis via Feishu.
</p>

---

## What It Does

ArXistant acts as a research assistant that performs first-pass scientific judgment on arXiv papers. It is not just a summary bot — for each relevant paper, it analyzes what problem it addresses, what is genuinely new, how it compares with prior work, and whether the reader's understanding of key scientific points is sound.

**Core workflow:**

1. **Fetch** new papers from arXiv categories on a daily schedule
2. **Filter** by relevance to your configured knowledge tree
3. **Scan** for quality score and topic relevance
4. **Read** with full-text analysis and executive summaries
5. **Verify** understanding through logic chain reconstruction, Feynman tests, and gap identification
6. **Report** via interactive Feishu cards or Markdown files

## Features

### Paper Discovery
- **Scheduled fetching** — Automatically collects new papers on weekdays
- **Date-specific fetch** — `/fetch 2026-04-26` to get papers from any day within the past week
- **Keyword pre-filtering** — Instant relevance matching against your knowledge tree, no LLM needed

### Deep Analysis
- **Quick scan** (`/scan`) — Quality score (1-5), tree node links, reading recommendation
- **Executive reading** (`/read`) — Full-text parsing from arXiv HTML, structured notes with key findings, methodology, tree connections
- **Understanding verification** — Multi-stage pipeline triggered automatically by `/read`:
  - Scientific point extraction
  - Claim-evidence-reasoning chain reconstruction
  - Logic chain critique
  - Feynman explanation test + critique
  - Gap identification and iteration
  - Understanding certificates with per-level scores

### Interactive Knowledge Tree
- **Build** your research interest hierarchy through conversation (`/build`)
- Papers are automatically linked to tree nodes by relevance
- Candidate node proposals from LLM analysis
- YAML-based import/export

### Feishu Integration
- Interactive card-based UI with button actions
- In-place card updates for live progress reporting
- Animated status indicators
- Multi-turn chat with context
- Scheduled daily reports pushed to chat

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

**Tech stack:**

| Component | Technology |
|-----------|-----------|
| Language | Python 3.10+ |
| LLM | OpenAI-compatible API (GLM) |
| Storage | SQLite with schema migrations |
| Messaging | Feishu (lark-oapi WebSocket SDK) |
| arXiv | arXiv API + listing page scraping |
| HTML Parsing | BeautifulSoup + lxml (LaTeXML output) |

## Bot Commands

| Command | Description |
|---------|-------------|
| `/fetch [date]` | Collect papers, show keyword-filtered list with action buttons |
| `/scan <arxiv_id>` | Quick scan — quality score + tree relevance |
| `/read <arxiv_id>` | Full reading — executive summary + understanding verification |
| `/report [cat]` | All recent papers grouped by category with status badges |
| `/tree` | Display knowledge tree |
| `/build` | Interactive tree generation from your interests |
| `/prefs` | Show learned preference weights |
| `/help` | Command reference |

## Quick Setup

### Prerequisites

- Python 3.10+
- A Feishu (Lark) app with bot permissions
- An OpenAI-compatible LLM API key

### Install

```bash
git clone https://github.com/jyshangguan/ArXistant.git
cd ArXistant
pip install -r requirements.txt
```

### Configure

Copy `.env.example` to `.env` and fill in your API keys:

```
GLM_API_KEY=your-key
FEISHU_APP_ID=your-app-id
FEISHU_APP_SECRET=your-app-secret
```

### Run

```bash
# Feishu bot (interactive)
python -m src.bot.server

# CLI pipeline (one-shot)
python -m src.main
```

## Documentation

Developer guide is available locally at `dev/dev_notes/DOCS.md`.

## License

[MIT](LICENSE)
