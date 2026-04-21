# Development Log

## 2026-04-21 — Initial MVP Implementation

### What was done
- Created full MVP pipeline: config → collect → filter → report
- 10 new files created:
  - `config/topics.yaml` — 2 research topics (Exoplanet Atmospheres, Stellar Activity)
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
