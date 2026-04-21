# Development Plan

## MVP: ArXistant Daily Report Pipeline

### Tasks

- [x] Create config files and skeleton (`config/topics.yaml`, `config/settings.yaml`, `src/__init__.py`, `src/config.py`, `requirements.txt`, `.env.example`)
- [x] Implement collector module (`src/collector.py`)
- [x] Implement LLM client module (`src/llm_client.py`)
- [x] Implement filter module (`src/filter.py`)
- [x] Implement report module (`src/report.py`)
- [x] Implement main pipeline (`src/main.py`)
- [x] End-to-end test: 28 papers fetched, 18 relevant, report generated at `reports/2026-04-21.md`
- [x] Update dev notes (this file)
