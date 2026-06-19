<p align="center">
  <img src="docs/logo0.5.jpg" width="200" alt="ArXistant logo">
</p>

<h1 align="center">ArXistant</h1>

<p align="center">
  <strong>Personalized arXiv Daily Paper Ranker + Local Paper Database</strong>
</p>

---

## What it does

1. **Daily arXiv fetch** — Pulls new astro-ph submissions every morning, scores them against your research interests, and generates a ranked HTML page with toggle-abstract buttons.

2. **Local paper database** — A lightweight HTTP server (`src/arxiv_db_server.py`) that serves:
   - **Daily papers** (`http://localhost:8765/`) with per-paper "💾 Save to DB" buttons
   - **Saved papers viewer** (`http://localhost:8765/database.html`) with search, notes, and delete
   - **My Publications** (`http://localhost:8765/publications.html`) — your 114-paper bibliography from SciX
   - **Interest editor** (`http://localhost:8765/interests.html`) — view and regenerate weighted keywords

3. **Auto-regenerating interests** — `local/interests.txt` is rebuilt from your saved papers + publication metadata every time you save or delete a paper. Keywords are weighted (1–10) based on frequency and source trust.

## Quick start

Run from the project root:

```bash
cd /Users/shangguan/Softwares/my_modules/ArXistant

# 1. Generate today's ranked paper list
python3 src/arxiv_daily_ranker_html.py \
  --interests-file local/interests.txt \
  --output local/arxiv_ranked_personalized.html

# 2. Start the database server
python3 src/arxiv_db_server.py
# Open http://localhost:8765/
```

## Project structure

```
ArXistant/
├── src/
│   ├── arxiv_daily_ranker.py         # Markdown output ranker
│   ├── arxiv_daily_ranker_html.py  # HTML output ranker (with toggle abstracts)
│   ├── arxiv_db_server.py            # HTTP server — DB, saved papers, publications, interests
│   └── interest_generator.py       # Weighted keyword extraction pipeline
├── local/                            # Git-ignored runtime data
│   ├── arxiv_papers.db               # SQLite database
│   ├── interests.txt                 # Auto-generated weighted keywords
│   ├── shangguan_papers_metadata.json
│   ├── scix_library_20.bib
│   ├── arxiv_ranked_personalized.html
│   └── ... (generated reports, logs, etc.)
├── docs/
│   └── logo0.5.jpg
├── .gitignore
└── README.md
```

## Cron

A daily cron job runs the ranker at 10:30 AM Beijing time and generates the HTML page automatically.

## License

MIT
