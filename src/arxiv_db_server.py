#!/usr/bin/env python3
"""
Lightweight HTTP server for arXiv paper database + My Publications database.
Serves HTML pages and provides REST API for SQLite database.

Usage:
    python src/arxiv_db_server.py
    # Then open http://localhost:8765 in your browser
"""

import json
import os
import re
import sqlite3
import subprocess
import sys
import threading
import urllib.parse
import urllib.request
import urllib.error
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))

DB_PATH = os.path.join(PROJECT_ROOT, "local", "arxiv_papers.db")
DAILY_HTML = os.path.join(PROJECT_ROOT, "local", "arxiv_ranked_personalized.html")
RECENT_HTML = os.path.join(PROJECT_ROOT, "local", "arxiv_recent_personalized.html")
PUBLICATIONS_JSON = os.path.join(PROJECT_ROOT, "local", "shangguan_papers_metadata.json")
BIB_PATH = os.path.join(PROJECT_ROOT, "local", "scix_library_20.bib")
ML_FEATURES_HTML = os.path.join(PROJECT_ROOT, "local", "ml_features.html")
CHAT_HTML = os.path.join(PROJECT_ROOT, "local", "chat.html")
ADS_TOKEN_PATH = os.path.join(PROJECT_ROOT, "local", "ads_token.txt")
SCIX_CONFIG_PATH = os.path.join(PROJECT_ROOT, "local", "scix_config.json")
ML_RANKER_DIR = os.path.join(PROJECT_ROOT, "local", "ml_ranker")
RETRAIN_STATE_PATH = os.path.join(ML_RANKER_DIR, "retrain_state.json")
DEFAULT_RETRAIN_AFTER_CHANGES = 5
RETRAIN_STATE_LOCK = threading.Lock()


def clean_python_env():
    """Environment for an arm64 system-Python child launched from macOS apps."""
    return {
        "HOME": os.path.expanduser("~"),
        "USER": os.environ.get("USER", ""),
        "LOGNAME": os.environ.get("LOGNAME", os.environ.get("USER", "")),
        "PATH": "/usr/local/bin:/usr/bin:/bin:/usr/sbin:/sbin",
        "LANG": os.environ.get("LANG", "en_US.UTF-8"),
        "TMPDIR": os.environ.get("TMPDIR", "/tmp"),
    }


def _default_retrain_state():
    return {
        "changes_since_training": 0,
        "retrain_after_changes": DEFAULT_RETRAIN_AFTER_CHANGES,
        "training": False,
        "last_training_started_at": None,
        "last_trained_at": None,
        "last_error": None,
    }


def _load_retrain_state():
    state = _default_retrain_state()
    try:
        with open(RETRAIN_STATE_PATH, "r", encoding="utf-8") as f:
            state.update(json.load(f))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    state["changes_since_training"] = max(0, int(state.get("changes_since_training", 0)))
    state["retrain_after_changes"] = min(100, max(1, int(
        state.get("retrain_after_changes", DEFAULT_RETRAIN_AFTER_CHANGES))))
    # A persisted true value can only belong to a server process that stopped.
    state["training"] = False
    return state


RETRAIN_STATE = _load_retrain_state()


def _save_retrain_state_locked():
    os.makedirs(ML_RANKER_DIR, exist_ok=True)
    temp_path = RETRAIN_STATE_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(RETRAIN_STATE, f, indent=2)
    os.replace(temp_path, RETRAIN_STATE_PATH)


def get_retrain_state():
    with RETRAIN_STATE_LOCK:
        return dict(RETRAIN_STATE)


def _run_training(included_changes):
    error = None
    training_succeeded = False
    try:
        command = ["/usr/bin/arch", "-arm64", "/usr/bin/python3",
                   os.path.join(PROJECT_ROOT, "src", "arxiv_ml_ranker.py")]
        result = subprocess.run(command + ["train"], cwd=PROJECT_ROOT,
                                capture_output=True, text=True, timeout=900,
                                env=clean_python_env())
        if result.returncode != 0:
            error = result.stderr.strip() or result.stdout.strip() or "Training failed"
        else:
            training_succeeded = True
            features = subprocess.run(command + ["features"], cwd=PROJECT_ROOT,
                                      capture_output=True, text=True, timeout=120,
                                      env=clean_python_env())
            if features.returncode != 0:
                error = (features.stderr.strip() or features.stdout.strip()
                         or "Training succeeded, but feature-page generation failed")
    except subprocess.TimeoutExpired:
        error = "Model training timed out"
    except Exception as exc:
        error = str(exc)

    with RETRAIN_STATE_LOCK:
        if training_succeeded:
            RETRAIN_STATE["changes_since_training"] = max(
                0, RETRAIN_STATE["changes_since_training"] - included_changes)
            RETRAIN_STATE["last_trained_at"] = datetime.now().astimezone().isoformat()
        RETRAIN_STATE["training"] = False
        RETRAIN_STATE["last_error"] = error
        _save_retrain_state_locked()


def start_training(manual=False):
    with RETRAIN_STATE_LOCK:
        if RETRAIN_STATE["training"]:
            return False, dict(RETRAIN_STATE)
        included_changes = RETRAIN_STATE["changes_since_training"]
        RETRAIN_STATE["training"] = True
        RETRAIN_STATE["last_training_started_at"] = datetime.now().astimezone().isoformat()
        RETRAIN_STATE["last_error"] = None
        _save_retrain_state_locked()
        state = dict(RETRAIN_STATE)
    threading.Thread(target=_run_training, args=(included_changes,),
                     name="arxistant-ml-training", daemon=True).start()
    return True, state


def record_training_change():
    should_start = False
    with RETRAIN_STATE_LOCK:
        RETRAIN_STATE["changes_since_training"] += 1
        should_start = (not RETRAIN_STATE["training"] and
                        RETRAIN_STATE["changes_since_training"] >=
                        RETRAIN_STATE["retrain_after_changes"])
        _save_retrain_state_locked()
        state = dict(RETRAIN_STATE)
    if should_start:
        _, state = start_training()
    return state


def set_retrain_threshold(value):
    value = int(value)
    if value < 1 or value > 100:
        raise ValueError("Retraining threshold must be between 1 and 100")
    with RETRAIN_STATE_LOCK:
        RETRAIN_STATE["retrain_after_changes"] = value
        should_start = (not RETRAIN_STATE["training"] and
                        RETRAIN_STATE["changes_since_training"] >= value)
        _save_retrain_state_locked()
        state = dict(RETRAIN_STATE)
    if should_start:
        _, state = start_training()
    return state


def load_scix_config():
    if not os.path.exists(SCIX_CONFIG_PATH):
        return {"scix_link": ""}
    with open(SCIX_CONFIG_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_scix_config(config):
    with open(SCIX_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(config, f, indent=2)


def load_ads_token():
    if not os.path.exists(ADS_TOKEN_PATH):
        return ""
    with open(ADS_TOKEN_PATH, 'r', encoding='utf-8') as f:
        return f.read().strip()


SCIXPLORER_LIBRARY_RE = re.compile(r'scixplorer\.org/user/libraries/([a-zA-Z0-9_-]+)')


def extract_library_id(scix_link):
    m = SCIXPLORER_LIBRARY_RE.search(scix_link)
    return m.group(1) if m else ""


def fetch_scix_library(library_id):
    """Fetch all papers from an ADS library by its ID. Returns list of paper dicts."""
    token = load_ads_token()
    if not token:
        return None, "ADS API token not configured. Add token to local/ads_token.txt"

    # Step 1: get all bibcodes from the library
    lib_url = f"https://api.adsabs.harvard.edu/v1/biblib/libraries/{library_id}?rows=500"
    req = urllib.request.Request(lib_url, headers={"Authorization": f"Bearer {token}"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            lib_data = json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode() if e.fp else ""
        return None, f"ADS library error ({e.code}): {body}"
    except Exception as e:
        return None, f"Failed to fetch library: {e}"

    bibcodes = lib_data.get("documents", [])
    if not bibcodes:
        return None, "No papers found in this library"

    # Step 2: batch-fetch metadata via ADS Search API
    papers = []
    batch_size = 100
    for i in range(0, len(bibcodes), batch_size):
        batch = bibcodes[i:i + batch_size]
        q = " OR ".join(f"bibcode:{b}" for b in batch)
        params = urllib.parse.urlencode({
            "q": q,
            "fl": "bibcode,title,author,abstract,year,keyword,alternate_bibcode",
            "rows": batch_size
        })
        search_url = f"https://api.adsabs.harvard.edu/v1/search/query?{params}"
        req = urllib.request.Request(search_url, headers={"Authorization": f"Bearer {token}"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                search_data = json.loads(resp.read().decode())
        except Exception:
            continue

        for doc in search_data.get("response", {}).get("docs", []):
            title = doc.get("title", [""])[0] if isinstance(doc.get("title"), list) else doc.get("title", "")
            authors = ", ".join(doc.get("author", [])) if isinstance(doc.get("author"), list) else str(doc.get("author", ""))
            keywords = ", ".join(doc.get("keyword", [])) if isinstance(doc.get("keyword"), list) else str(doc.get("keyword", ""))
            year = str(doc.get("year", ""))

            arxiv_id = ""
            alt_bibcodes = doc.get("alternate_bibcode", [])
            if isinstance(alt_bibcodes, list):
                for ab in alt_bibcodes:
                    if "arXiv" in ab:
                        arxiv_id = ab.split("arXiv:")[-1].strip()
                        break

            papers.append({
                "bibcode": doc.get("bibcode", ""),
                "title": title,
                "authors": authors,
                "abstract": doc.get("abstract", ""),
                "keywords": keywords,
                "year": year,
                "arxiv_id": arxiv_id
            })

    return papers, None


# Custom positive/negative keyword helpers (mirror arxiv_ml_ranker functions)
CUSTOM_POSITIVE_PATH = os.path.join(PROJECT_ROOT, "local", "ml_ranker", "custom_positive.json")
CUSTOM_NEGATIVE_PATH = os.path.join(PROJECT_ROOT, "local", "ml_ranker", "custom_negative.json")


def load_custom_positive():
    if not os.path.exists(CUSTOM_POSITIVE_PATH):
        return []
    with open(CUSTOM_POSITIVE_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def load_custom_negative():
    if not os.path.exists(CUSTOM_NEGATIVE_PATH):
        return []
    with open(CUSTOM_NEGATIVE_PATH, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_custom_positive(keywords):
    with open(CUSTOM_POSITIVE_PATH, 'w', encoding='utf-8') as f:
        json.dump(keywords, f, indent=2)


def save_custom_negative(keywords):
    with open(CUSTOM_NEGATIVE_PATH, 'w', encoding='utf-8') as f:
        json.dump(keywords, f, indent=2)


def add_custom_positive(keyword):
    keywords = load_custom_positive()
    keyword = keyword.strip().lower()
    if keyword and keyword not in keywords:
        keywords.append(keyword)
        save_custom_positive(keywords)
    return True


def add_custom_negative(keyword):
    keywords = load_custom_negative()
    keyword = keyword.strip().lower()
    if keyword and keyword not in keywords:
        keywords.append(keyword)
        save_custom_negative(keywords)
    return True


def remove_custom_positive(keyword):
    keywords = load_custom_positive()
    keyword = keyword.strip().lower()
    if keyword in keywords:
        keywords.remove(keyword)
        save_custom_positive(keywords)
        return True
    return False


def remove_custom_negative(keyword):
    keywords = load_custom_negative()
    keyword = keyword.strip().lower()
    if keyword in keywords:
        keywords.remove(keyword)
        save_custom_negative(keywords)
        return True
    return False


def deduplicate_custom_keywords():
    """Remove from negative any keywords that also exist in positive. Positive wins."""
    pos = load_custom_positive()
    neg = load_custom_negative()
    pos_set = set(pos)
    new_neg = [w for w in neg if w not in pos_set]
    if len(new_neg) != len(neg):
        save_custom_negative(new_neg)
    return pos, new_neg




def load_ads_token():
    """Load ADS API token from local file."""
    if not os.path.exists(ADS_TOKEN_PATH):
        return None
    with open(ADS_TOKEN_PATH, 'r', encoding='utf-8') as f:
        return f.read().strip()


def search_arxiv_api(query, max_results=20):
    """Search arXiv API and return list of paper dicts."""
    import xml.etree.ElementTree as ET
    encoded_q = urllib.parse.quote(query)
    url = (
        f"https://export.arxiv.org/api/query?"
        f"search_query=all:{encoded_q}&max_results={max_results}"
        f"&sortBy=relevance&sortOrder=descending"
    )
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(req, timeout=30)
    data = resp.read().decode('utf-8')

    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    root = ET.fromstring(data)
    entries = root.findall('atom:entry', ns)

    papers = []
    for entry in entries:
        id_elem = entry.find('atom:id', ns)
        title_elem = entry.find('atom:title', ns)
        summary_elem = entry.find('atom:summary', ns)
        published_elem = entry.find('atom:published', ns)

        arxiv_id = id_elem.text if id_elem is not None else ''
        short_id = arxiv_id.split('/')[-1].replace('abs/', '') if arxiv_id else ''
        short_id = __import__('re').sub(r'v\d+$', '', short_id)

        title = title_elem.text.strip() if title_elem is not None else ''
        abstract = summary_elem.text.strip() if summary_elem is not None else ''
        year = published_elem.text[:4] if published_elem is not None and published_elem.text else ''

        authors = []
        for author in entry.findall('atom:author', ns):
            name = author.find('atom:name', ns)
            if name is not None:
                authors.append(name.text)

        papers.append({
            'id': short_id,
            'title': title,
            'authors': authors,
            'abstract': abstract,
            'year': year,
            'source': 'arxiv'
        })

    return papers


def search_ads_api(query, token, max_results=20):
    """Search ADS API and return list of paper dicts."""
    url = "https://api.adsabs.harvard.edu/v1/search/query"
    params = {
        'q': query,
        'rows': max_results,
        'fl': 'title,author,abstract,bibcode,year,arxiv,doi,citation_count,pubdate',
        'sort': 'score desc'
    }
    query_str = urllib.parse.urlencode(params)
    req = urllib.request.Request(
        f"{url}?{query_str}",
        headers={
            'User-Agent': 'Mozilla/5.0',
            'Authorization': f'Bearer {token}'
        }
    )
    resp = urllib.request.urlopen(req, timeout=30)
    data = json.loads(resp.read().decode('utf-8'))

    papers = []
    for doc in data.get('response', {}).get('docs', []):
        title = doc.get('title', [''])[0] if isinstance(doc.get('title'), list) else doc.get('title', '')
        authors = doc.get('author', []) or []
        abstract = doc.get('abstract', '') or ''
        bibcode = doc.get('bibcode', '')
        year = str(doc.get('year', ''))
        arxiv = doc.get('arxiv', '')
        doi = doc.get('doi', [''])[0] if isinstance(doc.get('doi'), list) else doc.get('doi', '')
        citation_count = doc.get('citation_count', 0)
        pubdate = doc.get('pubdate', '')

        arxiv_id = ''
        if arxiv:
            arxiv_id = arxiv if '.' in arxiv else ''

        papers.append({
            'id': arxiv_id,
            'title': title,
            'authors': authors,
            'abstract': abstract,
            'year': year,
            'bibcode': bibcode,
            'doi': doi,
            'citation_count': citation_count,
            'pubdate': pubdate,
            'source': 'ads'
        })

    return papers

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Saved arXiv papers table
    c.execute('''
        CREATE TABLE IF NOT EXISTS saved_papers (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            arxiv_id TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            authors TEXT,
            abstract TEXT,
            relevance_score INTEGER DEFAULT 0,
            date_fetched TEXT,
            date_saved TEXT DEFAULT CURRENT_TIMESTAMP,
            notes TEXT DEFAULT ''
        )
    ''')
    
    # My publications table (from SciX)
    c.execute('''
        CREATE TABLE IF NOT EXISTS my_publications (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            bibcode TEXT NOT NULL UNIQUE,
            title TEXT NOT NULL,
            authors TEXT,
            abstract TEXT,
            keywords TEXT,
            year TEXT,
            date_added TEXT DEFAULT CURRENT_TIMESTAMP
        )
    ''')
    
    conn.commit()
    conn.close()
    
    # Populate publications from JSON if table is empty
    populate_publications()


def populate_publications():
    """Load publications from SciX JSON + BibTeX into the database."""
    if not os.path.exists(PUBLICATIONS_JSON):
        return
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute("SELECT COUNT(*) FROM my_publications")
    count = c.fetchone()[0]
    
    if count > 0:
        conn.close()
        return  # Already populated
    
    # Load JSON
    with open(PUBLICATIONS_JSON, 'r', encoding='utf-8') as f:
        papers = json.load(f)
    
    all_papers = {}
    for paper in papers:
        bibcode = paper.get('bibcode', '')
        title = paper.get('title', [''])[0] if isinstance(paper.get('title'), list) else paper.get('title', '')
        abstract = paper.get('abstract', '')
        keywords = ', '.join(paper.get('keyword', [])) if isinstance(paper.get('keyword'), list) else paper.get('keyword', '')
        year = str(paper.get('year', ''))
        authors = ', '.join(paper.get('author', [])) if isinstance(paper.get('author'), list) else (paper.get('author', '') or paper.get('authors', ''))
        all_papers[bibcode] = (bibcode, title, authors, abstract, keywords, year)
    
    # Overlay BibTeX author data if available
    if os.path.exists(BIB_PATH):
        import re as _re
        with open(BIB_PATH, 'r', encoding='utf-8') as f:
            bib_text = f.read()
        entries = _re.split(r'(?:\n|^)@\w+\{', bib_text)
        if entries and entries[0].strip() == '':
            entries = entries[1:]
        for entry in entries:
            entry = entry.strip()
            if not entry:
                continue
            m = _re.match(r'^([^,]+),', entry)
            if not m:
                continue
            bibcode = m.group(1).strip()
            author_m = _re.search(r'author\s*=\s*\{(.*?)\},\s*\n', entry, _re.DOTALL)
            if author_m:
                raw = author_m.group(1).strip()
                authors = [a.strip() for a in raw.split(' and ') if a.strip() and a.strip().lower() != 'et al.']
                # Strip BibTeX braces
                while True:
                    new_authors = [_re.sub(r'\{([^\{\}]*)\}', r'\1', a) for a in authors]
                    if new_authors == authors:
                        break
                    authors = new_authors
                author_str = ', '.join(authors)
                if bibcode in all_papers:
                    all_papers[bibcode] = (bibcode, all_papers[bibcode][1], author_str, all_papers[bibcode][3], all_papers[bibcode][4], all_papers[bibcode][5])
                else:
                    # BibTeX-only paper: extract title, year, keywords
                    title_m = _re.search(r'title\s*=\s*"\{(.*?)\}",', entry, _re.DOTALL)
                    title = title_m.group(1).strip() if title_m else ''
                    year_m = _re.search(r'year\s*=\s*(\d{4})', entry)
                    year = year_m.group(1) if year_m else ''
                    kw_m = _re.search(r'keywords\s*=\s*\{(.*?)\},', entry, _re.DOTALL)
                    keywords = kw_m.group(1).strip() if kw_m else ''
                    all_papers[bibcode] = (bibcode, title, author_str, '', keywords, year)
    
    for paper in all_papers.values():
        c.execute('''
            INSERT OR IGNORE INTO my_publications (bibcode, title, authors, abstract, keywords, year)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', paper)
    
    conn.commit()
    conn.close()
    print(f"Populated {len(all_papers)} publications into my_publications table")


class Handler(BaseHTTPRequestHandler):
    def log_message(self, format, *args):
        pass  # Suppress console spam

    def _send_json(self, data, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_html(self, html, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(html.encode())

    def _send_text(self, text, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(text.encode())

    def do_OPTIONS(self):
        self.send_response(204)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")
        self.end_headers()

    def do_GET(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        query = urllib.parse.parse_qs(parsed.query)

        if path == "/" or path == "/index.html":
            if os.path.exists(DAILY_HTML):
                with open(DAILY_HTML, 'r', encoding='utf-8') as f:
                    html = f.read()
                if '<!-- save-button-embedded -->' not in html:
                    html = html.replace('</body>', SAVE_BUTTON_SCRIPT + '</body>')
                self._send_html(html)
            else:
                self._send_text("Daily paper list not found. Run the ranker first.", 404)

        elif path == "/daily.html":
            if os.path.exists(DAILY_HTML):
                with open(DAILY_HTML, 'r', encoding='utf-8') as f:
                    html = f.read()
                if '<!-- save-button-embedded -->' not in html:
                    html = html.replace('</body>', SAVE_BUTTON_SCRIPT + '</body>')
                self._send_html(html)
            else:
                self._send_text("Daily paper list not found. Run the ranker first.", 404)

        elif path == "/recent.html":
            if os.path.exists(RECENT_HTML):
                with open(RECENT_HTML, 'r', encoding='utf-8') as f:
                    html = f.read()
                if '<!-- save-button-embedded -->' not in html:
                    html = html.replace('</body>', SAVE_BUTTON_SCRIPT + '</body>')
                self._send_html(html)
            else:
                self._send_text("Recent paper list not found. Run the ranker with --recent first.", 404)

        elif path == "/database.html":
            self._send_html(DATABASE_VIEWER_HTML)

        elif path == "/publications.html":
            self._send_html(PUBLICATIONS_VIEWER_HTML)

        elif path == "/ml-features.html":
            if os.path.exists(ML_FEATURES_HTML):
                with open(ML_FEATURES_HTML, 'r', encoding='utf-8') as f:
                    self._send_html(f.read())
            else:
                self._send_text("ML features page not found. Run the model training first.", 404)


        elif path == "/chat.html":
            if os.path.exists(CHAT_HTML):
                with open(CHAT_HTML, 'r', encoding='utf-8') as f:
                    self._send_html(f.read())
            else:
                self._send_text("Chat page not found.", 404)

        elif path == "/search-arxiv.html":
            self._send_html(SEARCH_ARXIV_HTML)

        elif path == "/api/arxiv/search":
            q = query.get("q", [""])[0]
            if not q:
                self._send_json({"papers": [], "count": 0, "query": "", "source": "arxiv"})
                return
            try:
                papers = search_arxiv_api(q)
                self._send_json({"papers": papers, "count": len(papers), "query": q, "source": "arxiv"})
            except Exception as e:
                self._send_json({"error": str(e), "papers": [], "count": 0, "query": q, "source": "arxiv"}, 502)

        elif path == "/api/ads/search":
            q = query.get("q", [""])[0]
            if not q:
                self._send_json({"papers": [], "count": 0, "query": "", "source": "ads"})
                return
            token = load_ads_token()
            if not token:
                self._send_json({"error": "ADS token not configured", "papers": [], "count": 0, "query": q, "source": "ads"}, 503)
                return
            try:
                papers = search_ads_api(q, token)
                self._send_json({"papers": papers, "count": len(papers), "query": q, "source": "ads"})
            except Exception as e:
                self._send_json({"error": str(e), "papers": [], "count": 0, "query": q, "source": "ads"}, 502)

        elif path == "/api/papers":
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM saved_papers ORDER BY date_saved DESC")
            rows = [dict(r) for r in c.fetchall()]
            conn.close()
            self._send_json({"papers": rows, "count": len(rows)})

        elif path == "/api/search":
            q = query.get("q", [""])[0].lower()
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""
                SELECT * FROM saved_papers 
                WHERE LOWER(title) LIKE ? OR LOWER(authors) LIKE ? OR LOWER(abstract) LIKE ? OR LOWER(notes) LIKE ?
                ORDER BY date_saved DESC
            """, (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"))
            rows = [dict(r) for r in c.fetchall()]
            conn.close()
            self._send_json({"papers": rows, "count": len(rows), "query": q})

        elif path == "/api/publications":
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("SELECT * FROM my_publications ORDER BY year DESC, title ASC")
            rows = [dict(r) for r in c.fetchall()]
            conn.close()
            self._send_json({"publications": rows, "count": len(rows)})

        elif path == "/api/publications/search":
            q = query.get("q", [""])[0].lower()
            conn = sqlite3.connect(DB_PATH)
            conn.row_factory = sqlite3.Row
            c = conn.cursor()
            c.execute("""
                SELECT * FROM my_publications 
                WHERE LOWER(title) LIKE ? OR LOWER(authors) LIKE ? OR LOWER(abstract) LIKE ? OR LOWER(keywords) LIKE ?
                ORDER BY year DESC, title ASC
            """, (f"%{q}%", f"%{q}%", f"%{q}%", f"%{q}%"))
            rows = [dict(r) for r in c.fetchall()]
            conn.close()
            self._send_json({"publications": rows, "count": len(rows), "query": q})

        elif path == "/api/scix-link":
            self._send_json(load_scix_config())

        elif path == "/api/custom-positive":
            self._send_json({"keywords": load_custom_positive()})

        elif path == "/api/custom-negative":
            self._send_json({"keywords": load_custom_negative()})

        elif path == "/api/ml-retraining":
            self._send_json(get_retrain_state())

        else:
            self._send_text("Not found", 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        data = json.loads(body) if body else {}

        if path == "/api/scix/fetch":
            scix_link = data.get("scix_link", "").strip()
            if not scix_link:
                self._send_json({"success": False, "error": "No SciX link provided"}, 400)
                return
            library_id = extract_library_id(scix_link)
            if not library_id:
                self._send_json({"success": False, "error": "Could not extract library ID from link. Expected URL like https://scixplorer.org/user/libraries/XXX"}, 400)
                return
            papers, error = fetch_scix_library(library_id)
            if error:
                self._send_json({"success": False, "error": error}, 500)
                return
            # Save the link
            config = load_scix_config()
            config["scix_link"] = scix_link
            save_scix_config(config)
            self._send_json({"success": True, "papers": papers, "count": len(papers)})

        elif path == "/api/publications/add":
            papers_to_add = data.get("papers", [])
            if not papers_to_add:
                self._send_json({"success": False, "error": "No papers provided"}, 400)
                return
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            # Gather all existing bibcodes, normalized titles, and arxiv_ids for duplicate checking
            c.execute("SELECT bibcode, LOWER(TRIM(title)) as title_lower FROM my_publications")
            existing = c.fetchall()
            existing_bibcodes = {row[0] for row in existing}
            existing_titles = {row[1] for row in existing}
            added = 0
            skipped = 0
            for p in papers_to_add:
                bibcode = p.get("bibcode", "").strip()
                title = (p.get("title") or "").strip()
                title_lower = title.lower()
                arxiv_id = (p.get("arxiv_id") or "").strip()
                # Duplicate check: same bibcode, same title (case-insensitive), or same arxiv_id via bibcode
                if bibcode and bibcode in existing_bibcodes:
                    skipped += 1
                    continue
                if title_lower and title_lower in existing_titles:
                    skipped += 1
                    continue
                if arxiv_id:
                    if arxiv_id in existing_bibcodes or f"arXiv:{arxiv_id}" in existing_bibcodes:
                        skipped += 1
                        continue
                years = str(p.get("year", ""))
                authors = (p.get("authors") or "")[:5000]
                abstracts = (p.get("abstract") or "")[:20000]
                keywords = (p.get("keywords") or "")[:2000]
                try:
                    c.execute('''
                        INSERT OR IGNORE INTO my_publications (bibcode, title, authors, abstract, keywords, year)
                        VALUES (?, ?, ?, ?, ?, ?)
                    ''', (bibcode, title, authors, abstracts, keywords, years))
                    if c.rowcount > 0:
                        added += 1
                    else:
                        skipped += 1
                except Exception:
                    skipped += 1
            conn.commit()
            conn.close()
            self._send_json({"success": True, "added": added, "skipped": skipped})

        elif path == "/api/publications/remove":
            bibcode = data.get("bibcode", "").strip()
            if not bibcode:
                self._send_json({"success": False, "error": "No bibcode provided"}, 400)
                return
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM my_publications WHERE bibcode = ?", (bibcode,))
            removed = c.rowcount > 0
            conn.commit()
            conn.close()
            self._send_json({"success": True, "removed": removed, "bibcode": bibcode})

        elif path == "/api/save":
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            try:
                arxiv_id = data.get("arxiv_id", "")
                c.execute("SELECT 1 FROM saved_papers WHERE arxiv_id = ?", (arxiv_id,))
                was_saved = c.fetchone() is not None
                c.execute('''
                    INSERT OR REPLACE INTO saved_papers 
                    (arxiv_id, title, authors, abstract, relevance_score, date_fetched, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    arxiv_id,
                    data.get("title", ""),
                    data.get("authors", ""),
                    data.get("abstract", ""),
                    data.get("relevance_score", 0),
                    data.get("date_fetched", ""),
                    data.get("notes", "")
                ))
                conn.commit()
                retraining = get_retrain_state() if was_saved else record_training_change()
                self._send_json({"success": True, "message": "Paper saved",
                                 "preference_changed": not was_saved,
                                 "retraining": retraining})
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)
            finally:
                conn.close()

        elif path == "/api/delete":
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM saved_papers WHERE arxiv_id = ?", (data.get("arxiv_id"),))
            removed = c.rowcount > 0
            conn.commit()
            conn.close()
            retraining = record_training_change() if removed else get_retrain_state()
            self._send_json({"success": True, "message": "Paper deleted",
                             "preference_changed": removed,
                             "retraining": retraining})

        elif path == "/api/update_notes":
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE saved_papers SET notes = ? WHERE arxiv_id = ?", 
                      (data.get("notes", ""), data.get("arxiv_id", "")))
            conn.commit()
            conn.close()
            self._send_json({"success": True, "message": "Notes updated"})

        elif path == "/api/custom-positive":
            keyword = data.get("keyword", "")
            if keyword:
                add_custom_positive(keyword)
                self._send_json({"success": True, "message": f"Keyword '{keyword}' added to custom positive"})
            else:
                self._send_json({"success": False, "error": "No keyword provided"}, 400)

        elif path == "/api/custom-negative":
            keyword = data.get("keyword", "")
            if keyword:
                add_custom_negative(keyword)
                self._send_json({"success": True, "message": f"Keyword '{keyword}' added to custom negative"})
            else:
                self._send_json({"success": False, "error": "No keyword provided"}, 400)

        elif path == "/api/custom-positive/remove":
            keyword = data.get("keyword", "")
            if keyword:
                if remove_custom_positive(keyword):
                    self._send_json({"success": True, "message": f"Keyword '{keyword}' removed from custom positive"})
                else:
                    self._send_json({"success": False, "error": f"Keyword '{keyword}' not in custom positive"}, 404)
            else:
                self._send_json({"success": False, "error": "No keyword provided"}, 400)

        elif path == "/api/custom-negative/remove":
            keyword = data.get("keyword", "")
            if keyword:
                if remove_custom_negative(keyword):
                    self._send_json({"success": True, "message": f"Keyword '{keyword}' removed from custom negative"})
                else:
                    self._send_json({"success": False, "error": f"Keyword '{keyword}' not in custom negative"}, 404)
            else:
                self._send_json({"success": False, "error": "No keyword provided"}, 400)

        elif path == "/api/regenerate-features":
            try:
                # Deduplicate: positive wins over negative
                deduplicate_custom_keywords()
                # Run ML generation in a fresh interpreter. macOS can attach
                # __PYVENV_LAUNCHER__ to app-launched Python processes; a clean
                # subprocess prevents that state from breaking a late numpy
                # import inside this long-running HTTP server.
                result = subprocess.run(
                    ["/usr/bin/arch", "-arm64", "/usr/bin/python3",
                     os.path.join(PROJECT_ROOT, "src", "arxiv_ml_ranker.py"), "features"],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    timeout=120,
                    env=clean_python_env(),
                )
                if result.returncode == 0:
                    self._send_json({"success": True, "message": "Features HTML regenerated (deduplicated)"})
                else:
                    error = result.stderr.strip() or result.stdout.strip() or "Feature generation failed"
                    self._send_json({"success": False, "error": error}, 500)
            except subprocess.TimeoutExpired:
                self._send_json({"success": False, "error": "Feature generation timed out"}, 504)
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)

        elif path == "/api/ml-retraining/settings":
            try:
                state = set_retrain_threshold(data.get("retrain_after_changes"))
                self._send_json({"success": True, **state})
            except (TypeError, ValueError) as e:
                self._send_json({"success": False, "error": str(e)}, 400)

        elif path == "/api/ml-retraining/train":
            started, state = start_training(manual=True)
            self._send_json({"success": True, "started": started, **state})

        elif path == "/api/refresh-daily":
            try:
                env = os.environ.copy()
                env['PYTHONPATH'] = os.path.join(PROJECT_ROOT, 'src')
                result = subprocess.run(
                    [sys.executable, os.path.join(PROJECT_ROOT, 'src', 'arxiv_daily_ranker_html.py'),
                     '--output', DAILY_HTML],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env=env
                )
                if result.returncode == 0:
                    self._send_json({"success": True, "output": result.stdout})
                else:
                    self._send_json({"success": False, "error": result.stderr}, 500)
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)

        elif path == "/api/refresh-recent":
            try:
                env = os.environ.copy()
                env['PYTHONPATH'] = os.path.join(PROJECT_ROOT, 'src')
                result = subprocess.run(
                    [sys.executable, os.path.join(PROJECT_ROOT, 'src', 'arxiv_daily_ranker_html.py'),
                     '--recent', '--output', RECENT_HTML],
                    cwd=PROJECT_ROOT,
                    capture_output=True,
                    text=True,
                    timeout=300,
                    env=env
                )
                if result.returncode == 0:
                    self._send_json({"success": True, "output": result.stdout})
                else:
                    self._send_json({"success": False, "error": result.stderr}, 500)
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)

        else:
            self._send_text("Not found", 404)


SEARCH_ARXIV_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Search arXiv / ADS</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #333; }
    h1 { color: #1a1a1a; border-bottom: 2px solid #b31b1b; padding-bottom: 10px; }
    .search-box { width: 100%; padding: 10px 14px; font-size: 1em; border: 2px solid #ddd; border-radius: 6px; margin-bottom: 12px; box-sizing: border-box; }
    .search-box:focus { outline: none; border-color: #b31b1b; }
    .search-row { display: flex; gap: 12px; align-items: center; margin-bottom: 20px; flex-wrap: wrap; }
    .source-toggle { display: flex; gap: 16px; align-items: center; }
    .source-toggle label { cursor: pointer; font-size: 0.95em; }
    .source-toggle input { margin-right: 4px; cursor: pointer; }
    .search-btn { padding: 8px 20px; background: #b31b1b; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 0.95em; font-weight: bold; }
    .search-btn:hover { background: #8a1515; }
    .search-btn:disabled { background: #ccc; cursor: not-allowed; }
    .stats { color: #666; margin-bottom: 20px; }
    .paper { border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; margin-bottom: 16px; background: #fafafa; }
    .paper:hover { background: #f5f5f5; }
    h2 { font-size: 1.1em; margin-top: 0; }
    h2 a { color: #b31b1b; text-decoration: none; }
    h2 a:hover { text-decoration: underline; }
    .authors { color: #555; font-size: 0.95em; margin: 8px 0; }
    .meta { font-size: 0.85em; color: #888; margin: 4px 0; }
    .year { display: inline-block; background: #b31b1b; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.85em; font-weight: bold; margin-right: 6px; }
    .bibcode { font-family: monospace; font-size: 0.85em; color: #666; }
    .arxiv-id { font-family: monospace; font-size: 0.85em; color: #666; }
    .citations { display: inline-block; background: #1976d2; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.85em; font-weight: bold; margin-right: 6px; }
    .source-badge { display: inline-block; background: #555; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.75em; font-weight: bold; margin-right: 6px; }
    details { margin-top: 8px; }
    summary { cursor: pointer; color: #666; font-size: 0.9em; list-style: none; }
    summary::-webkit-details-marker { display: none; }
    summary::before { content: "▸ "; color: #b31b1b; }
    details[open] summary::before { content: "▾ "; }
    .abstract-full { color: #333; font-size: 0.95em; margin-top: 8px; padding-top: 8px; border-top: 1px dashed #ccc; }
    .nav { margin-bottom: 20px; display: flex; gap: 16px; flex-wrap: wrap; }
    .nav a { color: #b31b1b; text-decoration: none; font-weight: bold; }
    .nav a:hover { text-decoration: underline; }
    .empty { color: #888; font-style: italic; text-align: center; padding: 40px; }
    .save-btn { margin-top: 8px; padding: 4px 12px; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 0.85em; transition: background 0.2s; }
    .save-btn:hover { opacity: 0.9; }
    .no-arxiv { color: #888; font-size: 0.85em; font-style: italic; }
    .error { color: #c62828; background: #ffebee; padding: 12px; border-radius: 6px; margin-bottom: 16px; }
    .scroll-top { position: fixed; bottom: 20px; right: 20px; padding: 10px 16px; background: #b31b1b; color: white; text-decoration: none; border-radius: 50%; font-size: 1.1em; font-weight: bold; cursor: pointer; border: none; box-shadow: 0 2px 8px rgba(0,0,0,0.3); z-index: 1000; transition: background 0.2s; }
    .scroll-top:hover { background: #8a1515; }
    .loading { color: #666; font-style: italic; }
  </style>
</head>
<body>
  <div class="nav">
    <a href="/daily.html">← Daily Papers</a>
    <a href="/recent.html">📅 Recent Papers</a>
    <a href="/chat.html">💬 Chat</a>
    <a href="/database.html">📂 Saved Papers</a>
    <a href="/publications.html">📚 My Publications</a>
    <a href="/ml-features.html">🧠 ML Features</a>
  </div>
  <h1>🔍 Search arXiv / ADS</h1>

  <div class="search-row">
    <input type="text" class="search-box" id="searchInput" placeholder="Enter keywords, title, author, arXiv ID..." onkeydown="if(event.key==='Enter')doSearch()">
    <button class="search-btn" id="searchBtn" onclick="doSearch()">🔍 Search</button>
  </div>
  <div class="source-toggle">
    <label><input type="radio" name="source" value="arxiv" checked> arXiv API</label>
    <label><input type="radio" name="source" value="ads"> ADS / SciX</label>
  </div>

  <p class="stats" id="stats">Enter a query and click Search.</p>
  <div id="results"></div>

  <button class="scroll-top" onclick="window.scrollTo({top:0,behavior:'smooth'})" title="To the top">▲</button>

  <script>
    let savedIds = new Set();
    let currentPapers = [];

    async function loadSavedIds() {
      try {
        const resp = await fetch('/api/papers');
        const data = await resp.json();
        savedIds = new Set(data.papers.map(p => p.arxiv_id));
      } catch (e) {
        console.warn('Could not fetch saved papers:', e);
      }
    }

    async function doSearch() {
      const q = document.getElementById('searchInput').value.trim();
      if (!q) return;
      const source = document.querySelector('input[name="source"]:checked').value;
      const btn = document.getElementById('searchBtn');
      const stats = document.getElementById('stats');
      const results = document.getElementById('results');

      btn.disabled = true;
      stats.textContent = 'Searching ' + source.toUpperCase() + '...';
      results.innerHTML = '<p class="loading">Loading results...</p>';

      try {
        const resp = await fetch('/api/' + source + '/search?q=' + encodeURIComponent(q));
        const data = await resp.json();

        if (data.error) {
          stats.textContent = 'Error: ' + data.error;
          results.innerHTML = '<p class="error">' + escapeHtml(data.error) + '</p>';
          btn.disabled = false;
          return;
        }

        currentPapers = data.papers || [];
        stats.textContent = data.count + ' result' + (data.count !== 1 ? 's' : '') + ' from ' + source.toUpperCase();
        renderResults(data.papers, source);
      } catch (e) {
        stats.textContent = 'Search failed.';
        results.innerHTML = '<p class="error">' + escapeHtml(e.message) + '</p>';
      }
      btn.disabled = false;
    }

    function renderResults(papers, source) {
      const container = document.getElementById('results');
      if (!papers || papers.length === 0) {
        container.innerHTML = '<p class="empty">No papers found. Try a different query.</p>';
        return;
      }

      const dateFetched = new Date().toISOString().split('T')[0];

      container.innerHTML = papers.map((p, i) => {
        const arxivId = p.id || '';
        const hasArxiv = arxivId && arxivId.includes('.');
        const title = escapeHtml(p.title || '');
        const abstract = escapeHtml(p.abstract || '');
        const authors = Array.isArray(p.authors) ? p.authors : (p.authors || '').split(',').map(a => a.trim());
        const authorStr = authors.length <= 5 ? authors.join(', ') : authors.slice(0,5).join(', ') + ', et al.';
        const authorStrEscaped = escapeHtml(authorStr);
        const year = p.year || '';
        const bibcode = p.bibcode || '';
        const citationCount = p.citation_count || 0;
        const doi = p.doi || '';

        let links = '';
        if (hasArxiv) {
          links = `<a href="https://arxiv.org/abs/${arxivId}" target="_blank">arXiv:${arxivId}</a>`;
          if (doi) links += ` | <a href="https://doi.org/${escapeHtml(doi)}" target="_blank">DOI</a>`;
          links += ` | <a href="https://alphaxiv.org/abs/${arxivId}" target="_blank">AlphaXiv</a>`;
        } else if (bibcode) {
          links = `<span class="bibcode">${escapeHtml(bibcode)}</span>`;
          if (doi) links += ` | <a href="https://doi.org/${escapeHtml(doi)}" target="_blank">DOI</a>`;
          links += ` | <a href="https://ui.adsabs.harvard.edu/abs/${escapeHtml(bibcode)}/abstract" target="_blank">ADS</a>`;
        } else if (doi) {
          links = `<a href="https://doi.org/${escapeHtml(doi)}" target="_blank">DOI</a>`;
        }

        let badges = `<span class="source-badge">${source.toUpperCase()}</span>`;
        if (year) badges += `<span class="year">${escapeHtml(year)}</span>`;
        if (citationCount) badges += `<span class="citations">${citationCount} citations</span>`;

        let saveBtn = '';
        if (hasArxiv) {
          const isSaved = savedIds.has(arxivId);
          const btnText = isSaved ? '✓ Saved' : '💾 Save to DB';
          const btnBg = isSaved ? '#2e7d32' : '#b31b1b';
          const btnData = isSaved ? 'true' : 'false';
          // Store paper data as JSON in data-paper attribute to avoid onclick quoting issues
          const paperData = JSON.stringify({id: arxivId, title: p.title || '', authors: authorStr, abstract: p.abstract || '', dateFetched: dateFetched}).replace(/"/g, '&quot;');
          saveBtn = `<button class="save-btn" style="background:${btnBg};" data-saved="${btnData}" data-paper="${paperData}">${btnText}</button>`;
        } else {
          saveBtn = '<span class="no-arxiv">No arXiv ID — cannot save to DB</span>';
        }

        return `
          <div class="paper" data-idx="${i}">
            <h2>${i+1}. ${badges} <a href="${hasArxiv ? 'https://arxiv.org/abs/' + arxivId : (bibcode ? 'https://ui.adsabs.harvard.edu/abs/' + bibcode + '/abstract' : '#')}" target="_blank">${title}</a></h2>
            <p class="meta">${links}</p>
            <p class="authors"><strong>Authors:</strong> ${authorStrEscaped}</p>
            <details>
              <summary><strong style="color:#b31b1b;">View abstract</strong></summary>
              <p class="abstract-full">${abstract || 'Abstract not available.'}</p>
            </details>
            ${saveBtn}
          </div>
        `;
      }).join('');

      // Attach click handlers via event delegation
      container.addEventListener('click', handleResultClick);
    }

    async function handleResultClick(e) {
      const btn = e.target.closest('.save-btn');
      if (!btn) return;
      e.preventDefault();

      const paperData = JSON.parse(btn.dataset.paper.replace(/&quot;/g, '"'));
      const arxivId = paperData.id;
      const isSaved = btn.dataset.saved === 'true';

      if (isSaved) {
        if (!confirm('Remove this paper from your database?')) return;
        try {
          const resp = await fetch('/api/delete', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ arxiv_id: arxivId })
          });
          const data = await resp.json();
          if (data.success) {
            btn.textContent = '💾'; btn.style.background = '#b31b1b'; btn.dataset.saved = 'false';
            savedIds.delete(arxivId);
          } else { btn.textContent = '✗ Error'; btn.style.background = '#c62828'; }
        } catch (e) { btn.textContent = '✗ Error'; btn.style.background = '#c62828'; }
      } else {
        try {
          const resp = await fetch('/api/save', {
            method: 'POST', headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
              arxiv_id: arxivId, title: paperData.title, authors: paperData.authors,
              abstract: paperData.abstract, relevance_score: 0, date_fetched: paperData.dateFetched
            })
          });
          const data = await resp.json();
          if (data.success) {
            btn.textContent = '✓'; btn.style.background = '#2e7d32'; btn.dataset.saved = 'true';
            savedIds.add(arxivId);
          } else { btn.textContent = '✗ Error'; btn.style.background = '#c62828'; }
        } catch (e) { btn.textContent = '✗ Error'; btn.style.background = '#c62828'; }
      }
    }

    function escapeHtml(text) {
      if (!text) return '';
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    loadSavedIds();
  </script>
</body>
</html>
"""

SAVE_BUTTON_SCRIPT = """
<script>
async function togglePaper(arxivId, title, authors, abstract, score, dateFetched) {
    const btn = document.getElementById('save-btn-' + arxivId);
    const isSaved = btn.dataset.saved === 'true';

    if (isSaved) {
        // Delete from database
        if (!confirm('Remove this paper from your database?')) return;
        try {
            const resp = await fetch('/api/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ arxiv_id: arxivId })
            });
            const data = await resp.json();
            if (data.success) {
                btn.textContent = '💾';
                btn.style.background = '#b31b1b';
                btn.style.cursor = 'pointer';
                btn.dataset.saved = 'false';
            } else {
                btn.textContent = '✗ Error';
                btn.style.background = '#c62828';
            }
        } catch (e) {
            btn.textContent = '✗ Error';
            btn.style.background = '#c62828';
        }
    } else {
        // Save to database
        try {
            const resp = await fetch('/api/save', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({
                    arxiv_id: arxivId,
                    title: title,
                    authors: authors,
                    abstract: abstract,
                    relevance_score: score,
                    date_fetched: dateFetched
                })
            });
            const data = await resp.json();
            if (data.success) {
                btn.textContent = '✓';
                btn.style.background = '#2e7d32';
                btn.style.cursor = 'pointer';
                btn.dataset.saved = 'true';
            } else {
                btn.textContent = '✗ Error';
                btn.style.background = '#c62828';
            }
        } catch (e) {
            btn.textContent = '✗ Error';
            btn.style.background = '#c62828';
        }
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    let savedIds = new Set();
    try {
        const resp = await fetch('/api/papers');
        const data = await resp.json();
        savedIds = new Set(data.papers.map(p => p.arxiv_id));
    } catch (e) {
        console.warn('Could not fetch saved papers:', e);
    }

    const papers = document.querySelectorAll('.paper');
    const dateFetched = document.querySelector('h1').textContent;
    papers.forEach((paper) => {
        const link = paper.querySelector('h2 a');
        const arxivId = link.href.split('/abs/')[1];
        const title = link.textContent;
        const authorsEl = paper.querySelector('.authors');
        const authors = authorsEl ? authorsEl.textContent.replace('Authors:', '').trim() : '';
        const abstractEl = paper.querySelector('.abstract-full');
        const abstract = abstractEl ? abstractEl.textContent : '';
        const scoreEl = paper.querySelector('.score');
        const scoreText = scoreEl ? scoreEl.textContent.replace('Relevance: ', '') : '0';
        const score = parseInt(scoreText) || 0;

        const btn = document.createElement('button');
        btn.id = 'save-btn-' + arxivId;
        btn.className = 'save-btn';
        btn.style.cssText = 'margin-top:6px;padding:1px 6px;color:white;border:none;border-radius:3px;cursor:pointer;font-size:0.7em;white-space:nowrap;';

        if (savedIds.has(arxivId)) {
            btn.textContent = '✓';
            btn.style.background = '#2e7d32';
            btn.dataset.saved = 'true';
        } else {
            btn.textContent = '💾';
            btn.style.background = '#b31b1b';
            btn.dataset.saved = 'false';
        }
        btn.onclick = () => togglePaper(arxivId, title, authors, abstract, score, dateFetched);
        paper.appendChild(btn);
    });
});
</script>
"""

DATABASE_VIEWER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>My arXiv Paper Database</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #333; }
    h1 { color: #1a1a1a; border-bottom: 2px solid #b31b1b; padding-bottom: 10px; }
    .search-box { width: 100%; padding: 10px 14px; font-size: 1em; border: 2px solid #ddd; border-radius: 6px; margin-bottom: 20px; box-sizing: border-box; }
    .search-box:focus { outline: none; border-color: #b31b1b; }
    .stats { color: #666; margin-bottom: 20px; }
    .paper { border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; margin-bottom: 16px; background: #fafafa; }
    .paper:hover { background: #f5f5f5; }
    h2 { font-size: 1.1em; margin-top: 0; }
    h2 a { color: #b31b1b; text-decoration: none; }
    h2 a:hover { text-decoration: underline; }
    .authors { color: #555; font-size: 0.95em; margin: 8px 0; }
    .meta { font-size: 0.85em; color: #888; margin: 4px 0; }
    .score { display: inline-block; background: #b31b1b; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.85em; font-weight: bold; }
    details { margin-top: 8px; }
    summary { cursor: pointer; color: #666; font-size: 0.9em; list-style: none; }
    summary::-webkit-details-marker { display: none; }
    summary::before { content: "▸ "; color: #b31b1b; }
    details[open] summary::before { content: "▾ "; }
    .abstract-full { color: #333; font-size: 0.95em; margin-top: 8px; padding-top: 8px; border-top: 1px dashed #ccc; }
    .notes { margin-top: 10px; }
    .notes textarea { width: 100%; min-height: 60px; padding: 8px; border: 1px solid #ddd; border-radius: 4px; font-family: inherit; font-size: 0.9em; box-sizing: border-box; }
    .notes button { margin-top: 6px; padding: 4px 12px; background: #b31b1b; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 0.85em; }
    .delete-btn { float: right; background: #c62828; color: white; border: none; border-radius: 4px; padding: 4px 10px; cursor: pointer; font-size: 0.8em; }
    .nav { margin-bottom: 20px; display: flex; gap: 16px; }
    .nav a { color: #b31b1b; text-decoration: none; font-weight: bold; }
    .nav a:hover { text-decoration: underline; }
    .empty { color: #888; font-style: italic; text-align: center; padding: 40px; }
  </style>
</head>
<body>
  <div class="nav">
    <a href="/daily.html">← Daily Papers</a>
    <a href="/recent.html">📅 Recent Papers</a>
    <a href="/search-arxiv.html">🔍 Search arXiv</a>
    <a href="/chat.html">💬 Chat</a>
    <a href="/publications.html">📚 My Publications</a>
    <a href="/ml-features.html">🧠 ML Features</a>
  </div>
  <h1>My arXiv Paper Database</h1>
  <input type="text" class="search-box" id="searchInput" placeholder="Search by title, author, abstract, or notes..." oninput="searchPapers()">
  <p class="stats" id="stats">Loading...</p>
  <div id="paperList"></div>

  <script>
    let allPapers = [];

    async function loadPapers() {
      const resp = await fetch('/api/papers');
      const data = await resp.json();
      allPapers = data.papers;
      renderPapers(allPapers);
    }

    async function searchPapers() {
      const q = document.getElementById('searchInput').value.trim();
      if (!q) {
        renderPapers(allPapers);
        return;
      }
      const resp = await fetch('/api/search?q=' + encodeURIComponent(q));
      const data = await resp.json();
      renderPapers(data.papers);
    }

    function renderPapers(papers) {
      const container = document.getElementById('paperList');
      document.getElementById('stats').textContent = papers.length + ' paper' + (papers.length !== 1 ? 's' : '') + ' saved';
      
      if (papers.length === 0) {
        container.innerHTML = '<p class="empty">No papers found. Go to the daily list and click "💾 Save to DB" on papers you want to keep.</p>';
        return;
      }
      
      container.innerHTML = papers.map(p => `
        <div class="paper">
          <button class="delete-btn" onclick="deletePaper('${p.arxiv_id}')">🗑 Delete</button>
          <h2><a href="https://arxiv.org/abs/${p.arxiv_id}" target="_blank">${escapeHtml(p.title)}</a></h2>
          <span class="score">Relevance: ${p.relevance_score}</span>
          <p class="authors"><strong>Authors:</strong> ${escapeHtml(p.authors)}</p>
          <p class="meta">Saved: ${p.date_saved} | Fetched: ${p.date_fetched || 'N/A'}</p>
          <details>
            <summary><strong style="color:#b31b1b;">View abstract</strong></summary>
            <p class="abstract-full">${escapeHtml(p.abstract)}</p>
          </details>
          <div class="notes">
            <textarea id="note-${p.arxiv_id}" placeholder="Add your notes here...">${escapeHtml(p.notes || '')}</textarea>
            <button onclick="saveNotes('${p.arxiv_id}')">💾 Save Notes</button>
          </div>
        </div>
      `).join('');
    }

    async function deletePaper(arxivId) {
      if (!confirm('Delete this paper from your database?')) return;
      await fetch('/api/delete', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({arxiv_id: arxivId})
      });
      loadPapers();
    }

    async function saveNotes(arxivId) {
      const notes = document.getElementById('note-' + arxivId).value;
      await fetch('/api/update_notes', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({arxiv_id: arxivId, notes: notes})
      });
      alert('Notes saved!');
    }

    function escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    loadPapers();
  </script>
</body>
</html>
"""

PUBLICATIONS_VIEWER_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>My Publications</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #333; }
    h1 { color: #1a1a1a; border-bottom: 2px solid #b31b1b; padding-bottom: 10px; }
    .search-box { width: 100%; padding: 10px 14px; font-size: 1em; border: 2px solid #ddd; border-radius: 6px; margin-bottom: 20px; box-sizing: border-box; }
    .search-box:focus { outline: none; border-color: #b31b1b; }
    .stats { color: #666; margin-bottom: 20px; }
    .paper { border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; margin-bottom: 16px; background: #fafafa; position: relative; }
    .paper:hover { background: #f5f5f5; }
    h2 { font-size: 1.1em; margin-top: 0; padding-right: 80px; }
    .authors { color: #555; font-size: 0.95em; margin: 8px 0; }
    .meta { font-size: 0.85em; color: #888; margin: 4px 0; }
    .year { display: inline-block; background: #b31b1b; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.85em; font-weight: bold; }
    .bibcode { font-family: monospace; font-size: 0.85em; color: #666; }
    details { margin-top: 8px; }
    summary { cursor: pointer; color: #666; font-size: 0.9em; list-style: none; }
    summary::-webkit-details-marker { display: none; }
    summary::before { content: "▸ "; color: #b31b1b; }
    details[open] summary::before { content: "▾ "; }
    .abstract-full { color: #333; font-size: 0.95em; margin-top: 8px; padding-top: 8px; border-top: 1px dashed #ccc; }
    .keywords { font-size: 0.85em; color: #666; margin-top: 6px; }
    .keywords strong { color: #b31b1b; }
    .nav { margin-bottom: 20px; display: flex; gap: 16px; flex-wrap: wrap; }
    .nav a { color: #b31b1b; text-decoration: none; font-weight: bold; }
    .nav a:hover { text-decoration: underline; }
    .empty { color: #888; font-style: italic; text-align: center; padding: 40px; }
    .scix-bar { background: #f0f4f8; border: 1px solid #d0d7de; border-radius: 8px; padding: 14px 16px; margin-bottom: 20px; display: flex; gap: 10px; align-items: center; flex-wrap: wrap; }
    .scix-bar label { font-weight: bold; white-space: nowrap; color: #333; }
    .scix-bar input { flex: 1; min-width: 260px; padding: 8px 12px; border: 2px solid #d0d7de; border-radius: 6px; font-size: 0.95em; }
    .btn { padding: 8px 18px; color: white; border: none; border-radius: 6px; cursor: pointer; font-weight: bold; font-size: 0.95em; }
    .btn:disabled { opacity: 0.6; cursor: not-allowed; }
    .btn-generate { background: #b31b1b; }
    .btn-add { background: #2da44e; }
    .btn-remove { position: absolute; top: 12px; right: 12px; padding: 4px 10px; background: #cf222e; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 0.8em; }
    .btn-remove:hover { background: #a40e26; }
    .preview-section { margin-bottom: 20px; border: 2px solid #2da44e; border-radius: 8px; padding: 16px; background: #f6fef6; }
    .preview-section h3 { margin-top: 0; color: #1a7f37; }
    .preview-item { padding: 8px 0; border-bottom: 1px solid #d0e8d0; font-size: 0.95em; }
    .preview-item:last-child { border-bottom: none; }
    .preview-item .bibcode { margin-left: 8px; }
    .status-msg { font-size: 0.85em; min-width: 100px; }
  </style>
</head>
<body>
  <div class="nav">
    <a href="/daily.html">← Daily Papers</a>
    <a href="/recent.html">📅 Recent Papers</a>
    <a href="/search-arxiv.html">🔍 Search arXiv</a>
    <a href="/chat.html">💬 Chat</a>
    <a href="/database.html">📂 Saved Papers</a>
    <a href="/ml-features.html">🧠 ML Features</a>
  </div>
  <h1>📚 My Publications</h1>
  <div class="scix-bar">
    <label for="scixLinkInput">SciX Library Link:</label>
    <input type="text" id="scixLinkInput" placeholder="https://scixplorer.org/user/libraries/...">
    <button id="generateBtn" class="btn btn-generate" onclick="generatePapers()">Fetch</button>
    <button id="addBtn" class="btn btn-add" onclick="addPapers()" disabled>Add</button>
    <span id="scixStatus" class="status-msg"></span>
  </div>
  <div id="previewSection" class="preview-section" style="display:none;"></div>
  <input type="text" class="search-box" id="searchInput" placeholder="Search my publications by title, author, abstract, or keywords..." oninput="searchPubs()">
  <p class="stats" id="stats">Loading...</p>
  <div id="pubList"></div>

  <script>
    let allPubs = [];
    let fetchedPapers = [];

    async function loadPubs() {
      const resp = await fetch('/api/publications');
      const data = await resp.json();
      allPubs = data.publications;
      renderPubs(allPubs);
    }

    async function searchPubs() {
      const q = document.getElementById('searchInput').value.trim();
      if (!q) {
        renderPubs(allPubs);
        return;
      }
      const resp = await fetch('/api/publications/search?q=' + encodeURIComponent(q));
      const data = await resp.json();
      renderPubs(data.publications);
    }

    function renderPubs(pubs) {
      const container = document.getElementById('pubList');
      document.getElementById('stats').textContent = pubs.length + ' publication' + (pubs.length !== 1 ? 's' : '') + ' in database';

      if (pubs.length === 0) {
        container.innerHTML = '<p class="empty">No publications in the database. Paste a SciX library link above and click "Fetch".</p>';
        return;
      }

      container.innerHTML = pubs.map(p => `
        <div class="paper" id="paper-${escapeHtml(p.bibcode)}">
          <button class="btn-remove" onclick="removePaper('${escapeHtml(p.bibcode)}')" title="Remove this paper">✕</button>
          <h2>${escapeHtml(p.title)}</h2>
          <span class="year">${p.year || 'N/A'}</span>
          <span class="bibcode">${escapeHtml(p.bibcode)}</span>
          ${p.authors ? `<p class="authors"><strong>Authors:</strong> ${escapeHtml(p.authors)}</p>` : ''}
          <details>
            <summary><strong style="color:#b31b1b;">View abstract</strong></summary>
            <p class="abstract-full">${escapeHtml(p.abstract || 'Abstract not available.')}</p>
          </details>
          ${p.keywords ? `<p class="keywords"><strong>Keywords:</strong> ${escapeHtml(p.keywords)}</p>` : ''}
        </div>
      `).join('');
    }

    function escapeHtml(text) {
      const div = document.createElement('div');
      div.textContent = text;
      return div.innerHTML;
    }

    async function loadScixLink() {
      try {
        const resp = await fetch('/api/scix-link');
        const data = await resp.json();
        if (data.scix_link) {
          document.getElementById('scixLinkInput').value = data.scix_link;
        }
      } catch (e) {
        console.error('Failed to load SciX link:', e);
      }
    }

    async function generatePapers() {
      const link = document.getElementById('scixLinkInput').value.trim();
      const statusEl = document.getElementById('scixStatus');
      const genBtn = document.getElementById('generateBtn');
      const addBtn = document.getElementById('addBtn');
      const previewSection = document.getElementById('previewSection');

      if (!link) {
        statusEl.textContent = 'Please enter a SciX link';
        statusEl.style.color = '#cf222e';
        return;
      }

      genBtn.disabled = true;
      addBtn.disabled = true;
      statusEl.textContent = 'Fetching papers from SciX...';
      statusEl.style.color = '#666';

      try {
        const resp = await fetch('/api/scix/fetch', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({scix_link: link})
        });
        const data = await resp.json();
        if (data.success) {
          fetchedPapers = data.papers;
          statusEl.textContent = 'Found ' + data.count + ' papers';
          statusEl.style.color = '#2da44e';
          renderPreview(fetchedPapers);
          addBtn.disabled = false;
        } else {
          fetchedPapers = [];
          statusEl.textContent = 'Error: ' + (data.error || 'Unknown error');
          statusEl.style.color = '#cf222e';
          previewSection.style.display = 'none';
        }
      } catch (e) {
        fetchedPapers = [];
        statusEl.textContent = 'Network error';
        statusEl.style.color = '#cf222e';
        previewSection.style.display = 'none';
      }
      genBtn.disabled = false;
    }

    function renderPreview(papers) {
      const section = document.getElementById('previewSection');
      if (papers.length === 0) {
        section.style.display = 'none';
        return;
      }
      section.style.display = 'block';
      const existingBibcodes = new Set(allPubs.map(p => p.bibcode));
      const existingTitles = new Set(allPubs.map(p => p.title.toLowerCase().trim()));
      let newCount = 0;
      let dupCount = 0;
      const items = papers.map(p => {
        const isDup = existingBibcodes.has(p.bibcode) || existingTitles.has((p.title || '').toLowerCase().trim());
        if (isDup) dupCount++; else newCount++;
        return `<div class="preview-item" style="color:${isDup ? '#888' : '#333'}">
          ${isDup ? '[duplicate]' : '[new]'}
          <strong>${escapeHtml(p.title)}</strong>
          <span class="bibcode">${escapeHtml(p.bibcode)}</span>
          <span class="year">${p.year || ''}</span>
        </div>`;
      }).join('');
      section.innerHTML = `<h3>Preview — ${papers.length} papers from SciX (${newCount} new, ${dupCount} duplicates)</h3>${items}`;
    }

    async function addPapers() {
      if (fetchedPapers.length === 0) return;
      const addBtn = document.getElementById('addBtn');
      const statusEl = document.getElementById('scixStatus');
      addBtn.disabled = true;
      statusEl.textContent = 'Adding papers...';
      statusEl.style.color = '#666';

      try {
        const resp = await fetch('/api/publications/add', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({papers: fetchedPapers})
        });
        const data = await resp.json();
        if (data.success) {
          statusEl.textContent = 'Added ' + data.added + ', skipped ' + data.skipped + ' duplicates';
          statusEl.style.color = '#2da44e';
          document.getElementById('previewSection').style.display = 'none';
          fetchedPapers = [];
          addBtn.disabled = true;
          await loadPubs();
        } else {
          statusEl.textContent = 'Error: ' + (data.error || 'Unknown');
          statusEl.style.color = '#cf222e';
        }
      } catch (e) {
        statusEl.textContent = 'Network error';
        statusEl.style.color = '#cf222e';
      }
      addBtn.disabled = false;
    }

    async function removePaper(bibcode) {
      if (!confirm('Remove this paper from the database?')) return;
      try {
        const resp = await fetch('/api/publications/remove', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({bibcode: bibcode})
        });
        const data = await resp.json();
        if (data.removed) {
          allPubs = allPubs.filter(p => p.bibcode !== bibcode);
          renderPubs(allPubs);
        }
      } catch (e) {
        console.error('Failed to remove paper:', e);
      }
    }

    loadScixLink();
    loadPubs();
  </script>
</body>
</html>
"""


def run_server(port=8765):
    init_db()
    server = HTTPServer(("localhost", port), Handler)
    print(f"Server running at http://localhost:{port}")
    print(f"  Daily papers:     http://localhost:{port}/")
    print(f"  Recent papers:    http://localhost:{port}/recent.html")
    print(f"  Saved papers:     http://localhost:{port}/database.html")
    print(f"  My publications:  http://localhost:{port}/publications.html")
    print(f"  My ML features:   http://localhost:{port}/ml-features.html")
    print(f"  Search arXiv/ADS: http://localhost:{port}/search-arxiv.html")
    print(f"  Chat with papers: http://localhost:{port}/chat.html")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped")


if __name__ == '__main__':
    run_server()
