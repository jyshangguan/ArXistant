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

2. **Local paper database** — A lightweight HTTP server (`arxiv_db_server.py`) that serves:
   - **Daily papers** (`http://localhost:8765/`) with per-paper "💾 Save to DB" buttons
   - **Saved papers viewer** (`http://localhost:8765/database.html`) with search, notes, and delete
   - **My Publications** (`http://localhost:8765/publications.html`) — your 112-paper bibliography from SciX
   - **Interest editor** (`http://localhost:8765/interests.html`) — view and regenerate weighted keywords

3. **Auto-regenerating interests** — `interests.txt` is rebuilt from your saved papers + publication metadata every time you save or delete a paper. Keywords are weighted (1–10) based on frequency and source trust.

## Quick start

```bash
cd /Users/shangguan/Softwares/my_modules/ArXistant

# 1. Generate today's ranked paper list
python3 arxiv_daily_ranker_html.py \
  --interests-file interests.txt \
  --output arxiv_ranked_personalized.html

# 2. Start the database server
python3 arxiv_db_server.py
# Open http://localhost:8765/
```

## Files

| File | Purpose |
|------|---------|
| `arxiv_daily_ranker_html.py` | Fetch, score, and generate ranked HTML for today's arXiv papers |
| `arxiv_db_server.py` | HTTP server — saved papers DB, publications viewer, interest editor |
| `interest_generator.py` | Weighted keyword extraction pipeline (auto-regenerates `interests.txt`) |
| `arxiv_papers.db` | SQLite database — saved_papers + my_publications tables |
| `shangguan_papers_metadata.json` | 112-paper SciX bibliography metadata |
| `scix_library_20.bib` | BibTeX export for full author lists |
| `shangguan_bibcodes.txt` | Bibcode list for publications |
| `interests.txt` | Auto-generated weighted keywords (tab-separated: keyword, weight) |

## Cron

A daily cron job runs the ranker at 10:30 AM Beijing time and generates the HTML page automatically.

## License

MIT
