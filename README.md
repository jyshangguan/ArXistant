# ArXistant

An AI research assistant that monitors arXiv, filters papers by your research interests, and generates research-oriented daily reports.

## What it does (eventually)

- Regularly monitor arXiv for new papers
- Detect papers relevant to your configured research topics
- Produce short daily reports with optional detailed analysis
- For each relevant paper, analyze:
  - what problem it addresses
  - what is genuinely new
  - how it compares with prior work
  - possible weaknesses or open questions
  - relevance to your interests
- Learn your preferences over time through feedback
- Send reports to messaging platforms (e.g., Feishu)

This is not just a summary bot — it acts like a research assistant performing first-pass scientific judgment.

## Current MVP scope

The current prototype focuses on:

1. Read topic configuration from a local YAML file
2. Fetch recent papers from arXiv
3. Filter papers by relevance to configured topics
4. Generate a local Markdown daily report

**Not in scope yet** unless explicitly requested:
- Feishu bot / messaging integration
- Vector database
- Multi-user support
- Production deployment

## Project structure

```
ArXistant/
├── config/
│   ├── topics.yaml   # Research topic definitions (name, description, keywords, categories)
│   └── settings.yaml # Runtime settings (LLM model, arXiv limits, report options)
├── dev/              # Development notes and feature workspaces
│   ├── plan.md       # Task plans and checklists
│   ├── problem.md    # Recurring problems and solutions
│   ├── develop_log.md # Log of changes per run
│   └── dev_notes/    # Personal notes, prompts, design docs
├── reports/          # Generated daily reports (git-ignored)
├── src/
│   ├── __init__.py   # Package marker
│   ├── config.py     # Load YAML configs, Topic/Settings dataclasses
│   ├── collector.py  # Fetch papers from arXiv by category (RawPaper)
│   ├── llm_client.py # OpenAI-compatible API wrapper
│   ├── filter.py     # Batch LLM relevance scoring (RelevantPaper)
│   ├── report.py     # Generate Markdown daily report
│   └── main.py       # Pipeline orchestrator
├── .env              # API keys (git-ignored)
├── .env.example      # API key template
├── requirements.txt
├── Claude.md
├── Claude.local.md
└── README.md
```

## Architecture principles

- **Modular**: each concern is a separate module (collect, filter, analyze, report, etc.)
- **Replaceable model providers**: designed to support Anthropic, Zhipu, Qwen, and others
- **Structured memory**: paper-level notes, topic-level notes, user preference notes — not a single mixed dump
- **Minimal dependencies**: prefer standard library and lightweight packages
- **SQLite + Markdown**: for early-stage storage and reporting

## Setup

### Prerequisites

- Python 3.10+
- Conda environment `llm` (or any environment with Python 3.10+)

### Install dependencies

```bash
conda activate llm
pip install -r requirements.txt
```

### Configure

1. Copy `.env.example` to `.env` and fill in your API keys
2. Edit `config/topics.yaml` to define your research topics
3. Adjust `config/settings.yaml` for preferences

### Run

```bash
conda activate llm
python -m src.main
```

This will fetch recent arXiv papers, filter by your topics, and generate a Markdown report in `reports/`.

## Development

- Use `conda activate llm` for all local runs
- Keep changes small, incremental, and testable
- See `Claude.md` for full project guidelines

## License

Private project — not for public distribution.
