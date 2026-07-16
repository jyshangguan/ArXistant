<p align="center">
  <img src="docs/logo0.5.jpg" width="200" alt="ArXistant logo">
</p>

<h1 align="center">ArXistant</h1>

<p align="center">
  <strong>Personalized arXiv Daily Paper Ranker + Local Paper Database + Publication Manager</strong>
</p>

---

## How to use ArXistant (Chrome Extension)

The **Chrome extension** is the primary way to use ArXistant. Install it once, then everything is one click away.

### 1. Install the extension

```bash
cd /path/to/ArXistant/chrome-extension
```

1. Open Chrome → `chrome://extensions/`
2. Toggle **Developer mode** ON
3. Click **"Load unpacked"**
4. Select the `chrome-extension/` folder

### 2. Register the server launcher (macOS)

The extension can start the ArXistant server for you. Double-click this app once to register it with macOS:

```
chrome-extension/ArXistantServer.app
```

> If macOS shows a security warning, go to **System Settings → Privacy & Security** and click **"Open Anyway"**.

### 3. Daily workflow

| Action | How |
|--------|-----|
| **Check today's papers** | Click the ArXistant icon → "Open Daily Papers" |
| **Server is offline** | Click "Start Server" in the popup — it launches automatically |
| **Mark as visited** | Click "✓ Mark as visited today" to dismiss the daily reminder |
| **Browse recent papers** | Click "Open Recent Papers" (~5 days) |
| **Search arXiv/ADS** | Click "🔍 Search arXiv" |
| **View saved papers** | Click "📂 Saved Papers" |
| **View publications** | Click "📚 My Publications" |
| **Inspect ML model** | Click "🧠 ML Features" |
| **Change reminder time** | Click "⚙️ Settings" |

### 4. Daily reminder

The extension shows a browser notification at your configured time (default 10:30 AM) **only if you haven't visited the daily page yet**. Click the notification to open the papers.

### 5. Save papers

On any paper list, click the **💾** button next to a paper to save it to your database. Saved papers feed the ML model — the more you save, the better the rankings get.

---

## What it does

### 1. Daily arXiv fetch & ML ranking

Fetches new astro-ph submissions from arXiv every day, scores them with a **TF-IDF + Logistic Regression** model trained on your saved papers, and generates a ranked HTML page with collapsible abstracts. Also supports fetching the **recent page** (last ~5 days of papers).

### 2. Local paper database (HTTP server)

A lightweight HTTP server (`src/arxiv_db_server.py`) running on `http://localhost:8765` serves:

| Page | URL | Description |
|------|-----|-------------|
| **Daily Papers** | `/` or `/daily.html` | Today's ranked astro-ph papers with save/delete buttons |
| **Recent Papers** | `/recent.html` | Papers from the last ~5 days, ranked |
| **Saved Papers** | `/database.html` | Search, view notes, and delete saved papers |
| **My Publications** | `/publications.html` | Your publication bibliography, imported from SciX/ADS |
| **Search arXiv/ADS** | `/search-arxiv.html` | Search arXiv API or ADS API and save papers |
| **ML Features** | `/ml-features.html` | Inspect ML model features, manage blacklist and custom keywords |
| **Chat** | `/chat.html` | Chat interface for discussing papers |

### 3. SciX / ADS publication import

Paste your SciX library link (e.g. `https://scixplorer.org/user/libraries/...`) on the My Publications page, click **Fetch** to pull all papers from the ADS API, then **Add** to import them. Duplicates are automatically detected and skipped (by bibcode, title, and arXiv ID). Each paper has a **Remove** button for manual cleanup.

### 4. Auto-regenerating research interests

`local/interests.txt` is rebuilt from your saved papers and publication metadata every time you save or delete a paper. Keywords are weighted (1–10) based on source trust and frequency. A three-file system separates auto-generated keywords (`interests_auto.txt`), manual overrides (`interests_manual.txt`), and the merged output (`interests.txt`).

---

## Chrome Extension Architecture

```
chrome-extension/
├── manifest.json              # Extension metadata & permissions
├── background.js              # Service worker: alarms, notifications, tracking
├── popup.html + popup.js      # Click icon → status, buttons, server control
├── popup.css                  # Popup styles
├── options.html + options.js  # Settings: server URL, reminder time
├── icons/                     # Red "A" icons (16, 48, 128 px)
└── ArXistantServer.app/       # macOS helper app (launches server)
    ├── Contents/
    │   ├── Info.plist         # App metadata + arxistant:// URL scheme
    │   ├── MacOS/
    │   │   └── ArXistantServer
    │   └── Resources/
    │       └── icon.png
```

The extension communicates with your local server at `http://localhost:8765`. When the server is offline, the popup shows a **"Start Server"** button that launches `ArXistantServer.app` via the `arxistant://start` URL scheme.

---

## Quick start (without extension)

### Prerequisites

- Python 3.8+
- `numpy` and `scikit-learn` (for ML ranking)
- `curl` (used by the ranker for HTTPS on macOS)

```bash
pip install numpy scikit-learn
```

### Setup

```bash
cd /path/to/ArXistant

# 1. (Required for SciX/ADS features) Create an ADS API token:
#    Go to https://ui.adsabs.harvard.edu/user/settings/token
#    Save it to local/ads_token.txt
echo "your-ads-token-here" > local/ads_token.txt

# 2. (Optional) Seed the ML model with some initial saved papers,
#    or it starts cold and improves as you save papers.

# 3. Generate today's ranked paper list
python3 src/arxiv_daily_ranker_html.py \
  --output local/arxiv_ranked_personalized.html

# 4. Start the server
python3 src/arxiv_db_server.py
# Open http://localhost:8765/
```

To generate the recent (last ~5 days) page instead:

```bash
python3 src/arxiv_daily_ranker_html.py --recent \
  --output local/arxiv_recent_personalized.html
```

---

## ML ranker system

The ML ranker (`src/arxiv_ml_ranker.py`) trains a **Logistic Regression** classifier on TF-IDF features:

- **Positive samples**: papers you've saved to the database
- **Negative samples**: random recent arXiv papers (up to 2:1 ratio)
- **Text features**: cleaned title + abstract (LaTeX, HTML, math mode stripped)
- **Feature engineering**: unigrams + bigrams, top 10,000 features, sublinear TF scaling

The model is persisted to `local/ml_ranker/model.pkl` and `local/ml_ranker/vectorizer.pkl`.

Train or re-train the model:

```bash
python3 src/arxiv_ml_ranker.py train
```

The **ML Features page** (`/ml-features.html`) lets you:
- Inspect the top 50 positive and 30 negative features the model learned
- Blacklist noisy/uninformative features
- Add custom positive/negative keywords to bias the model

---

## Interest generation system

`src/interest_generator.py` extracts weighted keywords from your saved papers and publications:

| Source | Weight |
|--------|--------|
| Publication keywords | 5.0 |
| Publication titles | 3.0 |
| Saved paper titles | 2.0 |
| Publication abstracts | 1.0 |
| Saved paper abstracts | 1.0 |

Keywords are scored by `source_weight × specificity_bonus × frequency_multiplier`, then normalized to 1–10. The top 50 are written to `interests_auto.txt` and merged with any manual overrides in `interests_manual.txt` to produce `interests.txt`.

---

## SciX / ADS API integration

The server uses the [NASA ADS API](https://ui.adsabs.harvard.edu/help/api/) for two purposes:

1. **Publication import** — Fetches all bibcodes from your ADS library, then batch-queries the Search API for full metadata (title, authors, abstract, keywords, year, arXiv ID).
2. **ADS search** — The search page can query ADS directly (with citation counts) as an alternative to the arXiv API.

Requires an API token in `local/ads_token.txt` (free from https://ui.adsabs.harvard.edu/user/settings/token).

---

## Project structure

```
ArXistant/
├── src/
│   ├── arxiv_daily_ranker.py          # Legacy Markdown-output ranker
│   ├── arxiv_daily_ranker_html.py     # HTML-output ranker (main pipeline)
│   ├── arxiv_db_server.py             # HTTP server — all pages + REST API
│   ├── arxiv_ml_ranker.py             # TF-IDF + Logistic Regression model
│   ├── interest_generator.py          # Weighted keyword extraction pipeline
│   └── fix_publications.py            # One-shot: fix author metadata from BibTeX
├── local/                             # Git-ignored runtime data
│   ├── arxiv_papers.db                # SQLite database
│   ├── ads_token.txt                  # ADS API token (required for SciX)
│   ├── scix_config.json               # SciX library link (managed by web UI)
│   ├── interests.txt                  # Merged auto + manual keywords
│   ├── interests_auto.txt             # Auto-generated keywords
│   ├── interests_manual.txt           # User manual keyword overrides
│   ├── arxiv_ranked_personalized.html # Daily ranked papers
│   ├── arxiv_recent_personalized.html # Recent (~5 days) ranked papers
│   ├── ml_features.html               # ML feature inspector page
│   ├── chat.html                      # Chat interface
│   ├── ml_ranker/                     # ML model artifacts
│   │   ├── model.pkl                  # Trained LogisticRegression
│   │   ├── vectorizer.pkl             # Trained TfidfVectorizer
│   │   ├── feature_blacklist.json     # Suppressed features
│   │   ├── custom_positive.json       # User-defined positive keywords
│   │   └── custom_negative.json       # User-defined negative keywords
│   └── ...
├── chrome-extension/                  # Chrome extension (primary UI)
│   ├── manifest.json
│   ├── background.js
│   ├── popup.html + popup.js + popup.css
│   ├── options.html + options.js
│   ├── icons/
│   │   ├── icon16.png
│   │   ├── icon48.png
│   │   └── icon128.png
│   └── ArXistantServer.app/           # macOS helper to launch server
├── docs/
│   └── logo0.5.jpg
├── start_server.sh                    # Launch server in background
├── .gitignore
└── README.md
```

---

## Shell script

`start_server.sh` starts the server in the background and logs output:

```bash
./start_server.sh
# Server runs on http://localhost:8765, logs to local/server.log
```

---

## Cron

A daily cron job runs the ranker and regenerates the HTML page automatically. Example crontab:

```cron
30 10 * * * cd /path/to/ArXistant && python3 src/arxiv_daily_ranker_html.py --output local/arxiv_ranked_personalized.html
```

---

## License

MIT
