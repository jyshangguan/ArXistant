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
import threading
import urllib.parse
import urllib.request
import urllib.error
from html import escape as html_escape
from html import unescape as html_unescape
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from datetime import datetime

from arxistant_paths import DATA_DIR, data_path, ensure_data_dirs

import arxistant_sync
import arxistant_secrets
import arxistant_tasks

DB_PATH = data_path("arxiv_papers.db")
DAILY_HTML = data_path("arxiv_ranked_personalized.html")
RECENT_HTML = data_path("arxiv_recent_personalized.html")
DAILY_JSON = data_path("arxiv_ranked_personalized.json")
RECENT_JSON = data_path("arxiv_recent_personalized.json")
PUBLICATIONS_JSON = data_path("shangguan_papers_metadata.json")
BIB_PATH = data_path("scix_library_20.bib")
ML_FEATURES_HTML = data_path("ml_features.html")
CHAT_CONFIG_PATH = data_path("chat_config.json")
ADS_TOKEN_PATH = data_path("ads_token.txt")
SCIX_CONFIG_PATH = data_path("scix_config.json")
ML_RANKER_DIR = data_path("ml_ranker")
RETRAIN_STATE_PATH = os.path.join(ML_RANKER_DIR, "retrain_state.json")
DEFAULT_RETRAIN_AFTER_CHANGES = 5
SERVER_API_VERSION = 2
RETRAIN_STATE_LOCK = threading.Lock()


# Re-exported for backwards compatibility (tests and any external callers);
# the implementations now live in arxistant_tasks.
child_python_env = arxistant_tasks.child_python_env
is_apple_silicon = arxistant_tasks.is_apple_silicon
python_command = arxistant_tasks.python_command


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
    training_succeeded, error = arxistant_tasks.train_and_generate_features()

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
        return None, f"ADS API token not configured. Add token to {ADS_TOKEN_PATH}"

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
CUSTOM_POSITIVE_PATH = data_path("ml_ranker", "custom_positive.json")
CUSTOM_NEGATIVE_PATH = data_path("ml_ranker", "custom_negative.json")


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

# --- Chat (paper reading helper) -------------------------------------------
#
# The Chat page asks questions about one paper at a time. Non-secret settings
# (base URL, model, temperature) live in chat_config.json inside the data
# directory; the API key is stored in the OS keychain via arxistant_secrets.
# The server proxies requests to any OpenAI-compatible endpoint, streaming the
# answer back to the browser as server-sent events.

DEFAULT_CHAT_CONFIG = {
    "base_url": "",
    "model": "",
    "temperature": 0.7,
}


def load_chat_config():
    config = dict(DEFAULT_CHAT_CONFIG)
    try:
        with open(CHAT_CONFIG_PATH, "r", encoding="utf-8") as f:
            data = json.load(f)
        for key in DEFAULT_CHAT_CONFIG:
            if key in data:
                config[key] = data[key]
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        pass
    return config


def save_chat_config(config):
    os.makedirs(os.path.dirname(CHAT_CONFIG_PATH), exist_ok=True)
    temp_path = CHAT_CONFIG_PATH + ".tmp"
    with open(temp_path, "w", encoding="utf-8") as f:
        json.dump(config, f, indent=2)
    os.replace(temp_path, CHAT_CONFIG_PATH)
    try:
        os.chmod(CHAT_CONFIG_PATH, 0o600)
    except OSError:
        pass


def _chat_file_key():
    try:
        with open(CHAT_CONFIG_PATH, "r", encoding="utf-8") as f:
            return (json.load(f).get("api_key") or "")
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return ""


def get_chat_api_key():
    """Return the LLM API key.

    Prefers the (chmod-600) chat config file, which is the reliable store when
    the server runs as a background daemon detached from the macOS login
    session; falls back to the OS keychain. Preferring the file also ensures a
    freshly saved key overrides any stale keychain entry.
    """
    key = _chat_file_key()
    if key:
        return key
    return arxistant_secrets.get_secret(arxistant_secrets.LLM_API_KEY) or ""


def chat_key_storage():
    """Report where the API key lives: 'file', 'keychain', or ''."""
    if _chat_file_key():
        return "file"
    if arxistant_secrets.get_secret(arxistant_secrets.LLM_API_KEY):
        return "keychain"
    return ""


def _load_ranked_papers(path, source):
    """Read a ranked-list JSON snapshot written by the daily/recent refresh."""
    try:
        with open(path, "r", encoding="utf-8") as f:
            items = json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return []
    papers = []
    for item in items:
        arxiv_id = str(item.get("id", "") or "").strip()
        if not arxiv_id:
            continue
        authors = item.get("authors", "")
        if isinstance(authors, list):
            authors = ", ".join(authors)
        papers.append({
            "arxiv_id": arxiv_id,
            "title": (item.get("title") or "").strip(),
            "authors": authors,
            "abstract": (item.get("abstract") or "").strip(),
            "source": source,
            "score": item.get("score", 0),
        })
    return papers


def collect_chat_library():
    """Merge saved papers with the daily/recent ranked lists for the chat picker.

    Saved papers win over ranked-list duplicates; daily wins over recent.
    """
    papers = {}
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute(
        "SELECT arxiv_id, title, authors, abstract, relevance_score, notes, highlights "
        "FROM saved_papers ORDER BY date_saved DESC")
    for arxiv_id, title, authors, abstract, score, notes, highlights in c.fetchall():
        papers[arxiv_id] = {
            "arxiv_id": arxiv_id,
            "title": title or "",
            "authors": authors or "",
            "abstract": abstract or "",
            "source": "saved",
            "score": score or 0,
            "notes": notes or "",
            "highlights": parse_highlights(highlights),
        }
    conn.close()
    for source, path in (("daily", DAILY_JSON), ("recent", RECENT_JSON)):
        for item in _load_ranked_papers(path, source):
            papers.setdefault(item["arxiv_id"], item)
    return list(papers.values())


def build_chat_request(base_url, model, messages, temperature, api_key):
    """Build the upstream OpenAI-compatible /chat/completions request."""
    url = base_url.strip().rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    payload = {"model": model, "messages": messages, "stream": True}
    if temperature is not None:
        payload["temperature"] = temperature
    headers = {
        "Content-Type": "application/json",
        "Accept": "text/event-stream",
        "User-Agent": "ArXistant/0.1",
    }
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    return urllib.request.Request(
        url, data=json.dumps(payload).encode("utf-8"),
        headers=headers, method="POST")


def iter_chat_sse(resp):
    """Yield SSE 'data: ...' lines from an LLM API response.

    Handles both true event streams and providers that ignore ``stream`` and
    return one JSON document; the latter is converted into a single event.
    """
    content_type = ""
    headers = getattr(resp, "headers", None)
    if headers is not None and hasattr(headers, "get"):
        content_type = headers.get("Content-Type", "") or ""
    if "text/event-stream" in content_type:
        for raw in resp:
            line = raw.decode("utf-8", "replace").strip()
            if line.startswith("data:"):
                yield line
    else:
        body = resp.read().decode("utf-8", "replace")
        yield "data: " + body
        yield "data: [DONE]"


# --- Web search (keyless DuckDuckGo) + tool-calling agent loop --------------

CHAT_TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "web_search",
            "description": ("Search the internet for up-to-date or general information "
                            "that is not in the paper. Use only when the question needs "
                            "external knowledge."),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The search query."}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "search_papers",
            "description": ("Search the scientific literature (Semantic Scholar) for papers "
                            "on a topic. Returns a list of papers with title, arXiv id, year "
                            "and citation count. Use when the user asks to find/search papers."),
            "parameters": {
                "type": "object",
                "properties": {"query": {"type": "string", "description": "The topic to search."}},
                "required": ["query"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "find_related",
            "description": ("Given an arXiv id, return similar/related papers. Use to suggest "
                            "more papers like one already being discussed."),
            "parameters": {
                "type": "object",
                "properties": {"arxiv_id": {"type": "string", "description": "The arXiv id seed."}},
                "required": ["arxiv_id"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "citation_graph",
            "description": ("Return papers that cite a given paper (kind=citedby) or the papers "
                            "it references (kind=references), via ADS."),
            "parameters": {
                "type": "object",
                "properties": {
                    "arxiv_id": {"type": "string"},
                    "kind": {"type": "string", "enum": ["citedby", "references"]},
                },
                "required": ["arxiv_id"],
            },
        },
    },
]

_TAG_RE = re.compile(r"<[^>]+>")


def _clean_text(s):
    s = _TAG_RE.sub("", s or "")
    return re.sub(r"\s+", " ", html_unescape(s)).strip()


def _ddg_real_url(href):
    # DuckDuckGo wraps outbound links in a redirect with a uddg= parameter.
    if "uddg=" in href:
        try:
            q = urllib.parse.parse_qs(urllib.parse.urlparse(href).query)
            return q.get("uddg", [href])[0]
        except Exception:
            return href
    if href.startswith("//"):
        return "https:" + href
    return href


def web_search(query, max_results=6):
    """Keyless web search via DuckDuckGo HTML. Returns a plain-text summary."""
    url = "https://html.duckduckgo.com/html/?q=" + urllib.parse.quote(query)
    req = urllib.request.Request(
        url, headers={"User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                                    "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0 Safari/537.36"})
    try:
        with urllib.request.urlopen(req, timeout=20) as resp:
            html = resp.read().decode("utf-8", "replace")
    except Exception as e:
        return "Web search failed: %s" % e
    hrefs = re.findall(r'class="result__a"[^>]*href="([^"]*)"', html)
    titles = re.findall(r'class="result__a"[^>]*>(.*?)</a>', html, re.S)
    snips = re.findall(r'class="result__snippet"[^>]*>(.*?)</a>', html, re.S)
    lines = []
    for i in range(min(max_results, len(titles))):
        title = _clean_text(titles[i]) if i < len(titles) else ""
        snip = _clean_text(snips[i]) if i < len(snips) else ""
        link = _ddg_real_url(hrefs[i]) if i < len(hrefs) else ""
        if not title:
            continue
        lines.append(f"{len(lines) + 1}. {title}" + (f" — {snip}" if snip else "") + (f" [{link}]" if link else ""))
    if not lines:
        return "No web results found for: " + query
    return "Web results for \"%s\":\n" % query + "\n".join(lines)


def _extract_text_tool_queries(content):
    """Pull web_search queries out of tool calls the model wrote as plain text.

    Some providers emit DeepSeek-style DSML (or JSON) tool calls in the message
    content instead of the structured ``tool_calls`` field.
    """
    qs = re.findall(r'name="query"[^>]*string="true">([^<]+)', content or "")
    if not qs:
        qs = re.findall(r'"name"\s*:\s*"web_search"[^}]*?"query"\s*:\s*"([^"]+)"', content or "", re.S)
    return [q.strip() for q in qs if q.strip()]


def _extract_text_tool_calls(content):
    """Pull (name, params) tool calls out of DSML/JSON written as plain text."""
    calls = []
    if not content:
        return calls
    for m in re.finditer(r'<｜｜DSML｜｜invoke name="([^"]+)">(.*?)(?=<｜｜DSML｜｜/invoke>|<｜｜DSML｜｜invoke|$)',
                         content, re.S):
        name = m.group(1)
        block = m.group(2)
        params = dict(re.findall(r'<｜｜DSML｜｜parameter name="([^"]+)"[^>]*>([^<]*)', block))
        calls.append((name, params))
    if not calls:
        for m in re.finditer(r'\{\s*"name"\s*:\s*"(\w+)"\s*,\s*"arguments"\s*:\s*(\{[^}]*\})', content or "", re.S):
            try:
                calls.append((m.group(1), json.loads(m.group(2))))
            except Exception:
                pass
    return calls


def _strip_tool_markup(content):
    """Remove any raw tool-call markup so it never reaches the user."""
    if not content:
        return ""
    idx = content.find("<｜｜DSML｜｜")
    if idx != -1:
        content = content[:idx]
    content = re.sub(r"```(?:json)?\s*\{\s*\"name\"\s*:\s*\"web_search\".*?```", "", content, flags=re.S)
    return content.strip()


# --- Paper discovery helpers (Semantic Scholar / ADS / local / full-text) ----

S2_FIELDS = "title,year,authors,externalIds,citationCount,tldr,abstract"


def _s2_to_item(p):
    ext = p.get("externalIds") or {}
    authors = p.get("authors") or []
    if authors and isinstance(authors[0], dict):
        authors = [a.get("name", "") for a in authors]
    tldr = p.get("tldr")
    return {
        "arxiv_id": ext.get("ArXiv") or "",
        "title": p.get("title") or "",
        "authors": ", ".join(authors),
        "year": p.get("year"),
        "citations": p.get("citationCount") or 0,
        "tldr": (tldr or {}).get("text") if isinstance(tldr, dict) else "",
        "abstract": p.get("abstract") or "",
        "source": "semanticscholar",
    }


def s2_search(query, limit=10):
    """Semantic Scholar keyword search with citation counts + TLDR (keyless)."""
    url = ("https://api.semanticscholar.org/graph/v1/paper/search?query="
           + urllib.parse.quote(query) + f"&limit={limit}&fields=" + S2_FIELDS)
    req = urllib.request.Request(url, headers={"User-Agent": "ArXistant/0.1"})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [_s2_to_item(p) for p in (data.get("data") or [])]


def s2_related(arxiv_id, limit=10):
    """Semantic Scholar 'more like this' recommendations (keyless)."""
    url = f"https://api.semanticscholar.org/recommendations/v1/papers/?limit={limit}&fields={S2_FIELDS}"
    req = urllib.request.Request(
        url, data=json.dumps({"positivePaperIds": ["arXiv:" + arxiv_id]}).encode("utf-8"),
        headers={"Content-Type": "application/json", "User-Agent": "ArXistant/0.1"},
        method="POST")
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    return [_s2_to_item(p) for p in (data.get("recommendedPapers") or [])]


def ads_bibcode_for(arxiv_id):
    token = load_ads_token()
    if not token:
        return None, None
    q = urllib.parse.quote("arXiv:" + arxiv_id)
    url = ("https://api.adsabs.harvard.edu/v1/search/query?q=" + q
           + "&fl=bibcode,title&rows=1")
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    docs = (data.get("response") or {}).get("docs") or []
    if not docs:
        return None, token
    return docs[0].get("bibcode"), token


def _ads_query(q, token, limit=10):
    params = urllib.parse.urlencode({"q": q, "fl": "bibcode,title,author,year,citation_count,arxiv", "rows": limit})
    url = "https://api.adsabs.harvard.edu/v1/search/query?" + params
    req = urllib.request.Request(url, headers={"Authorization": "Bearer " + token})
    with urllib.request.urlopen(req, timeout=30) as resp:
        data = json.loads(resp.read().decode("utf-8"))
    items = []
    for d in (data.get("response") or {}).get("docs") or []:
        authors = d.get("author") or []
        arxiv = d.get("arxiv") or ""
        items.append({
            "arxiv_id": arxiv if arxiv and "." in arxiv else "",
            "title": (d.get("title") or [""])[0] if isinstance(d.get("title"), list) else (d.get("title") or ""),
            "authors": ", ".join(authors),
            "year": d.get("year"),
            "citations": d.get("citation_count") or 0,
            "tldr": "", "abstract": "", "source": "ads",
        })
    return items


def ads_graph(arxiv_id, kind, limit=10):
    """ADS citation graph: kind in {citedby, references}."""
    bib, token = ads_bibcode_for(arxiv_id)
    if not token:
        raise ValueError("ADS token not configured")
    if not bib:
        return []
    op = "citations" if kind == "citedby" else "references"
    return _ads_query(f"{op}(bibcode:{bib})", token, limit)


def ads_topic(kind, query, limit=10):
    """ADS second-order operators: reviews(<q>) / trending(<q>)."""
    token = load_ads_token()
    if not token:
        raise ValueError("ADS token not configured")
    return _ads_query(f"{kind}({query})", token, limit)


def local_similar(arxiv_id, limit=10):
    """TF-IDF cosine similarity over the saved+daily library (offline, private)."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity
    papers = collect_chat_library()
    seed = next((p for p in papers if p["arxiv_id"] == arxiv_id), None)
    if seed is None:
        return []
    corpus = [(p["title"] or "") + " " + (p["abstract"] or "") for p in papers]
    vec = TfidfVectorizer(max_features=20000, stop_words="english")
    try:
        m = vec.fit_transform(corpus)
    except ValueError:
        return []
    idx = papers.index(seed)
    sims = cosine_similarity(m[idx:idx + 1], m)[0]
    order = sorted(range(len(papers)), key=lambda i: -sims[i])
    out = []
    for i in order:
        if papers[i]["arxiv_id"] == arxiv_id:
            continue
        out.append({**papers[i], "citations": 0, "tldr": "", "source": papers[i]["source"] + " (similar)"})
        if len(out) >= limit:
            break
    return out


def fulltext_search(query, limit=10):
    """Search the cached full-text HTML of papers you've opened."""
    q = query.lower()
    results = []
    try:
        names = os.listdir(FULLTEXT_CACHE_DIR)
    except OSError:
        return []
    for name in sorted(names):
        if not name.endswith(".html"):
            continue
        path = os.path.join(FULLTEXT_CACHE_DIR, name)
        try:
            with open(path, "r", encoding="utf-8", errors="replace") as f:
                html = f.read()
        except OSError:
            continue
        low = html.lower()
        pos = low.find(q)
        if pos == -1:
            continue
        snippet = _clean_text(html[max(0, pos - 120): pos + 240])
        results.append({"arxiv_id": name[:-5], "snippet": snippet, "source": "fulltext"})
        if len(results) >= limit:
            break
    return results


def expand_query_with_llm(query):
    """Ask the LLM to produce strong search queries/keywords for a topic."""
    config = load_chat_config()
    base_url = config.get("base_url", "").strip()
    model = config.get("model", "").strip()
    api_key = get_chat_api_key()
    if not base_url or not model:
        return []
    msgs = [
        {"role": "system", "content":
         "You expand astronomy literature search topics. Return ONLY a JSON array of 3-5 "
         "search queries (strings) suitable for arXiv/Semantic Scholar, using precise "
         "astronomy terminology. No prose."},
        {"role": "user", "content": query},
    ]
    try:
        resp = _chat_completion(base_url, model, msgs, 0.3, api_key, tools=None)
    except Exception:
        return []
    content = ((resp.get("choices") or [{}])[0].get("message") or {}).get("content") or ""
    m = re.search(r"\[.*\]", content, re.S)
    if not m:
        return []
    try:
        arr = json.loads(m.group(0))
        return [str(x) for x in arr if isinstance(x, (str, int))][:5]
    except Exception:
        return []


def _chat_completion(base_url, model, messages, temperature, api_key, tools=None):
    """One non-streaming OpenAI-compatible chat completion; returns parsed JSON."""
    url = base_url.strip().rstrip("/")
    if not url.endswith("/chat/completions"):
        url += "/chat/completions"
    payload = {"model": model, "messages": messages, "stream": False}
    if temperature is not None:
        payload["temperature"] = temperature
    if tools:
        payload["tools"] = tools
    headers = {"Content-Type": "application/json", "User-Agent": "ArXistant/0.1"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                 headers=headers, method="POST")
    with urllib.request.urlopen(req, timeout=240) as resp:
        return json.loads(resp.read().decode("utf-8"))


def papers_to_text(papers):
    """Compact text summary of paper results, for feeding back to the LLM."""
    if not papers:
        return "No papers found."
    lines = []
    for i, p in enumerate(papers[:10]):
        lines.append(f"{i + 1}. {p.get('title', '')} "
                     f"(arXiv:{p.get('arxiv_id', '')}, {p.get('year') or '?'}, "
                     f"{p.get('citations') or 0} citations)")
    return "\n".join(lines)


def run_tool(name, args):
    """Dispatch a chat tool. Returns (kind, value): kind 'text' or 'papers'."""
    try:
        if name == "web_search":
            return "text", web_search(args.get("query") or "")
        if name == "search_papers":
            return "papers", s2_search(args.get("query") or "")
        if name == "find_related":
            aid = args.get("arxiv_id") or ""
            try:
                rel = s2_related(aid)
            except Exception:
                rel = []
            return "papers", (rel or local_similar(aid))
        if name == "citation_graph":
            return "papers", ads_graph(args.get("arxiv_id") or "", args.get("kind") or "citedby")
        return "text", "Unknown tool: " + str(name)
    except Exception as e:
        return "text", f"Tool {name} failed: {e}"


def run_chat_agent(base_url, model, messages, temperature, api_key, max_iters=4):
    """Run a tool-calling loop (web_search). Returns a dict.

    {"unsupported": True}  -> provider rejected tools; caller should fall back.
    {"error": ...}         -> hard failure.
    {"final": str, "statuses": [queries]} -> success.
    """
    msgs = [dict(m) for m in messages]
    statuses = []

    def complete(use_tools):
        return _chat_completion(base_url, model, msgs, temperature, api_key,
                                tools=CHAT_TOOLS if use_tools else None)

    def http_error(e):
        body = ""
        try:
            body = e.read().decode("utf-8", "replace")[:500]
        except Exception:
            pass
        return {"unsupported": False, "error": f"LLM API returned HTTP {e.code}", "details": body}

    for _ in range(max_iters):
        try:
            resp = complete(use_tools=True)
        except urllib.error.HTTPError as e:
            body = ""
            try:
                body = e.read().decode("utf-8", "replace")[:500]
            except Exception:
                pass
            if e.code == 400 and ("tool" in body.lower() or "function" in body.lower()):
                return {"unsupported": True}
            return http_error(e)
        except Exception as e:
            return {"unsupported": False, "error": str(e)}
        choice = (resp.get("choices") or [{}])[0]
        msg = choice.get("message") or {}
        tool_calls = msg.get("tool_calls") or []
        content = msg.get("content") or ""
        if not tool_calls:
            text_queries = _extract_text_tool_queries(content)
            if text_queries:
                # Provider emitted tool calls as text: run them and feed results
                # back as a user message (works with any provider).
                for q in text_queries[:3]:
                    statuses.append(q)
                results = "\n\n".join(web_search(q) for q in text_queries[:3])
                msgs.append({"role": "assistant",
                             "content": _strip_tool_markup(content) or "(searching)"})
                msgs.append({"role": "user", "content": "Web search results:\n" + results})
                continue
            if content:
                return {"unsupported": False, "final": _strip_tool_markup(content), "statuses": statuses}
            break  # empty answer; fall through to a forced final answer
        msgs.append({"role": "assistant", "content": content, "tool_calls": tool_calls})
        for tc in tool_calls:
            fn = tc.get("function") or {}
            name = fn.get("name")
            try:
                args = json.loads(fn.get("arguments") or "{}")
            except Exception:
                args = {}
            kind, value = run_tool(name, args)
            if name == "web_search":
                statuses.append((args.get("query") or "").strip())
            msgs.append({"role": "tool", "tool_call_id": tc.get("id"),
                         "content": value if kind == "text" else papers_to_text(value)})

    # Forced final answer (no tools) so a search question never returns empty.
    msgs.append({"role": "system", "content":
                 "Answer the user's question now using the information gathered above. "
                 "Do not call any tools. Cite web URLs where relevant."})
    try:
        resp = complete(use_tools=False)
    except urllib.error.HTTPError as e:
        return http_error(e)
    except Exception as e:
        return {"unsupported": False, "error": str(e)}
    choice = (resp.get("choices") or [{}])[0]
    msg = choice.get("message") or {}
    return {"unsupported": False, "final": _strip_tool_markup(msg.get("content") or ""), "statuses": statuses}


PDF_CACHE_DIR = data_path("pdf")
PDF_CACHE_MAX_FILES = 200
PDF_MAX_BYTES = 80 * 1024 * 1024
ARXIV_PDF_URL = "https://arxiv.org/pdf/{arxiv_id}"

_ARXIV_ID_RE = re.compile(r"^[A-Za-z0-9._\-/()]+$")


def _safe_pdf_filename(arxiv_id):
    """Map an arXiv ID to a safe cache file name, or None when invalid."""
    arxiv_id = (arxiv_id or "").strip()
    if (not arxiv_id or ".." in arxiv_id or arxiv_id.count("/") > 1
            or not _ARXIV_ID_RE.match(arxiv_id)):
        return None
    return arxiv_id.replace("/", "_") + ".pdf"


def fetch_paper_pdf(arxiv_id):
    """Return a local path for the paper PDF, downloading and caching it.

    Raises ValueError for an invalid ID or when arXiv does not return a PDF,
    and urllib errors for network failures.
    """
    filename = _safe_pdf_filename(arxiv_id)
    if filename is None:
        raise ValueError("Invalid arXiv ID: " + repr(arxiv_id))
    os.makedirs(PDF_CACHE_DIR, exist_ok=True)
    path = os.path.join(PDF_CACHE_DIR, filename)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    url = ARXIV_PDF_URL.format(arxiv_id=urllib.parse.quote(arxiv_id, safe="/"))
    req = urllib.request.Request(
        url, headers={"User-Agent": "ArXistant/0.1 (local research assistant)"})
    temp_path = f"{path}.{threading.get_ident()}.tmp"
    try:
        # Stream straight to disk in chunks so large PDFs never sit in memory.
        with urllib.request.urlopen(req, timeout=60) as resp, \
                open(temp_path, "wb") as f:
            head = b""
            size = 0
            while True:
                chunk = resp.read(65536)
                if not chunk:
                    break
                if size == 0:
                    head = chunk.lstrip()[:5]
                size += len(chunk)
                if size > PDF_MAX_BYTES:
                    raise ValueError(
                        "The PDF is too large to cache (over "
                        f"{PDF_MAX_BYTES // (1024 * 1024)} MB)")
                f.write(chunk)
        if size < 1024 or not head.startswith(b"%PDF"):
            raise ValueError("arXiv did not return a PDF for " + arxiv_id)
        os.replace(temp_path, path)
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)
            except OSError:
                pass
    _evict_pdf_cache()
    return path


def _evict_pdf_cache():
    """Keep the PDF cache bounded by dropping the least recently used files."""
    try:
        entries = []
        for name in os.listdir(PDF_CACHE_DIR):
            full = os.path.join(PDF_CACHE_DIR, name)
            if name.endswith(".pdf") and os.path.isfile(full):
                entries.append((os.path.getmtime(full), full))
        entries.sort()
        while len(entries) > PDF_CACHE_MAX_FILES:
            _, oldest = entries.pop(0)
            try:
                os.remove(oldest)
            except OSError:
                pass
    except OSError:
        pass


FULLTEXT_CACHE_DIR = data_path("fulltext")
FULLTEXT_MAX_BYTES = 8 * 1024 * 1024
ARXIV_HTML_URLS = (
    "https://arxiv.org/html/{arxiv_id}",
    "https://ar5iv.labs.arxiv.org/html/{arxiv_id}",
)

_SCRIPT_RE = re.compile(r"<script\b[^>]*>.*?</script>", re.S | re.I)


def fetch_paper_fulltext(arxiv_id):
    """Return a local path to a sanitized HTML copy of the paper's full text.

    Prefers arxiv.org/html, falling back to ar5iv. Scripts are stripped and a
    <base> tag is injected so relative assets resolve at the source. Cached.
    """
    base = _safe_pdf_filename(arxiv_id)
    if base is None:
        raise ValueError("Invalid arXiv ID: " + repr(arxiv_id))
    filename = base[:-4] + ".html"
    os.makedirs(FULLTEXT_CACHE_DIR, exist_ok=True)
    path = os.path.join(FULLTEXT_CACHE_DIR, filename)
    if os.path.exists(path) and os.path.getsize(path) > 0:
        return path
    last_err = None
    for tmpl in ARXIV_HTML_URLS:
        url = tmpl.format(arxiv_id=urllib.parse.quote(arxiv_id, safe="/"))
        req = urllib.request.Request(
            url, headers={"User-Agent": "ArXistant/0.1 (local research assistant)"})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                html = resp.read(FULLTEXT_MAX_BYTES).decode("utf-8", "replace")
        except Exception as e:
            last_err = e
            continue
        low = html.lower()
        if "<html" not in low and "<!doctype" not in low:
            last_err = ValueError("response is not HTML")
            continue
        html = _SCRIPT_RE.sub("", html)
        base_href = "https://arxiv.org/html/" + urllib.parse.quote(arxiv_id, safe="/")
        if re.search(r"<head\b[^>]*>", html, re.I):
            html = re.sub(r"<head\b[^>]*>",
                          lambda m: m.group(0) + '<base href="' + base_href + '">',
                          html, count=1, flags=re.I)
        else:
            html = '<base href="' + base_href + '">' + html
        temp_path = path + ".tmp"
        with open(temp_path, "w", encoding="utf-8") as f:
            f.write(html)
        os.replace(temp_path, path)
        return path
    raise ValueError("Could not fetch full text for " + arxiv_id +
                     (": " + str(last_err) if last_err else ""))

# --- End chat helpers --------------------------------------------------------

def normalize_tags(value):
    """Canonicalize a tag list into the comma-joined TEXT column format.

    Accepts a list of tags or a comma-separated string. Tags are trimmed,
    empty entries dropped, and duplicates removed case-insensitively (the
    first spelling wins). Returns a comma-joined string.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        items = value.split(",")
    elif isinstance(value, (list, tuple)):
        items = value
    else:
        items = [value]
    seen = set()
    out = []
    for item in items:
        tag = str(item).strip()
        if not tag:
            continue
        key = tag.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(tag[:60])
    return ",".join(out[:50])


def split_tags(value):
    """Parse the comma-joined tags column back into a list of tags."""
    if not value:
        return []
    return [t for t in (part.strip() for part in str(value).split(",")) if t]


def normalize_highlights(value):
    """Canonicalize a highlight list into the JSON TEXT column format.

    Accepts a list of passage quotes (or a JSON string encoding one). Quotes
    are whitespace-normalized, deduplicated, and bounded so the column cannot
    grow without limit. Returns the JSON array string.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        try:
            value = json.loads(value)
        except (json.JSONDecodeError, ValueError):
            return ""
    if not isinstance(value, (list, tuple)):
        return ""
    seen = set()
    out = []
    for item in value:
        quote = " ".join(str(item).split())
        if len(quote) < 3 or len(quote) > 2000:
            continue
        key = quote.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(quote)
        if len(out) >= 200:
            break
    return json.dumps(out, ensure_ascii=False) if out else ""


def parse_highlights(value):
    """Parse the highlights column back into a list of quotes."""
    if not value:
        return []
    try:
        parsed = json.loads(value)
    except (json.JSONDecodeError, ValueError, TypeError):
        return []
    return parsed if isinstance(parsed, list) else []


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
            notes TEXT DEFAULT '',
            tags TEXT DEFAULT '',
            highlights TEXT DEFAULT ''
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
    
    arxistant_sync.migrate_db(conn)

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
            INSERT OR IGNORE INTO my_publications (bibcode, title, authors, abstract, keywords, year, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
        ''', (*paper, arxistant_sync.now_iso()))
    
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
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())

    def _send_html(self, html, status=200):
        if '<!-- arxistant-mobile-menu -->' not in html:
            html = html.replace('</body>', MOBILE_MENU_SCRIPT + '</body>')
        self.send_response(status)
        self.send_header("Content-Type", "text/html; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(html.encode())

    def _send_text(self, text, status=200):
        self.send_response(status)
        self.send_header("Content-Type", "text/plain; charset=utf-8")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()
        self.wfile.write(text.encode())

    def _not_found_page(self, title, message, api_endpoint, button_label):
        """Return an HTML page with a message and an optional button."""
        button_html = ""
        if api_endpoint and button_label:
            button_html = f"""<button class="btn" id="generate-btn" onclick="generate()">{button_label}</button>
  <div id="status"></div>
  <script>
    async function generate() {{
      const btn = document.getElementById('generate-btn');
      const status = document.getElementById('status');
      btn.disabled = true;
      btn.textContent = 'Generating…';
      status.textContent = '';

      try {{
        const resp = await fetch('{api_endpoint}', {{ method: 'POST' }});
        const data = await resp.json();
        if (data.success) {{
          status.textContent = 'Done! Reloading the page…';
          setTimeout(() => location.reload(), 1500);
        }} else {{
          status.textContent = 'Error: ' + (data.error || 'Unknown error');
          btn.disabled = false;
          btn.textContent = '{button_label}';
        }}
      }} catch (e) {{
        status.textContent = 'Could not reach the server: ' + e.message;
        btn.disabled = false;
        btn.textContent = '{button_label}';
      }}
    }}
  </script>"""

        html = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>{title} — ArXistant</title>
  <style>
    body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 600px; margin: 80px auto; text-align: center; color: #333; }}
    h1 {{ color: #b31b1b; }}
    .btn {{ display: inline-block; margin-top: 20px; padding: 12px 24px; background: #b31b1b; color: white; border: none; border-radius: 6px; font-size: 1em; cursor: pointer; text-decoration: none; }}
    .btn:hover {{ background: #8b1515; }}
    .btn:disabled {{ background: #ccc; cursor: wait; }}
    #status {{ margin-top: 16px; font-size: 0.9em; color: #666; }}
    .nav {{ margin-top: 30px; }}
    .nav a {{ color: #b31b1b; text-decoration: none; margin: 0 8px; }}
    .nav a:hover {{ text-decoration: underline; }}
  </style>
</head>
<body>
  <h1>{title}</h1>
  <p>{message}</p>
  {button_html}
  <div class="nav">
    <a href="/">Daily Papers</a> ·
    <a href="/database.html">Saved Papers</a> ·
    <a href="/cloud-sync.html">☁️ Cloud Sync</a>
  </div>
</body>
</html>"""
        self._send_html(html)

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

        if path == "/api/health":
            self._send_json({
                "success": True,
                "api_version": SERVER_API_VERSION,
                "data_dir": DATA_DIR,
            })

        elif path == "/" or path == "/index.html":
            if os.path.exists(DAILY_HTML):
                with open(DAILY_HTML, 'r', encoding='utf-8') as f:
                    html = f.read()
                if '<!-- save-button-embedded -->' not in html:
                    html = html.replace('</body>', SAVE_BUTTON_SCRIPT + '</body>')
                if '<!-- chat-link-embedded -->' not in html:
                    html = html.replace('</body>', CHAT_LINK_SCRIPT + '</body>')
                self._send_html(html)
            else:
                self._not_found_page(
                    "Daily Paper List",
                    "No daily paper list has been generated yet.",
                    "/api/refresh-daily",
                    "Generate Daily Papers")

        elif path == "/daily.html":
            if os.path.exists(DAILY_HTML):
                with open(DAILY_HTML, 'r', encoding='utf-8') as f:
                    html = f.read()
                if '<!-- save-button-embedded -->' not in html:
                    html = html.replace('</body>', SAVE_BUTTON_SCRIPT + '</body>')
                if '<!-- chat-link-embedded -->' not in html:
                    html = html.replace('</body>', CHAT_LINK_SCRIPT + '</body>')
                self._send_html(html)
            else:
                self._not_found_page(
                    "Daily Paper List",
                    "No daily paper list has been generated yet.",
                    "/api/refresh-daily",
                    "Generate Daily Papers")

        elif path == "/recent.html":
            if os.path.exists(RECENT_HTML):
                with open(RECENT_HTML, 'r', encoding='utf-8') as f:
                    html = f.read()
                if '<!-- save-button-embedded -->' not in html:
                    html = html.replace('</body>', SAVE_BUTTON_SCRIPT + '</body>')
                if '<!-- chat-link-embedded -->' not in html:
                    html = html.replace('</body>', CHAT_LINK_SCRIPT + '</body>')
                self._send_html(html)
            else:
                self._not_found_page(
                    "Recent Paper List",
                    "No recent paper list has been generated yet.",
                    "/api/refresh-recent",
                    "Generate Recent Papers")

        elif path == "/database.html":
            self._send_html(DATABASE_VIEWER_HTML)

        elif path == "/cloud-sync.html":
            self._send_html(CLOUD_SYNC_HTML)

        elif path == "/publications.html":
            self._send_html(PUBLICATIONS_VIEWER_HTML)

        elif path == "/ml-features.html":
            if os.path.exists(ML_FEATURES_HTML):
                with open(ML_FEATURES_HTML, 'r', encoding='utf-8') as f:
                    self._send_html(f.read())
            else:
                self._send_html(ML_TRAIN_FALLBACK_HTML)


        elif path == "/chat.html":
            self._send_html(CHAT_PAGE_HTML)

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

        elif path == "/api/cloud/status":
            config = arxistant_sync.load_config()
            status = {
                "success": True,
                "enabled": config.get("enabled"),
                "provider": config.get("provider"),
                "device_id": config.get("device_id"),
                "interval_minutes": config.get("interval_minutes"),
                "last_sync_at": config.get("last_sync_at"),
                "last_error": config.get("last_error"),
                "keychain_available": arxistant_secrets.is_available(),
                "config": {
                    "local_folder_path": config["local_folder"].get("path", ""),
                    "webdav_url": config["webdav"].get("url", ""),
                    "webdav_username": config["webdav"].get("username", ""),
                    "webdav_password_set": bool(arxistant_secrets.get_secret(
                        arxistant_secrets.WEBDAV_PASSWORD)),
                },
            }
            try:
                status["provider_status"] = arxistant_sync.get_provider(config).status()
            except Exception as exc:
                status["provider_status"] = {"error": str(exc)}
            self._send_json(status)

        elif path == "/api/chat/config":
            config = load_chat_config()
            self._send_json({
                "success": True,
                "base_url": config["base_url"],
                "model": config["model"],
                "temperature": config["temperature"],
                "has_api_key": bool(get_chat_api_key()),
                "key_storage": chat_key_storage(),
                "keychain_available": arxistant_secrets.is_available(),
            })

        elif path == "/api/chat/library":
            try:
                papers = collect_chat_library()
                self._send_json({
                    "success": True, "papers": papers, "count": len(papers)})
            except Exception as e:
                self._send_json(
                    {"success": False, "error": str(e), "papers": []}, 500)

        elif path == "/api/discover/related":
            arxiv_id = query.get("arxiv_id", [""])[0]
            try:
                self._send_json({"success": True, "papers": s2_related(arxiv_id)})
            except Exception as e:
                self._send_json({"success": False, "error": str(e), "papers": []}, 502)

        elif path == "/api/discover/similar":
            arxiv_id = query.get("arxiv_id", [""])[0]
            try:
                self._send_json({"success": True, "papers": local_similar(arxiv_id)})
            except Exception as e:
                self._send_json({"success": False, "error": str(e), "papers": []}, 500)

        elif path == "/api/discover/scholar":
            q = query.get("q", [""])[0]
            try:
                self._send_json({"success": True, "papers": s2_search(q)})
            except Exception as e:
                self._send_json({"success": False, "error": str(e), "papers": []}, 502)

        elif path == "/api/discover/ads":
            arxiv_id = query.get("arxiv_id", [""])[0]
            kind = query.get("kind", ["citedby"])[0]
            try:
                self._send_json({"success": True, "papers": ads_graph(arxiv_id, kind)})
            except Exception as e:
                self._send_json({"success": False, "error": str(e), "papers": []}, 502)

        elif path == "/api/discover/ads-topic":
            kind = query.get("kind", ["reviews"])[0]
            q = query.get("q", [""])[0]
            try:
                self._send_json({"success": True, "papers": ads_topic(kind, q)})
            except Exception as e:
                self._send_json({"success": False, "error": str(e), "papers": []}, 502)

        elif path == "/api/discover/fulltext":
            q = query.get("q", [""])[0]
            try:
                self._send_json({"success": True, "papers": fulltext_search(q)})
            except Exception as e:
                self._send_json({"success": False, "error": str(e), "papers": []}, 500)

        elif path == "/api/discover/expand":
            q = query.get("q", [""])[0]
            self._send_json({"success": True, "queries": expand_query_with_llm(q)})

        elif path == "/api/chat/pdf":
            arxiv_id = query.get("arxiv_id", [""])[0]
            filename = _safe_pdf_filename(arxiv_id)
            if filename is None:
                self._send_json(
                    {"success": False, "error": "Invalid arXiv ID"}, 400)
                return
            try:
                pdf_path = fetch_paper_pdf(arxiv_id)
            except Exception as e:
                safe_id = html_escape(arxiv_id, quote=True)
                self._send_html(
                    "<!DOCTYPE html><html><body style='font-family:sans-serif;"
                    "color:#333;padding:24px;line-height:1.6;'>"
                    f"<p><strong>Could not download the PDF for arXiv:{safe_id}.</strong></p>"
                    f"<p>{html_escape(str(e))}</p>"
                    f"<p><a href='https://arxiv.org/pdf/{safe_id}' target='_blank'>"
                    "Open the PDF on arXiv instead ↗</a></p>"
                    "</body></html>", 502)
                return
            with open(pdf_path, "rb") as f:
                self.send_response(200)
                self.send_header("Content-Type", "application/pdf")
                self.send_header("Content-Length", str(os.fstat(f.fileno()).st_size))
                self.send_header("Content-Disposition", f'inline; filename="{filename}"')
                self.send_header("Access-Control-Allow-Origin", "*")
                self.end_headers()
                while True:
                    chunk = f.read(65536)
                    if not chunk:
                        break
                    self.wfile.write(chunk)

        elif path == "/api/chat/fulltext":
            arxiv_id = query.get("arxiv_id", [""])[0]
            try:
                ft_path = fetch_paper_fulltext(arxiv_id)
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 502)
                return
            with open(ft_path, "r", encoding="utf-8") as f:
                content = f.read()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(content.encode("utf-8"))

        else:
            self._send_text("Not found", 404)

    def _start_sse(self):
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Cache-Control", "no-cache")
        self.send_header("Access-Control-Allow-Origin", "*")
        self.end_headers()

    def _sse_write(self, obj):
        if isinstance(obj, (bytes, bytearray)):
            self.wfile.write(obj)
        else:
            self.wfile.write(("data: " + json.dumps(obj) + "\n\n").encode("utf-8"))
        self.wfile.flush()

    def _stream_one(self, base_url, model, msgs, temperature, api_key, use_tools):
        """One streaming completion.

        Forwards content deltas to the client live (suppressing any tool-call
        markup so it never reaches the user). Returns (content, tool_calls).
        """
        url = base_url.strip().rstrip("/")
        if not url.endswith("/chat/completions"):
            url += "/chat/completions"
        payload = {"model": model, "messages": msgs, "stream": True}
        if temperature is not None:
            payload["temperature"] = temperature
        if use_tools:
            payload["tools"] = CHAT_TOOLS
        headers = {"Content-Type": "application/json",
                   "Accept": "text/event-stream", "User-Agent": "ArXistant/0.1"}
        if api_key:
            headers["Authorization"] = "Bearer " + api_key
        req = urllib.request.Request(url, data=json.dumps(payload).encode("utf-8"),
                                     headers=headers, method="POST")
        resp = urllib.request.urlopen(req, timeout=240)
        content = ""
        tool_calls = {}
        suppress = False
        try:
            ct = resp.headers.get("Content-Type", "") if hasattr(resp, "headers") else ""
            if "text/event-stream" not in ct:
                # Provider ignored stream: read whole body, forward as one delta.
                body = json.loads(resp.read().decode("utf-8"))
                msg = ((body.get("choices") or [{}])[0]).get("message") or {}
                content = msg.get("content") or ""
                for i, tc in enumerate(msg.get("tool_calls") or []):
                    fn = tc.get("function") or {}
                    tool_calls[i] = {"id": tc.get("id"), "name": fn.get("name") or "",
                                     "arguments": fn.get("arguments") or ""}
                if content and not _extract_text_tool_queries(content):
                    self._sse_write({"choices": [{"delta": {"content": content}}]})
                return content, [tool_calls[i] for i in sorted(tool_calls)]
            for raw in resp:
                line = raw.decode("utf-8", "replace").strip()
                if not line.startswith("data:"):
                    continue
                payload_s = line[5:].strip()
                if payload_s == "[DONE]":
                    break
                try:
                    obj = json.loads(payload_s)
                except Exception:
                    continue
                if obj.get("error"):
                    err = obj["error"]
                    raise RuntimeError(err.get("message") if isinstance(err, dict) else str(err))
                choice = (obj.get("choices") or [{}])[0]
                delta = choice.get("delta") or {}
                c = delta.get("content")
                if c:
                    if not suppress:
                        if "<｜｜DSML｜｜" in (content + c):
                            suppress = True   # tool markup begins; stop forwarding
                        else:
                            self._sse_write({"choices": [{"delta": {"content": c}}]})
                    content += c
                for part in (delta.get("tool_calls") or []):
                    idx = part.get("index", 0)
                    slot = tool_calls.setdefault(idx, {"id": None, "name": "", "arguments": ""})
                    if part.get("id"):
                        slot["id"] = part["id"]
                    fn = part.get("function") or {}
                    if fn.get("name"):
                        slot["name"] += fn["name"]
                    if fn.get("arguments"):
                        slot["arguments"] += fn["arguments"]
        finally:
            try:
                resp.close()
            except Exception:
                pass
        return content, [tool_calls[i] for i in sorted(tool_calls)]

    def _run_streaming_agent(self, msgs, base_url, model, temperature, api_key, max_iters=4):
        """Tool-calling loop that streams intermediate output live to the client."""
        for _ in range(max_iters):
            try:
                content, tool_calls = self._stream_one(
                    base_url, model, msgs, temperature, api_key, use_tools=True)
            except urllib.error.HTTPError as e:
                body = ""
                try:
                    body = e.read().decode("utf-8", "replace")[:500]
                except Exception:
                    pass
                if e.code == 400 and ("tool" in body.lower() or "function" in body.lower()):
                    self._stream_one(base_url, model, msgs, temperature, api_key, use_tools=False)
                    self._sse_write(b"data: [DONE]\n\n")
                    return
                self._sse_write({"error": f"LLM API returned HTTP {e.code}"})
                self._sse_write(b"data: [DONE]\n\n")
                return
            except Exception as e:
                self._sse_write({"error": str(e)})
                self._sse_write(b"data: [DONE]\n\n")
                return
            if not tool_calls:
                text_calls = _extract_text_tool_calls(content)
                if text_calls:
                    tool_results = []
                    for name, params in text_calls[:4]:
                        if name == "web_search":
                            self._sse_write({"status": "web_search", "query": params.get("query", "")})
                        kind, value = run_tool(name, params)
                        if kind == "papers":
                            self._sse_write({"status": "papers", "papers": value})
                            tool_results.append(papers_to_text(value))
                        else:
                            tool_results.append(value)
                    msgs.append({"role": "assistant",
                                 "content": _strip_tool_markup(content) or "(searching)"})
                    msgs.append({"role": "user", "content": "Tool results:\n" + "\n\n".join(tool_results)})
                    continue
                self._sse_write(b"data: [DONE]\n\n")
                return
            tc_formatted = []
            for i, tc in enumerate(tool_calls):
                tc_formatted.append({"id": tc.get("id") or f"call_{i}", "type": "function",
                                     "function": {"name": tc["name"], "arguments": tc["arguments"]}})
            msgs.append({"role": "assistant", "content": content, "tool_calls": tc_formatted})
            for i, tc in enumerate(tool_calls):
                try:
                    args = json.loads(tc["arguments"] or "{}")
                except Exception:
                    args = {}
                if tc["name"] == "web_search":
                    self._sse_write({"status": "web_search", "query": (args.get("query") or "").strip()})
                kind, value = run_tool(tc["name"], args)
                if kind == "papers":
                    self._sse_write({"status": "papers", "papers": value})
                    llm_text = papers_to_text(value)
                else:
                    llm_text = value
                msgs.append({"role": "tool", "tool_call_id": tc_formatted[i]["id"], "content": llm_text})
        # Exhausted iterations: force a streamed final answer.
        msgs.append({"role": "system", "content":
                     "Answer the user's question now using the information gathered above. "
                     "Do not call any tools. Cite web URLs where relevant."})
        try:
            self._stream_one(base_url, model, msgs, temperature, api_key, use_tools=False)
        except Exception as e:
            self._sse_write({"error": str(e)})
        self._sse_write(b"data: [DONE]\n\n")

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
                        INSERT OR IGNORE INTO my_publications (bibcode, title, authors, abstract, keywords, year, updated_at)
                        VALUES (?, ?, ?, ?, ?, ?, ?)
                    ''', (bibcode, title, authors, abstracts, keywords, years, arxistant_sync.now_iso()))
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
            if removed:
                arxistant_sync.add_tombstone(conn, "my_publications", bibcode)
            conn.commit()
            conn.close()
            self._send_json({"success": True, "removed": removed, "bibcode": bibcode})

        elif path == "/api/save":
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            try:
                arxiv_id = data.get("arxiv_id", "")
                c.execute("SELECT notes, tags, highlights FROM saved_papers WHERE arxiv_id = ?", (arxiv_id,))
                existing = c.fetchone()
                was_saved = existing is not None
                # Re-saving an existing paper must not wipe notes, tags, or
                # highlights the user already stored; callers only override
                # what they send.
                notes = data.get("notes", existing[0] if existing else "") or ""
                tags = normalize_tags(data["tags"]) if "tags" in data else (
                    existing[1] if existing else "") or ""
                highlights = normalize_highlights(data["highlights"]) if "highlights" in data else (
                    existing[2] if existing else "") or ""
                c.execute('''
                    INSERT OR REPLACE INTO saved_papers 
                    (arxiv_id, title, authors, abstract, relevance_score, date_fetched, notes, tags, highlights, updated_at)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ''', (
                    arxiv_id,
                    data.get("title", ""),
                    data.get("authors", ""),
                    data.get("abstract", ""),
                    data.get("relevance_score", 0),
                    data.get("date_fetched", ""),
                    notes,
                    tags,
                    highlights,
                    arxistant_sync.now_iso()
                ))
                conn.commit()
                arxistant_sync.schedule_auto_sync()
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
            arxiv_id = data.get("arxiv_id", "")
            c.execute("DELETE FROM saved_papers WHERE arxiv_id = ?", (arxiv_id,))
            removed = c.rowcount > 0
            if removed:
                arxistant_sync.add_tombstone(conn, "saved_papers", arxiv_id)
            conn.commit()
            conn.close()
            arxistant_sync.schedule_auto_sync()
            retraining = record_training_change() if removed else get_retrain_state()
            self._send_json({"success": True, "message": "Paper deleted",
                             "preference_changed": removed,
                             "retraining": retraining})

        elif path == "/api/update_notes":
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE saved_papers SET notes = ?, updated_at = ? WHERE arxiv_id = ?", 
                      (data.get("notes", ""), arxistant_sync.now_iso(), data.get("arxiv_id", "")))
            updated = c.rowcount > 0
            conn.commit()
            conn.close()
            if updated:
                arxistant_sync.schedule_auto_sync()
                self._send_json({"success": True, "message": "Notes updated"})
            else:
                self._send_json({"success": False,
                                 "error": "Paper is not in your saved papers"}, 404)

        elif path == "/api/update_tags":
            arxiv_id = data.get("arxiv_id", "")
            tags = normalize_tags(data.get("tags"))
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE saved_papers SET tags = ?, updated_at = ? WHERE arxiv_id = ?",
                      (tags, arxistant_sync.now_iso(), arxiv_id))
            updated = c.rowcount > 0
            conn.commit()
            conn.close()
            if updated:
                arxistant_sync.schedule_auto_sync()
                self._send_json({"success": True, "message": "Tags updated",
                                 "tags": split_tags(tags)})
            else:
                self._send_json({"success": False,
                                 "error": "Paper is not in your saved papers"}, 404)

        elif path == "/api/update_highlights":
            arxiv_id = data.get("arxiv_id", "")
            highlights = normalize_highlights(data.get("highlights"))
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE saved_papers SET highlights = ?, updated_at = ? WHERE arxiv_id = ?",
                      (highlights, arxistant_sync.now_iso(), arxiv_id))
            updated = c.rowcount > 0
            conn.commit()
            conn.close()
            if updated:
                arxistant_sync.schedule_auto_sync()
                self._send_json({"success": True, "message": "Highlights updated",
                                 "highlights": parse_highlights(highlights)})
            else:
                self._send_json({"success": False,
                                 "error": "Paper is not in your saved papers"}, 404)

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
            # Deduplicate: positive wins over negative
            deduplicate_custom_keywords()
            ok, error = arxistant_tasks.regenerate_features()
            if ok:
                self._send_json({"success": True, "message": "Features HTML regenerated (deduplicated)"})
            else:
                self._send_json({"success": False, "error": error}, 500)

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
            ok, error = arxistant_tasks.refresh_daily(DAILY_HTML)
            if ok:
                self._send_json({"success": True})
            else:
                self._send_json({"success": False, "error": error}, 500)

        elif path == "/api/refresh-recent":
            ok, error = arxistant_tasks.refresh_recent(RECENT_HTML)
            if ok:
                self._send_json({"success": True})
            else:
                self._send_json({"success": False, "error": error}, 500)

        elif path == "/api/cloud/settings":
            config = arxistant_sync.load_config()
            if "enabled" in data:
                config["enabled"] = bool(data["enabled"])
            if "interval_minutes" in data:
                config["interval_minutes"] = int(data["interval_minutes"])
            if data.get("provider"):
                config["provider"] = data["provider"]
            if data.get("local_folder_path") is not None:
                config["local_folder"]["path"] = str(data["local_folder_path"]).strip()
            # Non-secret settings go to config; the app password goes to the
            # OS keychain.
            for key in ("url", "username"):
                value = str(data.get(f"webdav_{key}", "") or "").strip()
                if value:
                    config["webdav"][key] = value
            try:
                value = str(data.get("webdav_password", "") or "").strip()
                if value:
                    arxistant_secrets.set_secret(arxistant_secrets.WEBDAV_PASSWORD, value)
            except arxistant_secrets.SecretStoreError as exc:
                self._send_json({"success": False, "error": str(exc)}, 500)
                return
            arxistant_sync.save_config(config)
            self._send_json({"success": True, "config": config})

        elif path == "/api/cloud/sync":
            result = arxistant_sync.run_sync()
            self._send_json(result, 200 if result.get("success") else 502)

        elif path == "/api/cloud/disconnect":
            config = arxistant_sync.load_config()
            config["enabled"] = False
            config["last_sync_at"] = None
            config["last_error"] = None
            arxistant_secrets.delete_secret(arxistant_secrets.WEBDAV_PASSWORD)
            arxistant_sync.save_config(config)
            self._send_json({"success": True, "config": config})

        elif path == "/api/chat/config":
            config = load_chat_config()
            if "base_url" in data:
                config["base_url"] = str(data.get("base_url") or "").strip().rstrip("/")
            if "model" in data:
                config["model"] = str(data.get("model") or "").strip()
            if "temperature" in data:
                try:
                    config["temperature"] = min(2.0, max(0.0, float(data["temperature"])))
                except (TypeError, ValueError):
                    pass
            api_key = str(data.get("api_key") or "").strip()
            if api_key:
                # The chmod-600 config file is the authoritative store (it works
                # even when the keychain is unreachable in a background daemon);
                # the keychain is only a best-effort extra copy.
                config["api_key"] = api_key
                try:
                    arxistant_secrets.set_secret(arxistant_secrets.LLM_API_KEY, api_key)
                except arxistant_secrets.SecretStoreError:
                    pass
            if data.get("delete_api_key"):
                config.pop("api_key", None)
                try:
                    arxistant_secrets.delete_secret(arxistant_secrets.LLM_API_KEY)
                except Exception:
                    pass
            save_chat_config(config)
            self._send_json({
                "success": True,
                "base_url": config["base_url"],
                "model": config["model"],
                "temperature": config["temperature"],
                "has_api_key": bool(get_chat_api_key()),
                "key_storage": chat_key_storage(),
            })

        elif path == "/api/chat":
            messages = data.get("messages") or []
            if not isinstance(messages, list) or not messages:
                self._send_json({"success": False, "error": "No messages provided"}, 400)
                return
            config = load_chat_config()
            base_url = config.get("base_url", "").strip()
            model = config.get("model", "").strip()
            if not base_url or not model:
                self._send_json({
                    "success": False,
                    "error": ("The LLM is not configured yet. Open Chat → "
                              "LLM Settings and save a base URL, model, and API key."),
                }, 400)
                return
            temperature = data.get("temperature", config.get("temperature", 0.7))
            api_key = get_chat_api_key()
            self._start_sse()
            try:
                self._run_streaming_agent(
                    [dict(m) for m in messages], base_url, model, temperature, api_key)
            except (BrokenPipeError, ConnectionResetError):
                pass  # The browser closed the chat.
            except Exception as exc:
                try:
                    self._sse_write({"error": str(exc)})
                    self.wfile.write(b"data: [DONE]\n\n")
                    self.wfile.flush()
                except Exception:
                    pass
            return

        elif path == "/api/shutdown":
            self._send_json({"success": True, "message": "Server stopping"})
            # shutdown() must run on a separate thread: it waits for
            # serve_forever() to return, which cannot happen from inside this
            # request handler.
            threading.Thread(
                target=self.server.shutdown, daemon=True, name="arxistant-shutdown"
            ).start()

        else:
            self._send_text("Not found", 404)


CLOUD_SYNC_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Cloud Sync — ArXistant</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 16px; line-height: 1.5; color: #333; }
    h1 { color: #1a1a1a; border-bottom: 2px solid #b31b1b; padding-bottom: 8px; font-size: 1.3em; }
    .nav { margin-bottom: 16px; display: flex; gap: 12px; flex-wrap: wrap; }
    .nav a { color: #b31b1b; text-decoration: none; font-size: 0.9em; }
    label { display: block; font-size: 0.85em; color: #555; margin: 12px 0 4px; }
    input, select { width: 100%; padding: 10px; border: 1px solid #ddd; border-radius: 6px; font-size: 1em; box-sizing: border-box; }
    .row { display: flex; gap: 8px; margin-top: 16px; flex-wrap: wrap; }
    button { padding: 10px 16px; border: none; border-radius: 6px; font-size: 0.95em; cursor: pointer; background: #f0f0f0; color: #333; }
    button.primary { background: #b31b1b; color: white; }
    .hint { font-size: 0.8em; color: #888; margin-top: 4px; }
    #status { background: #f8f9fa; border-radius: 6px; padding: 10px; font-size: 0.8em; white-space: pre-wrap; margin-top: 16px; color: #555; min-height: 1.2em; }
  </style>
</head>
<body>
  <div class="nav">
    <a href="/daily.html">← Daily Papers</a>
    <a href="/database.html">📂 Saved Papers</a>
  </div>
  <h1>☁️ Cloud Sync</h1>

  <label for="provider">Provider</label>
  <select id="provider">
    <option value="webdav">Nutstore WebDAV (坚果云)</option>
    <option value="local_folder">Local folder</option>
  </select>

  <div id="webdav-group">
    <label for="webdav-url">WebDAV address</label>
    <input type="text" id="webdav-url" placeholder="https://dav.jianguoyun.com/dav/">
    <label for="webdav-username">Email</label>
    <input type="text" id="webdav-username" placeholder="you@example.com">
    <label for="webdav-password">App password (第三方应用密码)</label>
    <input type="password" id="webdav-password" placeholder="App password">
    <div class="hint">Generate a dedicated app password in Nutstore security settings. It is stored in the secure keystore, never on disk.</div>
  </div>

  <div id="local-group" style="display:none;">
    <label for="local-path">Sync folder path</label>
    <input type="text" id="local-path" placeholder="/path/to/folder">
    <div class="hint">Any folder, e.g. one synced by Dropbox/iCloud/OneDrive.</div>
  </div>

  <div class="row">
    <button class="primary" id="btn-connect" onclick="connect()">🔗 Connect</button>
    <button id="btn-disconnect" onclick="disconnect()">Disconnect</button>
  </div>

  <div id="status">Loading…</div>

  <script>
    function $(id) { return document.getElementById(id); }

    function showProvider() {
      var p = $('provider').value;
      $('webdav-group').style.display = (p === 'webdav') ? 'block' : 'none';
      $('local-group').style.display = (p === 'local_folder') ? 'block' : 'none';
    }
    $('provider').addEventListener('change', showProvider);

    async function loadStatus() {
      try {
        var r = await fetch('/api/cloud/status');
        var s = await r.json();
        var c = s.config || {};
        $('provider').value = (s.provider === 'local_folder') ? 'local_folder' : 'webdav';
        $('webdav-url').value = c.webdav_url || '';
        $('webdav-username').value = c.webdav_username || '';
        $('webdav-password').value = '';
        $('webdav-password').placeholder = c.webdav_password_set ? 'Saved (leave blank to keep)' : 'App password';
        $('local-path').value = c.local_folder_path || '';
        showProvider();
        var st = 'Provider: ' + (s.provider || 'none') + (s.enabled ? ' (enabled)' : ' (disabled)');
        st += ' | Last sync: ' + (s.last_sync_at || 'never');
        $('status').textContent = st;
      } catch (e) {
        $('status').textContent = 'Could not load status: ' + e.message;
      }
    }

    async function connect() {
      $('status').textContent = 'Connecting…';
      try {
        var config = {
          provider: $('provider').value,
          enabled: true,
          webdav_url: $('webdav-url').value.trim(),
          webdav_username: $('webdav-username').value.trim(),
          webdav_password: $('webdav-password').value,
          local_folder_path: $('local-path').value.trim()
        };
        var sr = await fetch('/api/cloud/settings', { method: 'POST', headers: {'Content-Type': 'application/json'}, body: JSON.stringify(config) });
        var sd = await sr.json();
        if (!sd.success) throw new Error(sd.error || 'Failed to save');
        var yr = await fetch('/api/cloud/sync', { method: 'POST' });
        var yd = await yr.json();
        if (!yd.success) throw new Error(yd.error || 'Sync failed');
        $('status').textContent = 'Connected and synced ✓';
      } catch (e) {
        $('status').textContent = '✗ ' + e.message;
      }
    }

    async function disconnect() {
      try {
        await fetch('/api/cloud/disconnect', { method: 'POST' });
        $('status').textContent = 'Cloud sync disabled.';
        loadStatus();
      } catch (e) {
        $('status').textContent = '✗ ' + e.message;
      }
    }

    loadStatus();
  </script>
</body>
</html>
"""


ML_TRAIN_FALLBACK_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>ML Features — ArXistant</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; max-width: 600px; margin: 0 auto; padding: 16px; line-height: 1.5; color: #333; }
    h1 { color: #1a1a1a; border-bottom: 2px solid #b31b1b; padding-bottom: 8px; font-size: 1.3em; }
    .nav { margin-bottom: 16px; display: flex; gap: 12px; flex-wrap: wrap; }
    .nav a { color: #b31b1b; text-decoration: none; font-size: 0.9em; }
    .btn { padding: 10px 16px; border: none; border-radius: 6px; font-size: 0.95em; cursor: pointer; background: #b31b1b; color: white; margin-top: 12px; }
    .btn:disabled { background: #ccc; cursor: wait; }
    #status { margin-top: 12px; font-size: 0.9em; color: #555; min-height: 1.2em; }
  </style>
</head>
<body>
  <div class="nav">
    <a href="/daily.html">← Daily Papers</a>
    <a href="/database.html">📂 Saved Papers</a>
    <a href="/cloud-sync.html">☁️ Cloud Sync</a>
  </div>
  <h1>🧠 ML Features</h1>
  <p>No model has been trained yet. The ML model is <strong>not synced</strong> between devices — each device trains its own model from its saved papers.</p>
  <p>First make sure you have saved papers: generate the daily list and save a few, or <a href="/cloud-sync.html">sync from Nutstore</a>.</p>
  <button class="btn" id="train-btn" onclick="train()">🧠 Train Model Now</button>
  <div id="status"></div>
  <script>
    async function train() {
      var btn = document.getElementById('train-btn');
      var status = document.getElementById('status');
      btn.disabled = true;
      btn.textContent = 'Training…';
      status.textContent = 'Starting training…';
      try {
        var r = await fetch('/api/ml-retraining/train', { method: 'POST' });
        var d = await r.json();
        if (!d.success || !d.started) {
          status.textContent = 'Error: ' + (d.error || 'Could not start training');
          btn.disabled = false;
          btn.textContent = '🧠 Train Model Now';
          return;
        }
        for (;;) {
          await new Promise(function (res) { setTimeout(res, 3000); });
          var sr = await fetch('/api/ml-retraining');
          var sd = await sr.json();
          if (sd.training) { status.textContent = 'Training in progress…'; continue; }
          if (sd.last_error) {
            status.textContent = 'Error: ' + sd.last_error;
            btn.disabled = false;
            btn.textContent = '🧠 Train Model Now';
            break;
          }
          status.textContent = 'Done! Reloading…';
          setTimeout(function () { location.reload(); }, 1000);
          break;
        }
      } catch (e) {
        status.textContent = 'Error: ' + e.message;
        btn.disabled = false;
        btn.textContent = '🧠 Train Model Now';
      }
    }
  </script>
</body>
</html>
"""


MOBILE_MENU_SCRIPT = """<!-- arxistant-mobile-menu -->
<style>
  .arx-menu-btn {
    position: fixed; top: 12px; right: 12px; z-index: 10000;
    width: 46px; height: 30px; border-radius: 15px;
    background: rgba(200, 200, 200, 0.5);
    border: 1px solid rgba(255, 255, 255, 0.5);
    display: flex; align-items: center; justify-content: center; gap: 5px;
    cursor: pointer; -webkit-tap-highlight-color: transparent;
    box-shadow: 0 1px 4px rgba(0,0,0,0.15);
  }
  .arx-menu-btn span { width: 4px; height: 4px; border-radius: 50%; background: #444; }
  .arx-menu-panel {
    position: fixed; top: 50px; right: 12px; z-index: 9999;
    background: #fff; border: 1px solid #e0e0e0; border-radius: 12px;
    box-shadow: 0 6px 20px rgba(0,0,0,0.18);
    padding: 6px; display: none; min-width: 176px; max-height: 72vh; overflow-y: auto;
  }
  .arx-menu-panel.open { display: block; }
  .arx-menu-item {
    display: flex; align-items: center; gap: 10px;
    padding: 11px 12px; border-radius: 8px; color: #333;
    font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif;
    font-size: 15px; white-space: nowrap; cursor: pointer;
    -webkit-user-select: none; -webkit-touch-callout: none;
  }
  .arx-menu-item:active { background: #f0f0f0; }
  .arx-menu-item:hover { background: #f0f0f0; }
  .arx-menu-item .icon { font-size: 18px; }
  .arx-tooltip {
    position: fixed; z-index: 10001; display: none;
    background: rgba(0, 0, 0, 0.85); color: #fff;
    padding: 8px 12px; border-radius: 8px; font-size: 13px;
    pointer-events: none; max-width: 240px; line-height: 1.4;
  }
</style>
<script>
(function () {
    var IS_MOBILE = /Android|WebView/i.test(navigator.userAgent);

    // Navigation is consolidated into this "..." menu; hide the legacy
    // icon/link bars. On desktop the top .nav-bar keeps the refresh button,
    // so it stays visible there (on mobile it is hidden and refresh happens
    // via pull-down).
    var hideSelectors = IS_MOBILE
        ? '.nav, .nav-bar, .nav-bar-bottom, .quick-links'
        : '.nav, .nav-bar a, .nav-bar-bottom, .quick-links';
    var toHide = document.querySelectorAll(hideSelectors);
    for (var i = 0; i < toHide.length; i++) toHide[i].style.display = 'none';

    var onRecent = (window.location.pathname === '/recent.html');
    var toggleItem = onRecent
        ? { icon: '📅', label: 'Daily Papers', href: '/daily.html', desc: 'Ranked arXiv submissions for today' }
        : { icon: '📆', label: 'Recent Papers', href: '/recent.html', desc: 'Ranked papers from the last five days' };

    var ITEMS = [
        toggleItem,
        { icon: '📂', label: 'Saved Papers', href: '/database.html', desc: 'Search, annotate, and remove saved papers' },
        { icon: '🔍', label: 'Search arXiv', href: '/search-arxiv.html', desc: 'Find papers and save them' },
        { icon: '💬', label: 'Chat', href: '/chat.html', desc: 'Read and discuss your papers with an LLM' },
        { icon: '📚', label: 'My Publications', href: '/publications.html', desc: 'Import and manage your publications' },
        { icon: '🧠', label: 'ML Features', href: '/ml-features.html', desc: 'Inspect training and ranking features' },
        { icon: '☁️', label: 'Cloud Sync', href: '/cloud-sync.html', desc: 'Sync your library across devices via Nutstore' }
    ];

    var btn = document.createElement('div');
    btn.className = 'arx-menu-btn';
    btn.innerHTML = '<span></span><span></span><span></span>';
    document.body.appendChild(btn);

    var panel = document.createElement('div');
    panel.className = 'arx-menu-panel';
    for (var j = 0; j < ITEMS.length; j++) {
        (function (item) {
            var div = document.createElement('div');
            div.className = 'arx-menu-item';
            div.innerHTML = '<span class="icon">' + item.icon + '</span><span>' + item.label + '</span>';
            var longPress = false;
            var timer = null;
            div.addEventListener('touchstart', function (e) {
                longPress = false;
                var t = e.touches[0];
                timer = setTimeout(function () {
                    longPress = true;
                    showTooltip(item.label + ' — ' + item.desc, t.clientX, t.clientY);
                }, 500);
            });
            div.addEventListener('touchmove', function () { clearTimeout(timer); hideTooltip(); });
            div.addEventListener('touchend', function () { clearTimeout(timer); hideTooltip(); });
            div.addEventListener('click', function (e) {
                e.stopPropagation();
                if (!longPress) window.location.href = item.href;
                longPress = false;
            });
            panel.appendChild(div);
        })(ITEMS[j]);
    }
    document.body.appendChild(panel);

    var tip = document.createElement('div');
    tip.className = 'arx-tooltip';
    document.body.appendChild(tip);

    function showTooltip(text, x, y) {
        tip.textContent = text;
        tip.style.display = 'block';
        var left = Math.max(8, Math.min(x - 100, window.innerWidth - 250));
        var top = Math.max(8, y - 56);
        tip.style.left = left + 'px';
        tip.style.top = top + 'px';
    }
    function hideTooltip() { tip.style.display = 'none'; }

    btn.addEventListener('click', function (e) {
        e.stopPropagation();
        panel.classList.toggle('open');
    });

    document.addEventListener('click', function (e) {
        if (e.target !== btn && !panel.contains(e.target)) panel.classList.remove('open');
    });
    document.addEventListener('touchstart', function (e) {
        if (e.target !== btn && !panel.contains(e.target)) panel.classList.remove('open');
    }, { passive: true });
})();
</script>
"""


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
    <a href="/cloud-sync.html">☁️ Cloud Sync</a>
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
        const arxivLink = paper.querySelector('.arxiv-id a');
        const titleLink = paper.querySelector('h2 > a');
        const arxivId = arxivLink ? arxivLink.href.split('/abs/')[1] : '';
        const title = titleLink ? titleLink.textContent : '';
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
<script>
// ── Tag editor (issue #3): create/assign/remove tags on saved papers ──
(function () {
    const ARX = '';
    const savedTags = {};   // arxivId -> [tags] as stored on the server
    let openEditor = null;  // { arxivId, el }

    if (!document.getElementById('arx-tag-styles')) {
        const style = document.createElement('style');
        style.id = 'arx-tag-styles';
        style.textContent = `
            .tag-btn { margin-top:6px; margin-left:6px; padding:1px 6px; color:white; border:none; border-radius:3px; cursor:pointer; font-size:0.7em; white-space:nowrap; background:#00796b; }
            .tag-btn:hover { opacity: 0.9; }
            .tag-editor { margin-top:8px; padding:10px 12px; background:#fff; border:1px solid #d7d7d7; border-radius:6px; }
            .tag-editor-title { font-size:0.8em; font-weight:bold; color:#00695c; margin-bottom:6px; }
            .tag-chips { display:flex; flex-wrap:wrap; gap:6px; margin-bottom:8px; }
            .tag-chips:empty::after { content:'No tags yet.'; color:#999; font-size:0.75em; }
            .tag-chip { display:inline-flex; align-items:center; gap:5px; background:#e0f2f1; color:#00695c; border:1px solid #b2dfdb; border-radius:10px; padding:1px 8px; font-size:0.75em; }
            .tag-chip button { background:none; border:none; color:#00695c; cursor:pointer; padding:0; font-size:1em; line-height:1; }
            .tag-chip button:hover { color:#c62828; }
            .tag-input-row { display:flex; gap:6px; margin-bottom:8px; }
            .tag-input-row input { flex:1; min-width:0; padding:4px 8px; border:1px solid #ccc; border-radius:4px; font-size:0.8em; }
            .tag-input-row button { padding:4px 10px; background:#00796b; color:white; border:none; border-radius:4px; cursor:pointer; font-size:0.75em; }
            .tag-editor-actions { display:flex; gap:6px; }
            .tag-editor-actions .tag-save { padding:4px 10px; background:#b31b1b; color:white; border:none; border-radius:4px; cursor:pointer; font-size:0.75em; }
            .tag-editor-actions .tag-save:disabled { background:#ccc; cursor:wait; }
            .tag-editor-actions .tag-close { padding:4px 10px; background:#eee; color:#555; border:none; border-radius:4px; cursor:pointer; font-size:0.75em; }
        `;
        document.head.appendChild(style);
    }

    function parseTags(value) {
        return String(value || '').split(',').map(t => t.trim()).filter(Boolean);
    }

    function paperMeta(paper) {
        const arxivLink = paper.querySelector('.arxiv-id a');
        const titleLink = paper.querySelector('h2 > a');
        const authorsEl = paper.querySelector('.authors');
        const abstractEl = paper.querySelector('.abstract-full');
        const scoreEl = paper.querySelector('.score');
        const scoreText = scoreEl ? scoreEl.textContent.replace('Relevance: ', '') : '0';
        const h1 = document.querySelector('h1');
        return {
            arxivId: arxivLink ? arxivLink.href.split('/abs/')[1] : '',
            title: titleLink ? titleLink.textContent : '',
            authors: authorsEl ? authorsEl.textContent.replace('Authors:', '').trim() : '',
            abstract: abstractEl ? abstractEl.textContent : '',
            score: parseInt(scoreText) || 0,
            dateFetched: h1 ? h1.textContent : ''
        };
    }

    function updateTagButton(arxivId) {
        const btn = document.getElementById('tag-btn-' + arxivId.replace(/\\./g, '_'));
        if (btn) {
            const n = (savedTags[arxivId] || []).length;
            btn.textContent = n ? '🏷️ ' + n : '🏷️';
            btn.title = 'Edit tags';
        }
    }

    async function ensureSaved(meta) {
        const saveBtn = document.getElementById('save-btn-' + meta.arxivId);
        if (saveBtn && saveBtn.dataset.saved === 'true') return true;
        const resp = await fetch(ARX + '/api/save', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({
                arxiv_id: meta.arxivId, title: meta.title, authors: meta.authors,
                abstract: meta.abstract, relevance_score: meta.score,
                date_fetched: meta.dateFetched
            })
        });
        const data = await resp.json();
        if (data.success && saveBtn) {
            saveBtn.textContent = '✓';
            saveBtn.style.background = '#2e7d32';
            saveBtn.dataset.saved = 'true';
        }
        return data.success;
    }

    function closeEditor() {
        if (openEditor) { openEditor.el.remove(); openEditor = null; }
    }

    function renderChips(container, working) {
        container.innerHTML = '';
        working.forEach((tag, idx) => {
            const chip = document.createElement('span');
            chip.className = 'tag-chip';
            const label = document.createElement('span');
            label.textContent = tag;
            const x = document.createElement('button');
            x.textContent = '✕';
            x.title = 'Remove tag';
            x.onclick = () => { working.splice(idx, 1); renderChips(container, working); };
            chip.appendChild(label);
            chip.appendChild(x);
            container.appendChild(chip);
        });
    }

    function openTagEditor(paper, meta) {
        if (openEditor && openEditor.arxivId === meta.arxivId) { closeEditor(); return; }
        closeEditor();

        const editor = document.createElement('div');
        editor.className = 'tag-editor';
        editor.innerHTML =
            '<div class="tag-editor-title">🏷️ Tags for this paper</div>' +
            '<div class="tag-chips"></div>' +
            '<div class="tag-input-row"><input type="text" maxlength="60" placeholder="Add a tag (e.g. JWST, black holes)…"><button class="tag-add">Add</button></div>' +
            '<div class="tag-editor-actions"><button class="tag-save">💾 Save tags</button><button class="tag-close">Close</button></div>';

        const working = (savedTags[meta.arxivId] || []).slice();
        const chips = editor.querySelector('.tag-chips');
        const input = editor.querySelector('input');
        renderChips(chips, working);

        const addFromInput = () => {
            const tag = input.value.trim();
            if (!tag) return;
            if (!working.some(t => t.toLowerCase() === tag.toLowerCase())) working.push(tag);
            input.value = '';
            renderChips(chips, working);
        };
        editor.querySelector('.tag-add').onclick = addFromInput;
        input.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); addFromInput(); } };

        editor.querySelector('.tag-close').onclick = closeEditor;
        editor.querySelector('.tag-save').onclick = async () => {
            const saveEl = editor.querySelector('.tag-save');
            saveEl.disabled = true;
            try {
                const resp = await fetch(ARX + '/api/update_tags', {
                    method: 'POST',
                    headers: {'Content-Type': 'application/json'},
                    body: JSON.stringify({ arxiv_id: meta.arxivId, tags: working })
                });
                const data = await resp.json();
                if (data.success) {
                    savedTags[meta.arxivId] = data.tags || [];
                    updateTagButton(meta.arxivId);
                    closeEditor();
                } else {
                    alert('Could not save tags: ' + (data.error || 'unknown error'));
                }
            } catch (e) {
                alert('Could not save tags: ' + e.message);
            } finally {
                saveEl.disabled = false;
            }
        };

        openEditor = { arxivId: meta.arxivId, el: editor };
        paper.appendChild(editor);
    }

    document.addEventListener('DOMContentLoaded', async () => {
        try {
            const resp = await fetch(ARX + '/api/papers');
            const data = await resp.json();
            (data.papers || []).forEach(p => { savedTags[p.arxiv_id] = parseTags(p.tags); });
        } catch (e) {
            console.warn('Could not fetch saved-paper tags:', e);
        }

        document.querySelectorAll('.paper').forEach((paper) => {
            const meta = paperMeta(paper);
            if (!meta.arxivId) return;
            const btn = document.createElement('button');
            btn.id = 'tag-btn-' + meta.arxivId.replace(/\\./g, '_');
            btn.className = 'tag-btn';
            const n = (savedTags[meta.arxivId] || []).length;
            btn.textContent = n ? '🏷️ ' + n : '🏷️';
            btn.title = 'Edit tags';
            btn.onclick = async () => {
                let saved;
                try { saved = await ensureSaved(meta); } catch (e) { saved = false; }
                if (!saved) { alert('Could not save the paper; tags require a saved paper.'); return; }
                if (!savedTags[meta.arxivId]) savedTags[meta.arxivId] = [];
                openTagEditor(paper, meta);
            };
            // The save button is appended asynchronously; place the tag button
            // right after it once it exists.
            const place = (tries) => {
                const saveBtn = document.getElementById('save-btn-' + meta.arxivId);
                if (saveBtn) {
                    saveBtn.insertAdjacentElement('afterend', btn);
                } else if (tries > 0) {
                    setTimeout(() => place(tries - 1), 150);
                } else {
                    paper.appendChild(btn);
                }
            };
            place(20);
        });
    });
})();
</script>
"""

CHAT_LINK_SCRIPT = """<!-- chat-link-embedded -->
<script>
document.addEventListener('DOMContentLoaded', () => {
    document.querySelectorAll('.paper').forEach((paper) => {
        if (paper.querySelector('.chat-link-btn')) return;
        const arxivLink = paper.querySelector('.arxiv-id a');
        if (!arxivLink) return;
        const arxivId = arxivLink.href.split('/abs/')[1];
        if (!arxivId) return;
        const link = document.createElement('a');
        link.href = '/chat.html?paper=' + encodeURIComponent(arxivId);
        link.className = 'chat-link-btn';
        link.title = 'Read this paper with the Chat helper';
        link.textContent = '💬';
        link.style.cssText = 'margin-top:6px;margin-left:14px;padding:1px 6px;color:white;border:none;border-radius:3px;font-size:0.7em;white-space:nowrap;background:#555;text-decoration:none;display:inline-block;';
        // The save button is appended asynchronously (it awaits a fetch), so
        // wait for it and insert the chat button right after it; fall back to
        // appending to the paper if the save button never appears.
        const place = (tries) => {
            const saveBtn = document.getElementById('save-btn-' + arxivId);
            if (saveBtn) {
                saveBtn.insertAdjacentElement('afterend', link);
            } else if (tries > 0) {
                setTimeout(() => place(tries - 1), 150);
            } else {
                paper.appendChild(link);
            }
        };
        place(20);
    });
});
</script>
"""

CHAT_PAGE_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>Chat — ArXistant</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; max-width: 1440px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #333; }
    h1 { color: #1a1a1a; border-bottom: 2px solid #b31b1b; padding-bottom: 10px; margin-bottom: 14px; }
    .nav { margin-bottom: 16px; display: flex; gap: 16px; flex-wrap: wrap; }
    .nav a { color: #b31b1b; text-decoration: none; font-weight: bold; }
    .nav a:hover { text-decoration: underline; }

    .layout { display: flex; align-items: stretch; position: relative; height: calc(100vh - 175px); min-height: 540px; --chat-w: 400px; }
    .pane { transition: width 0.25s ease, margin 0.25s ease, opacity 0.2s ease; overflow: hidden; }
    .pane-left { width: 300px; flex-shrink: 0; margin-right: 16px; }
    .pane-left-inner { width: 300px; height: 100%; overflow-y: auto; box-sizing: border-box; }
    .pane-center { flex: 1; min-width: 0; display: flex; flex-direction: column; }
    .pane-right { width: var(--chat-w); flex-shrink: 0; }
    .pane-right-inner { width: 100%; height: 100%; display: flex; flex-direction: column; box-sizing: border-box; }
    .layout.left-hidden .pane-left { width: 0; margin-right: 0; opacity: 0; pointer-events: none; }
    .layout.right-hidden .pane-right { width: 0; margin-left: 0; opacity: 0; pointer-events: none; }

    .picker-view { max-width: 760px; width: 100%; margin: 0 auto; }
    .picker-view .paper-list { max-height: calc(100vh - 395px); min-height: 280px; }
    .reader-view { flex: 1; min-height: 0; display: flex; }
    .reader-main { flex: 1; min-width: 0; display: flex; flex-direction: column; }
    .reader-tools { display: flex; align-items: center; gap: 6px; margin-bottom: 6px; flex-shrink: 0; }
    .view-btn { border: 1px solid #ddd; background: #f5f5f5; color: #555; border-radius: 6px; padding: 3px 14px; font-size: 0.8em; font-weight: bold; cursor: pointer; }
    .view-btn.active { background: #b31b1b; border-color: #b31b1b; color: #fff; }
    .view-hint { font-size: 0.75em; color: #888; margin-left: 6px; }

    .edge-handle { position: absolute; top: 50%; transform: translateY(-50%); z-index: 30; background: #b31b1b; color: white; border: none; border-radius: 6px; padding: 16px 8px; cursor: pointer; font-size: 0.95em; line-height: 1; box-shadow: 0 2px 6px rgba(0,0,0,0.25); transition: left 0.25s ease, right 0.25s ease, background 0.15s ease; }
    .edge-handle:hover { background: #8a1515; }
    .edge-handle.left { left: 2px; }
    .layout:not(.left-hidden) .edge-handle.left { left: 300px; }
    .edge-handle.right { right: 2px; }
    .layout:not(.right-hidden) .edge-handle.right { right: calc(var(--chat-w) + 14px); }
    .resizer-right { flex-shrink: 0; width: 14px; cursor: col-resize; position: relative; }
    .resizer-right::after { content: ''; position: absolute; top: 0; bottom: 0; left: 6px; width: 2px; background: #e0e0e0; border-radius: 1px; transition: background 0.15s ease; }
    .resizer-right:hover::after, .resizer-right.active::after { background: #b31b1b; }
    .layout.right-hidden .resizer-right { display: none; }
    .layout.resizing .pane-right, .layout.resizing .resizer-right, .layout.resizing .edge-handle { transition: none; }
    .edge-handle::after { content: attr(data-tip); position: absolute; top: 50%; left: 50%; transform: translate(-50%, 28px); background: rgba(0,0,0,0.82); color: #fff; padding: 3px 10px; border-radius: 6px; font-size: 11px; white-space: nowrap; opacity: 0; pointer-events: none; transition: opacity 0.15s ease; z-index: 40; }
    .edge-handle:hover::after { opacity: 1; }
    .hidden { display: none !important; }

    .panel { border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; margin-bottom: 16px; background: #fafafa; }
    .panel h3 { margin-top: 0; color: #b31b1b; font-size: 1em; }
    .panel input, .panel select { width: 100%; padding: 8px; margin-bottom: 8px; border: 1px solid #ddd; border-radius: 4px; box-sizing: border-box; font-size: 0.9em; font-family: inherit; background: white; }
    .panel input:focus, .panel select:focus { outline: none; border-color: #b31b1b; }
    .panel button { background: #b31b1b; color: white; border: none; padding: 6px 14px; border-radius: 4px; cursor: pointer; font-size: 0.9em; }
    .panel button:hover { background: #8a1515; }
    .hint { font-size: 0.8em; color: #888; margin: 6px 0 0; }
    .hint.ok { color: #2e7d32; }
    .hint.warn { color: #b26a00; }

    .paper-list { overflow-y: auto; }
    .plist-item { border: 1px solid #ddd; border-radius: 4px; padding: 8px; margin-bottom: 6px; background: white; cursor: pointer; font-size: 0.85em; }
    .plist-item:hover { border-color: #b31b1b; }
    .plist-item.active { border-color: #b31b1b; background: #fdf3f3; }
    .plist-item .title { font-weight: bold; color: #333; margin-bottom: 2px; }
    .plist-item .meta { color: #888; font-size: 0.85em; }
    .badge { display: inline-block; font-size: 0.7em; font-weight: bold; color: white; background: #777; border-radius: 8px; padding: 1px 7px; margin-right: 4px; vertical-align: middle; }
    .badge.saved { background: #2e7d32; }
    .badge.daily { background: #b31b1b; }
    .badge.recent { background: #1976d2; }
    .badge.arxiv { background: #555; }

    .pdf-wrap { position: relative; flex: 1; min-width: 0; border: 1px solid #e0e0e0; border-radius: 8px; overflow: hidden; background: #525659; }
    .pdf-wrap iframe { position: absolute; top: 0; left: 0; width: 100%; height: 100%; border: none; }
    .pdf-status { position: absolute; top: 12px; left: 50%; transform: translateX(-50%); z-index: 5; background: rgba(0,0,0,0.72); color: white; padding: 5px 16px; border-radius: 14px; font-size: 0.85em; max-width: 92%; text-align: center; }
    .pdf-status.error { background: #c62828; white-space: normal; }
    .pdf-status a { color: #ffe082; }
    .text-wrap { background: #fff; }
    .text-wrap iframe { background: #fff; }
    .paper-results { margin: 0 0 12px; padding: 10px 12px; border: 1px solid #d8c9c9; border-radius: 8px; background: #fff; }
    .paper-results .pr-head { font-size: 0.78em; font-weight: bold; color: #7a1010; margin-bottom: 6px; }
    .paper-results .pr-item { padding: 6px 0; border-top: 1px solid #eee; font-size: 0.83em; }
    .paper-results .pr-item:first-of-type { border-top: none; }
    .paper-results .pr-title { font-weight: bold; color: #333; }
    .paper-results .pr-meta { color: #888; font-size: 0.9em; }
    .paper-results button { margin-top: 2px; }
    .paper-results a { color: #b31b1b; margin-left: 6px; font-size: 0.9em; }
    .sel-bubble { position: absolute; z-index: 30; background: #b31b1b; color: #fff; border: none; border-radius: 14px; padding: 0; font-size: 0.78em; font-weight: bold; cursor: pointer; box-shadow: 0 2px 8px rgba(0,0,0,0.3); transform: translateX(-50%); display: flex; overflow: hidden; }
    .sel-bubble button { background: transparent; color: #fff; border: none; padding: 5px 12px; font-size: 1em; font-weight: bold; cursor: pointer; white-space: nowrap; }
    .sel-bubble button:hover { background: #8a1515; }
    .sel-bubble button + button { border-left: 1px solid rgba(255,255,255,0.35); }
    mark.user-hl { background: #9be7ff; cursor: pointer; }
    .notes-box { flex-shrink: 0; border: 1px solid #e0e0e0; border-left: 4px solid #00796b; border-radius: 8px; background: #f4fbf9; padding: 8px 12px; margin-bottom: 12px; }
    .notes-box summary { cursor: pointer; font-size: 0.85em; font-weight: bold; color: #00695c; }
    .notes-box .notes-status { font-weight: normal; font-size: 0.85em; color: #666; margin-left: 8px; }
    .notes-box textarea { width: 100%; min-height: 70px; margin-top: 8px; padding: 8px; border: 1px solid #cde8e2; border-radius: 4px; font-family: inherit; font-size: 0.85em; box-sizing: border-box; }
    .notes-box button { margin-top: 6px; padding: 4px 12px; background: #00796b; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 0.8em; }

    .paper-info { flex-shrink: 0; max-height: 45%; overflow-y: auto; border: 1px solid #e0e0e0; border-left: 4px solid #b31b1b; border-radius: 8px; background: #fdf6f6; padding: 9px 30px 9px 12px; margin-bottom: 12px; position: relative; box-sizing: border-box; }
    .paper-info .unpin { position: absolute; right: 8px; top: 6px; cursor: pointer; color: #c62828; font-weight: bold; font-size: 1.2em; }
    .info-line { display: flex; align-items: center; gap: 12px; flex-wrap: wrap; font-size: 0.85em; color: #666; }
    .info-line a { color: #b31b1b; text-decoration: none; font-weight: bold; }
    .info-line a:hover { text-decoration: underline; }
    .info-line .pid { font-family: monospace; color: #333; }
    .save-toggle { border: none; border-radius: 10px; padding: 2px 12px; font-size: 0.8em; font-weight: bold; cursor: pointer; background: #b31b1b; color: #fff; }
    .save-toggle:hover { background: #8a1515; }
    .save-toggle.saved { background: #2e7d32; }
    .save-toggle.saved:hover { background: #1b5e20; }

    .chat-header { display: flex; justify-content: space-between; align-items: center; margin-bottom: 10px; flex-shrink: 0; }
    .chat-header h2 { margin: 0; font-size: 1.1em; color: #555; }
    .chat-header button { background: #666; color: white; border: none; padding: 4px 12px; border-radius: 4px; cursor: pointer; font-size: 0.85em; }
    .chat-header button:hover { background: #555; }

    .chat-messages { flex: 1; min-height: 200px; overflow-y: auto; border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; background: #fafafa; margin-bottom: 10px; }
    .message { margin-bottom: 12px; padding: 10px 14px; border-radius: 8px; max-width: 95%; word-wrap: break-word; }
    .message.user { background: #b31b1b; color: white; margin-left: auto; }
    .message.assistant { background: white; border: 1px solid #ddd; margin-right: auto; }
    .message.system { background: #fff8e1; border: 1px solid #f0e0a0; font-size: 0.85em; color: #6d5a00; margin: 0 auto 12px; max-width: 95%; white-space: pre-wrap; }
    .message .label { font-size: 0.75em; font-weight: bold; margin-bottom: 4px; opacity: 0.7; }
    .message pre { background: #f5f5f5; padding: 8px; border-radius: 4px; overflow-x: auto; font-size: 0.85em; margin: 8px 0; }
    .message code { background: #f5f5f5; padding: 2px 4px; border-radius: 3px; font-size: 0.9em; }
    .message p { margin: 0 0 8px; }
    .message p:last-child { margin-bottom: 0; }
    .message ul, .message ol { margin: 8px 0; padding-left: 20px; }
    .message li { margin-bottom: 4px; }
    .thinking { color: #999; font-style: italic; }

    .quick-prompts { display: flex; gap: 6px; flex-wrap: wrap; margin-bottom: 10px; flex-shrink: 0; }
    .sel-chip { display: flex; align-items: center; gap: 8px; background: #fdf3f3; border: 1px solid #b31b1b; color: #7a1010; border-radius: 6px; padding: 4px 10px; font-size: 0.78em; margin-bottom: 8px; flex-shrink: 0; cursor: pointer; max-width: 100%; }
    .sel-chip .sel-x { font-weight: bold; color: #c62828; }
    .sel-chip .sel-t { overflow: hidden; text-overflow: ellipsis; white-space: nowrap; }
    .quick-chip { font-size: 0.8em; padding: 4px 10px; background: #f0f0f0; border: 1px solid #ddd; border-radius: 14px; cursor: pointer; color: #444; }
    .quick-chip:hover { border-color: #b31b1b; color: #b31b1b; }

    .chat-input { display: flex; gap: 8px; flex-shrink: 0; }
    .chat-input textarea { flex: 1; padding: 10px; border: 1px solid #ddd; border-radius: 6px; font-family: inherit; resize: none; min-height: 58px; font-size: 0.95em; }
    .chat-input textarea:focus { outline: none; border-color: #b31b1b; }
    .chat-input button { padding: 10px 20px; background: #b31b1b; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 1em; }
    .chat-input button:hover { background: #8a1515; }
    .chat-input button:disabled { background: #ccc; cursor: not-allowed; }

    .empty-chat { text-align: center; color: #888; padding: 30px 16px; font-style: italic; }

    @media (max-width: 1000px) {
      .layout { flex-direction: column; height: auto; gap: 12px; }
      .pane { width: 100%; margin: 0; overflow: visible; }
      .pane-left-inner, .pane-right-inner { width: 100%; height: auto; overflow: visible; }
      .layout.left-hidden .pane-left, .layout.right-hidden .pane-right { width: 100%; opacity: 1; pointer-events: auto; }
      .reader-view { flex-direction: column; }
      .paper-info { width: 100%; }
      .picker-view { max-width: none; }
      .picker-view .paper-list { max-height: 320px; min-height: 0; }
      .pdf-wrap { height: 70vh; flex: none; }
      .chat-messages { height: 380px; flex: none; }
      .edge-handle { display: none; }
      .resizer-right { display: none; }
    }
  </style>
</head>
<body>
  <div class="nav">
    <a href="/daily.html">← Daily Papers</a>
    <a href="/recent.html">📅 Recent Papers</a>
    <a href="/database.html">📂 Saved Papers</a>
    <a href="/search-arxiv.html">🔍 Search arXiv</a>
    <a href="/publications.html">📚 My Publications</a>
    <a href="/ml-features.html">🧠 ML Features</a>
    <a href="/cloud-sync.html">☁️ Cloud Sync</a>
  </div>
  <h1>💬 Paper Reading Helper</h1>

  <div class="layout" id="layout">
    <aside class="pane pane-left">
      <div class="pane-left-inner">
        <div class="panel">
          <h3>🔧 LLM Settings</h3>
          <select id="preset" onchange="applyPreset(this.value)">
            <option value="custom">Custom / keep my values</option>
          </select>
          <input type="text" id="baseUrl" placeholder="Base URL (e.g. https://api.openai.com/v1)">
          <input type="text" id="modelName" placeholder="Model (e.g. gpt-4o-mini, deepseek-chat)">
          <input type="password" id="apiKey" placeholder="API key">
          <button onclick="saveConfig()">Save Settings</button>
          <p class="hint" id="configStatus">Loading…</p>
          <p class="hint">The API key is stored in the OS keychain when available, otherwise in a private local file; base URL and model stay in ArXistant's local data directory. Questions are sent to the provider you configure.</p>
        </div>
      </div>
    </aside>

    <main class="pane pane-center">
      <div id="pickerView" class="picker-view">
        <div class="panel">
          <h3>📄 Choose a Paper</h3>
          <input type="text" id="paperSearch" placeholder="Search title / author / arXiv ID…" oninput="renderPaperList()" style="padding:8px;border:1px solid #ddd;border-radius:4px;font-size:0.9em;">
          <select id="sourceFilter" onchange="renderPaperList()">
            <option value="all">All sources</option>
            <option value="saved">💾 Saved papers</option>
            <option value="daily">📅 Daily list</option>
            <option value="recent">📆 Recent list</option>
          </select>
          <p class="hint" id="paperCount">Loading…</p>
          <div class="paper-list" id="paperList"></div>
          <p class="hint">Daily/recent papers appear after the next list refresh. Selecting a paper opens its PDF here.</p>
        </div>
      </div>

      <div id="readerView" class="reader-view hidden">
        <div class="reader-main">
          <div class="reader-tools">
            <button id="viewPdfBtn" class="view-btn active" onclick="switchView('pdf')">PDF</button>
            <button id="viewTextBtn" class="view-btn" onclick="switchView('text')">Text</button>
            <span class="view-hint" id="viewHint"></span>
          </div>
          <div id="pdfWrap" class="pdf-wrap">
            <div id="pdfStatus" class="pdf-status hidden"></div>
            <iframe id="pdfFrame" title="Paper PDF"></iframe>
          </div>
          <div id="textWrap" class="pdf-wrap text-wrap hidden">
            <iframe id="textFrame" title="Paper full text"></iframe>
            <div id="selBubble" class="sel-bubble hidden">
              <button onclick="attachSelection()">💬 Ask about this</button>
              <button onclick="highlightSelection()">🖍 Highlight</button>
            </div>
          </div>
        </div>
      </div>
    </main>

    <div id="chatResizer" class="resizer-right" title="Drag to resize"></div>
    <aside class="pane pane-right" id="paneRight">
      <div class="pane-right-inner">
        <div class="paper-info hidden" id="paperInfo">
          <span class="unpin" onclick="unpinPaper()" title="Close this paper">×</span>
          <div class="info-line">
            <button id="saveToggle" class="save-toggle" onclick="toggleSavePinned()">💾 Save</button>
            <span class="pid" id="paperIdText"></span>
            <a id="paperAbsLink" href="#" target="_blank" rel="noopener">arXiv ↗</a>
            <a id="paperScixLink" href="#" target="_blank" rel="noopener">SciX ↗</a>
          </div>
        </div>
        <details id="notesBox" class="notes-box hidden">
          <summary>📝 Notes<span class="notes-status" id="notesStatus"></span></summary>
          <textarea id="notesArea" placeholder="Your notes on this paper — stored locally with the saved paper, shared with the Saved Papers page…"></textarea>
          <button onclick="savePinnedNotes()">💾 Save notes</button>
        </details>
        <div class="chat-header">
          <h2>Conversation</h2>
          <button onclick="newChat()">New Chat</button>
        </div>
        <div class="chat-messages" id="chatMessages">
          <div class="empty-chat" id="emptyChat"></div>
        </div>
        <div class="quick-prompts" id="quickPrompts"></div>
        <div id="selChip" class="sel-chip hidden" onclick="clearSelection()" title="Click to remove the selected excerpt">
          <span>📄</span><span class="sel-t" id="selChipText"></span><span class="sel-x">×</span>
        </div>
        <div class="chat-input">
          <textarea id="chatInput" placeholder="Ask about this paper…" onkeydown="if(event.key==='Enter' && !event.shiftKey){event.preventDefault();sendMessage();}"></textarea>
          <button id="sendBtn" onclick="sendMessage()">Send</button>
        </div>
      </div>
    </aside>

    <button id="leftHandle" class="edge-handle left" data-tip="Settings" aria-label="Toggle settings panel" onclick="toggleLeft()">❯</button>
    <button id="rightHandle" class="edge-handle right" data-tip="Chat" aria-label="Toggle chat panel" onclick="toggleRight()">❮</button>
  </div>

  <script>
    const PRESETS = {
      openai:     { label: 'OpenAI',           baseUrl: 'https://api.openai.com/v1',            model: 'gpt-4o-mini' },
      deepseek:   { label: 'DeepSeek',         baseUrl: 'https://api.deepseek.com/v1',          model: 'deepseek-chat' },
      openrouter: { label: 'OpenRouter',       baseUrl: 'https://openrouter.ai/api/v1',         model: 'openai/gpt-4o-mini' },
      moonshot:   { label: 'Moonshot (Kimi)',  baseUrl: 'https://api.moonshot.cn/v1',           model: 'moonshot-v1-8k' },
      zhipu:      { label: 'Zhipu (GLM)',      baseUrl: 'https://open.bigmodel.cn/api/paas/v4', model: 'glm-4-flash' },
      ollama:     { label: 'Local Ollama',     baseUrl: 'http://localhost:11434/v1',            model: 'llama3.1' }
    };
    const SOURCE_LABELS = { saved: '💾 Saved', daily: '📅 Daily', recent: '📆 Recent', arxiv: '🌐 arXiv' };
    const QUICK_PROMPTS = [
      'Summarize this paper in 5 bullet points',
      'What problem does this paper tackle, and what is the key idea?',
      'What are the main results and their caveats?',
      'Explain the method in simple terms',
      'Which parts deserve a careful full read?'
    ];

    let library = [];
    let libraryById = {};
    let lastFiltered = [];
    let pinned = null;
    let chatHistory = [];   // session-only, never persisted
    let streaming = false;
    let configured = false;
    let currentPdfUrl = null;
    let pdfLoadedFor = null;
    let pdfAbort = null;
    let pdfTick = null;
    let savedIds = new Set();
    let currentView = 'pdf';
    let fullText = '';
    let textLoadedFor = null;   // arxiv_id the text iframe currently holds
    let pendingSelection = '';  // last text selected in the text view
    let activeSelection = '';   // excerpt attached to the next question
    let userHighlights = [];    // persisted passage quotes for the pinned paper
    let pendingHighlights = []; // quotes to highlight once the text view is ready

    function $(id) { return document.getElementById(id); }

    function normalizeId(id) { return (id || '').replace(/v\\d+$/, ''); }

    function escapeHtml(text) {
      if (text === null || text === undefined) return '';
      const div = document.createElement('div');
      div.textContent = String(text);
      return div.innerHTML;
    }

    // ---------- Pane layout ----------

    function paneState() {
      const layout = $('layout');
      return {
        left: layout.classList.contains('left-hidden'),
        right: layout.classList.contains('right-hidden')
      };
    }

    function updateToggleUI() {
      const s = paneState();
      $('leftHandle').textContent = s.left ? '❯' : '❮';
      $('rightHandle').textContent = s.right ? '❮' : '❯';
    }

    function savePaneState() {
      try {
        const s = paneState();
        s.chatW = parseInt(getComputedStyle($('layout')).getPropertyValue('--chat-w')) || 400;
        localStorage.setItem('chatPaneStateV2', JSON.stringify(s));
      } catch (e) {}
    }

    function toggleLeft() {
      $('layout').classList.toggle('left-hidden');
      updateToggleUI();
      savePaneState();
    }

    function toggleRight() {
      $('layout').classList.toggle('right-hidden');
      updateToggleUI();
      savePaneState();
    }

    function initPanes() {
      // Default: the settings panel starts hidden; the chat panel starts shown.
      const state = { left: true, right: false };
      try {
        const saved = JSON.parse(localStorage.getItem('chatPaneStateV2') || '{}');
        if (typeof saved.left === 'boolean') state.left = saved.left;
        if (typeof saved.right === 'boolean') state.right = saved.right;
        if (saved.chatW) $('layout').style.setProperty('--chat-w', saved.chatW + 'px');
      } catch (e) {}
      $('layout').classList.toggle('left-hidden', state.left);
      $('layout').classList.toggle('right-hidden', state.right);
      updateToggleUI();
      initChatResize();
    }

    function initChatResize() {
      const layout = $('layout');
      const resizer = $('chatResizer');
      const right = $('paneRight');
      if (!resizer || !right) return;
      let dragging = false, startX = 0, startW = 0;
      resizer.addEventListener('mousedown', e => {
        dragging = true;
        startX = e.clientX;
        startW = right.getBoundingClientRect().width;
        resizer.classList.add('active');
        layout.classList.add('resizing');
        document.body.style.userSelect = 'none';
        document.body.style.cursor = 'col-resize';
        e.preventDefault();
      });
      window.addEventListener('mousemove', e => {
        if (!dragging) return;
        const w = Math.max(280, Math.min(820, startW - (e.clientX - startX)));
        layout.style.setProperty('--chat-w', w + 'px');
      });
      window.addEventListener('mouseup', () => {
        if (!dragging) return;
        dragging = false;
        resizer.classList.remove('active');
        layout.classList.remove('resizing');
        document.body.style.userSelect = '';
        document.body.style.cursor = '';
        savePaneState();
      });
    }

    // ---------- LLM settings ----------

    function buildPresetOptions() {
      const sel = $('preset');
      Object.keys(PRESETS).forEach(k => {
        const opt = document.createElement('option');
        opt.value = k;
        opt.textContent = PRESETS[k].label;
        sel.appendChild(opt);
      });
    }

    function applyPreset(key) {
      const p = PRESETS[key];
      if (!p) return;
      if (p.baseUrl) $('baseUrl').value = p.baseUrl;
      if (p.model) $('modelName').value = p.model;
    }

    function updateConfigStatus(cfg) {
      const el = $('configStatus');
      configured = Boolean(cfg.base_url && cfg.model && cfg.has_api_key);
      const missing = [];
      if (!cfg.base_url) missing.push('base URL');
      if (!cfg.model) missing.push('model');
      if (!cfg.has_api_key) missing.push('API key');
      if (missing.length === 0) {
        el.textContent = '✅ Ready — ' + cfg.model +
          (cfg.key_storage === 'file' ? ' · key in local file' : '');
        el.className = 'hint ok';
      } else {
        el.textContent = '⚠️ Missing: ' + missing.join(', ');
        el.className = 'hint warn';
      }
    }

    async function loadConfig() {
      try {
        const resp = await fetch('/api/chat/config');
        const cfg = await resp.json();
        $('baseUrl').value = cfg.base_url || '';
        $('modelName').value = cfg.model || '';
        const keyInput = $('apiKey');
        keyInput.value = '';
        keyInput.placeholder = cfg.has_api_key ? 'API key saved — leave blank to keep' : 'API key';
        updateConfigStatus(cfg);
      } catch (e) {
        $('configStatus').textContent = '⚠️ Could not load config: ' + e.message;
      }
    }

    async function saveConfig() {
      const payload = {
        base_url: $('baseUrl').value.trim(),
        model: $('modelName').value.trim()
      };
      const key = $('apiKey').value.trim();
      if (key) payload.api_key = key;
      const status = $('configStatus');
      try {
        const resp = await fetch('/api/chat/config', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(payload)
        });
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || 'Failed to save');
        $('apiKey').value = '';
        await loadConfig();
      } catch (e) {
        status.textContent = '⚠️ ' + e.message;
        status.className = 'hint warn';
      }
    }

    // ---------- Paper library ----------

    async function loadLibrary() {
      try {
        const resp = await fetch('/api/chat/library');
        const data = await resp.json();
        library = data.papers || [];
        libraryById = {};
        library.forEach(p => { libraryById[normalizeId(p.arxiv_id)] = p; });
        savedIds = new Set(library.filter(p => p.source === 'saved')
          .map(p => normalizeId(p.arxiv_id)));
        renderPaperList();
      } catch (e) {
        $('paperCount').textContent = 'Could not load papers: ' + e.message;
      }
    }

    function renderPaperList() {
      const q = $('paperSearch').value.trim().toLowerCase();
      const src = $('sourceFilter').value;
      lastFiltered = library.filter(p => {
        if (src !== 'all' && p.source !== src) return false;
        if (!q) return true;
        return ((p.title || '') + ' ' + (p.authors || '') + ' ' + p.arxiv_id).toLowerCase().includes(q);
      });
      $('paperCount').textContent = lastFiltered.length + ' paper' + (lastFiltered.length !== 1 ? 's' : '');
      const container = $('paperList');
      container.innerHTML = '';
      if (lastFiltered.length === 0) {
        container.innerHTML = '<p class="hint">No papers match.</p>';
        return;
      }
      // Build items as real DOM nodes and bind each click via a closure over the
      // exact paper object, so the handler can never reference a stale index.
      lastFiltered.slice(0, 60).forEach(p => {
        const active = pinned && normalizeId(pinned.arxiv_id) === normalizeId(p.arxiv_id);
        const item = document.createElement('div');
        item.className = 'plist-item' + (active ? ' active' : '');
        const title = document.createElement('div');
        title.className = 'title';
        const badge = document.createElement('span');
        badge.className = 'badge ' + (p.source || '');
        badge.textContent = SOURCE_LABELS[p.source] || (p.source || '');
        title.appendChild(badge);
        title.appendChild(document.createTextNode(p.title || ''));
        const meta = document.createElement('div');
        meta.className = 'meta';
        const authors = p.authors || '';
        meta.textContent = authors.substring(0, 80) + (authors.length > 80 ? '…' : '');
        item.appendChild(title);
        item.appendChild(meta);
        item.addEventListener('click', () => pinPaper(p));
        container.appendChild(item);
      });
    }

    // ---------- Paper + PDF ----------

    function pinPaper(p) {
      if (!p || !p.arxiv_id) return;
      pinned = p;
      $('pickerView').classList.add('hidden');
      $('readerView').classList.remove('hidden');
      $('paperInfo').classList.remove('hidden');
      $('paperIdText').textContent = 'arXiv:' + p.arxiv_id;
      $('paperAbsLink').href = 'https://arxiv.org/abs/' + encodeURIComponent(p.arxiv_id);
      $('paperScixLink').href = scixUrlFor(p);
      // Notes + highlights (issue #6): same saved_papers record the Saved
      // Papers page edits.
      userHighlights = Array.isArray(p.highlights) ? p.highlights.slice() : [];
      $('notesBox').classList.remove('hidden');
      $('notesArea').value = p.notes || '';
      $('notesStatus').textContent = '';
      renderSaveToggle();
      resetReaderForNewPaper();
      renderPaperList();
      renderQuickPrompts();
    }

    function scixUrlFor(p) {
      // SciXplorer record pages use ADS-style bibcodes; for arXiv papers the
      // bibcode is <year>arXiv<digits><first-author-surname-initial>, the same
      // convention arXiv itself uses for its ADS links. Fall back to a SciX
      // search when the ID or author list does not fit the pattern.
      const id = (p.arxiv_id || '').trim();
      const authors = (p.authors || '').trim();
      const m = id.match(/^(\\d{2})\\d{2}\\.(\\d{4,5})(v\\d+)?$/);
      if (m && authors) {
        const yy = parseInt(m[1], 10);
        const year = (yy >= 95 ? 1900 : 2000) + yy;
        const digits = id.replace('.', '').replace(/v\\d+$/, '');
        const first = authors.split(',')[0].trim();
        const initial = (first.split(/\\s+/).pop() || '').charAt(0).toUpperCase();
        if (initial >= 'A' && initial <= 'Z') {
          return 'https://scixplorer.org/abs/' + year + 'arXiv' + digits + initial;
        }
      }
      return 'https://scixplorer.org/search?q=' + encodeURIComponent('arXiv:' + id);
    }

    function renderSaveToggle() {
      const btn = $('saveToggle');
      if (!pinned) return;
      const saved = savedIds.has(normalizeId(pinned.arxiv_id));
      btn.textContent = saved ? '✓ Saved' : '💾 Save';
      btn.classList.toggle('saved', saved);
      btn.title = saved ? 'Remove from your saved library' : 'Save to your library';
    }

    async function toggleSavePinned() {
      if (!pinned) return;
      const id = normalizeId(pinned.arxiv_id);
      const wasSaved = savedIds.has(id);
      if (wasSaved && !confirm('Remove this paper from your saved library?')) return;
      try {
        const body = wasSaved ? { arxiv_id: pinned.arxiv_id } : {
          arxiv_id: pinned.arxiv_id,
          title: pinned.title || '',
          authors: pinned.authors || '',
          abstract: pinned.abstract || '',
          relevance_score: pinned.score || 0,
          date_fetched: new Date().toISOString().slice(0, 10)
        };
        const resp = await fetch(wasSaved ? '/api/delete' : '/api/save', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify(body)
        });
        const data = await resp.json();
        if (!data.success) throw new Error(data.error || 'The server rejected the change.');
        if (wasSaved) savedIds.delete(id); else savedIds.add(id);
        renderSaveToggle();
        renderPaperList();
      } catch (e) {
        addSystemMessage('⚠️ ' + e.message);
      }
    }

    async function showPdf(p) {
      const frame = $('pdfFrame');
      const status = $('pdfStatus');
      const id = normalizeId(p.arxiv_id);
      if (pdfLoadedFor === id && currentPdfUrl) {
        frame.src = currentPdfUrl;
        status.classList.add('hidden');
        return;
      }
      if (pdfAbort) pdfAbort.abort();
      if (pdfTick) { clearInterval(pdfTick); pdfTick = null; }
      if (currentPdfUrl) { URL.revokeObjectURL(currentPdfUrl); currentPdfUrl = null; }
      frame.removeAttribute('src');
      status.className = 'pdf-status';
      const arxivUrl = 'https://arxiv.org/pdf/' + encodeURIComponent(p.arxiv_id);
      const started = Date.now();
      const renderStatus = () => {
        const secs = Math.round((Date.now() - started) / 1000);
        status.innerHTML = '⏳ Loading PDF… ' + secs +
          's &nbsp;·&nbsp; large papers can take a while &nbsp;·&nbsp; ' +
          '<a href="' + arxivUrl + '" target="_blank">read on arXiv ↗</a>';
      };
      renderStatus();
      pdfTick = setInterval(renderStatus, 1000);
      const controller = new AbortController();
      pdfAbort = controller;
      const timer = setTimeout(() => controller.abort(), 180000);
      try {
        const resp = await fetch('/api/chat/pdf?arxiv_id=' + encodeURIComponent(p.arxiv_id),
                                 { signal: controller.signal });
        if (!resp.ok) throw new Error('Could not load the PDF (HTTP ' + resp.status + ')');
        const blob = await resp.blob();
        if (!pinned || normalizeId(pinned.arxiv_id) !== normalizeId(p.arxiv_id)) return;
        currentPdfUrl = URL.createObjectURL(blob);
        pdfLoadedFor = id;
        frame.src = currentPdfUrl;
        status.classList.add('hidden');
      } catch (e) {
        if (!pinned || normalizeId(pinned.arxiv_id) !== normalizeId(p.arxiv_id)) return;
        const reason = (e && e.name === 'AbortError')
          ? 'The PDF download timed out' : (e.message || String(e));
        status.innerHTML = '⚠️ ' + escapeHtml(reason) +
          ' — <a href="' + arxivUrl + '" target="_blank">read on arXiv ↗</a>';
        status.className = 'pdf-status error';
      } finally {
        clearTimeout(timer);
        if (pdfTick) { clearInterval(pdfTick); pdfTick = null; }
        if (pdfAbort === controller) pdfAbort = null;
      }
    }

    function unpinPaper() {
      pinned = null;
      if (pdfAbort) pdfAbort.abort();
      if (currentPdfUrl) { URL.revokeObjectURL(currentPdfUrl); currentPdfUrl = null; }
      pdfLoadedFor = null;
      $('pdfFrame').removeAttribute('src');
      $('pdfStatus').classList.add('hidden');
      $('readerView').classList.add('hidden');
      $('paperInfo').classList.add('hidden');
      $('notesBox').classList.add('hidden');
      $('pickerView').classList.remove('hidden');
      clearSelection();
      renderPaperList();
      renderQuickPrompts();
    }

    // ---------- Text view: selection + highlighting ----------

    function resetReaderForNewPaper() {
      fullText = '';
      textLoadedFor = null;
      textLoadingFor = null;
      pendingSelection = '';
      clearSelection();
      $('textFrame').removeAttribute('src');
      $('selBubble').classList.add('hidden');
      if (currentPdfUrl) { URL.revokeObjectURL(currentPdfUrl); currentPdfUrl = null; }
      pdfLoadedFor = null;
      $('pdfFrame').removeAttribute('src');
      $('pdfStatus').classList.add('hidden');
      switchView('text');   // Text is the default; PDF loads only on demand
    }

    function switchView(v) {
      currentView = v;
      $('viewPdfBtn').classList.toggle('active', v === 'pdf');
      $('viewTextBtn').classList.toggle('active', v === 'text');
      $('pdfWrap').classList.toggle('hidden', v !== 'pdf');
      $('textWrap').classList.toggle('hidden', v !== 'text');
      if (v === 'text' && pinned) loadText(pinned);
      if (v === 'pdf' && pinned) showPdf(pinned);
    }

    let textLoadingFor = null;  // guards against duplicate loads/polls
    function loadText(p) {
      const frame = $('textFrame');
      const nid = normalizeId(p.arxiv_id);
      if (textLoadedFor === nid || textLoadingFor === nid) return;
      textLoadingFor = nid;
      $('viewHint').textContent = 'Loading full text…';
      frame.src = '/api/chat/fulltext?arxiv_id=' + encodeURIComponent(p.arxiv_id);
      const poll = setInterval(() => {
        let doc = null;
        try { doc = frame.contentDocument; } catch (e) {}
        if (!doc || !doc.body || doc.readyState === 'loading') return;
        if ((doc.body.innerText || '').length < 200) return;
        clearInterval(poll);
        textLoadingFor = null;
        fullText = doc.body.innerText;
        textLoadedFor = normalizeId(p.arxiv_id);
        $('viewHint').textContent = 'Select any text, then ask about it.';
        doc.addEventListener('mouseup', onTextSelect);
        doc.addEventListener('keyup', onTextSelect);
        doc.addEventListener('selectionchange', scheduleSelect);
        try {
          frame.contentWindow.addEventListener('scroll', hideSelectBubble, { passive: true });
        } catch (e) {}
        // Catch mouse releases that land outside the iframe (common when dragging).
        if (!parentMouseUpBound) { window.addEventListener('mouseup', onParentMouseUp); parentMouseUpBound = true; }
        if (pendingHighlights.length) {
          const nH = highlightQuotes(pendingHighlights);
          pendingHighlights = [];
          if (nH) addSystemMessage('️ Highlighted ' + nH + ' passage' + (nH > 1 ? 's' : '') + ' in the paper.');
        }
        if (userHighlights.length) applyUserHighlights();
      }, 300);
    }

    let lastSelect = 0;
    let parentMouseUpBound = false;
    function scheduleSelect() {
      const now = Date.now();
      if (now - lastSelect < 80) return;
      lastSelect = now;
      onTextSelect();
    }
    function onParentMouseUp() {
      if (currentView !== 'text' || !pinned) return;
      setTimeout(onTextSelect, 0);
    }
    function hideSelectBubble() { $('selBubble').classList.add('hidden'); }

    function onTextSelect() {
      const frame = $('textFrame');
      const bubble = $('selBubble');
      try {
        const sel = frame.contentWindow.getSelection();
        const txt = sel ? sel.toString().replace(/\\s+/g, ' ').trim() : '';
        if (!txt || txt.length < 3 || !sel.rangeCount) { bubble.classList.add('hidden'); pendingSelection = ''; return; }
        pendingSelection = txt;
        const r = sel.getRangeAt(0).getBoundingClientRect();
        bubble.style.left = Math.max(60, Math.min(r.left + r.width / 2, frame.clientWidth - 60)) + 'px';
        bubble.style.top = Math.max(4, r.top - 36) + 'px';
        bubble.classList.remove('hidden');
      } catch (e) { bubble.classList.add('hidden'); }
    }

    function attachSelection() {
      if (!pendingSelection) return;
      activeSelection = pendingSelection;
      pendingSelection = '';
      $('selBubble').classList.add('hidden');
      const chip = $('selChip');
      $('selChipText').textContent = activeSelection.length > 90
        ? activeSelection.substring(0, 90) + '…' : activeSelection;
      chip.classList.remove('hidden');
      try { $('textFrame').contentWindow.getSelection().removeAllRanges(); } catch (e) {}
      $('chatInput').focus();
    }

    function clearSelection() {
      activeSelection = '';
      pendingSelection = '';
      $('selChip').classList.add('hidden');
      $('selBubble').classList.add('hidden');
    }

    function highlightQuotes(quotes) {
      const frame = $('textFrame');
      let doc;
      try { doc = frame.contentDocument; } catch (e) { return 0; }
      if (!doc || !doc.body) return 0;
      let count = 0;
      let first = null;
      for (const q of quotes) {
        const mk = highlightOne(doc, q);
        if (mk) { count++; if (!first) first = mk; }
      }
      if (first) { try { first.scrollIntoView({ block: 'center' }); } catch (e) {} }
      return count;
    }

    function highlightOne(doc, quote, user) {
      const needle = quote.replace(/\\s+/g, ' ').trim();
      if (needle.length < 8) return false;
      const walker = doc.createTreeWalker(doc.body, NodeFilter.SHOW_TEXT, null);
      const nodes = [];
      let n;
      while ((n = walker.nextNode())) nodes.push(n);
      const cleaned = nodes.map(nd => nd.data.replace(/\\s+/g, ' '));
      const total = cleaned.join('');
      const lt = total.toLowerCase();
      const ln = needle.toLowerCase();
      let idx = total.indexOf(needle);
      let matchLen = needle.length;
      if (idx === -1) idx = lt.indexOf(ln);                 // case-insensitive
      if (idx === -1) {                                     // tolerant prefix match
        for (const L of [60, 40, 30, 24]) {
          if (ln.length <= L) continue;
          const i = lt.indexOf(ln.substring(0, L));
          if (i !== -1) { idx = i; matchLen = L; break; }
        }
      }
      if (idx === -1) return false;
      const end = idx + matchLen;
      let pos = 0, sNode = null, sOff = 0, eNode = null, eOff = 0;
      for (let i = 0; i < nodes.length; i++) {
        const len = cleaned[i].length;
        if (sNode === null && idx < pos + len) { sNode = nodes[i]; sOff = idx - pos; }
        if (end <= pos + len) { eNode = nodes[i]; eOff = end - pos; break; }
        pos += len;
      }
      if (!sNode || !eNode) return false;
      try {
        const range = doc.createRange();
        range.setStart(sNode, cleanToRaw(sNode.data, sOff));
        range.setEnd(eNode, cleanToRaw(eNode.data, eOff));
        const mark = doc.createElement('mark');
        if (user) {
          mark.className = 'user-hl';
          mark.title = 'Click to remove this highlight';
          const q = quote;
          mark.onclick = () => removeUserHighlight(q, mark);
        } else {
          mark.style.background = '#ffe08a';
        }
        range.surroundContents(mark);
        return mark;
      } catch (e) {
        return null;   // range crossed a boundary; skip
      }
    }

    // ---------- User highlights + notes (issue #6) ----------

    function applyUserHighlights() {
      let doc;
      try { doc = $('textFrame').contentDocument; } catch (e) { return; }
      if (!doc || !doc.body) return;
      for (const q of userHighlights) highlightOne(doc, q, true);
    }

    async function ensurePinnedSaved() {
      if (!pinned) return false;
      if (savedIds.has(normalizeId(pinned.arxiv_id))) return true;
      try {
        const resp = await fetch('/api/save', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({
            arxiv_id: pinned.arxiv_id, title: pinned.title || '',
            authors: pinned.authors || '', abstract: pinned.abstract || '',
            relevance_score: pinned.score || 0,
            date_fetched: new Date().toISOString().slice(0, 10)
          })
        });
        const data = await resp.json();
        if (!data.success) return false;
        savedIds.add(normalizeId(pinned.arxiv_id));
        renderSaveToggle();
        renderPaperList();
        return true;
      } catch (e) {
        return false;
      }
    }

    async function persistHighlights() {
      if (!pinned) return;
      try {
        const resp = await fetch('/api/update_highlights', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ arxiv_id: pinned.arxiv_id, highlights: userHighlights })
        });
        const data = await resp.json();
        if (data.success) {
          userHighlights = data.highlights || [];
          pinned.highlights = userHighlights;
        } else {
          addSystemMessage('Could not save highlights: ' + (data.error || 'unknown error'));
        }
      } catch (e) {
        addSystemMessage('Could not save highlights: ' + e.message);
      }
    }

    async function highlightSelection() {
      const quote = pendingSelection;
      if (!quote || !pinned) return;
      $('selBubble').classList.add('hidden');
      if (!(await ensurePinnedSaved())) {
        alert('Highlights are stored with saved papers; the paper could not be saved.');
        return;
      }
      pendingSelection = '';
      try { $('textFrame').contentWindow.getSelection().removeAllRanges(); } catch (e) {}
      if (userHighlights.some(q => q.toLowerCase() === quote.toLowerCase())) return;
      userHighlights.push(quote);
      if (textLoadedFor === normalizeId(pinned.arxiv_id)) {
        let doc;
        try { doc = $('textFrame').contentDocument; } catch (e) { doc = null; }
        if (doc) highlightOne(doc, quote, true);
      }
      await persistHighlights();
    }

    function removeUserHighlight(quote, mark) {
      userHighlights = userHighlights.filter(q => q !== quote);
      try {
        const parent = mark.parentNode;
        parent.replaceChild(mark.ownerDocument.createTextNode(mark.textContent), mark);
        parent.normalize();
      } catch (e) {}
      persistHighlights();
    }

    async function savePinnedNotes() {
      if (!pinned) return;
      const notes = $('notesArea').value;
      if (!(await ensurePinnedSaved())) {
        alert('Notes are stored with saved papers; the paper could not be saved.');
        return;
      }
      try {
        const resp = await fetch('/api/update_notes', {
          method: 'POST', headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ arxiv_id: pinned.arxiv_id, notes: notes })
        });
        const data = await resp.json();
        if (data.success) {
          pinned.notes = notes;
          $('notesStatus').textContent = 'Saved ✓';
        } else {
          $('notesStatus').textContent = data.error || 'Save failed';
        }
      } catch (e) {
        $('notesStatus').textContent = 'Save failed: ' + e.message;
      }
    }

    function cleanToRaw(data, cleanOff) {
      let c = 0, i = 0;
      while (i < data.length && c < cleanOff) {
        if (/\\s/.test(data[i])) {
          c += 1;
          while (i < data.length && /\\s/.test(data[i])) i++;
        } else { c += 1; i += 1; }
      }
      return Math.min(i, data.length);
    }

    async function pinFromUrl(id) {
      const norm = normalizeId(id);
      let p = libraryById[norm] || libraryById[id];
      if (!p) {
        // Not in the local library — look it up on the arXiv API.
        try {
          const resp = await fetch('/api/arxiv/search?q=' + encodeURIComponent(id));
          const data = await resp.json();
          const hits = data.papers || [];
          const hit = hits.find(x => normalizeId(x.id) === norm) || hits[0];
          if (hit && hit.title) {
            p = {
              arxiv_id: hit.id || id,
              title: hit.title,
              authors: Array.isArray(hit.authors) ? hit.authors.join(', ') : (hit.authors || ''),
              abstract: hit.abstract || '',
              source: 'arxiv',
              score: 0
            };
          }
        } catch (e) { console.warn('arXiv lookup failed:', e); }
      }
      if (p) {
        if (!libraryById[normalizeId(p.arxiv_id)]) {
          library.push(p);
          libraryById[normalizeId(p.arxiv_id)] = p;
        }
        pinPaper(p);
      } else {
        addSystemMessage('Could not find paper "' + id + '". Pick one from the list.');
      }
    }

    // ---------- Paper search results rendered in chat ----------

    let resultPapers = [];   // accumulated tool-result papers, indexed by Read buttons

    function renderPaperResults(papers) {
      const container = $('chatMessages');
      const emptyEl = container.querySelector('.empty-chat');
      if (emptyEl) emptyEl.remove();
      const base = resultPapers.length;
      const div = document.createElement('div');
      div.className = 'paper-results';
      let html = '<div class="pr-head">📚 ' + papers.length + ' paper' + (papers.length !== 1 ? 's' : '') + ' found</div>';
      papers.forEach((p, i) => {
        const gi = base + i;
        resultPapers.push(p);
        const meta = [p.year, (p.citations != null ? p.citations + ' citations' : ''), (p.source || '')].filter(Boolean).join(' · ');
        html += '<div class="pr-item">' +
          '<div class="pr-title">' + escapeHtml(p.title || p.arxiv_id || '') + '</div>' +
          (meta ? '<div class="pr-meta">' + escapeHtml(meta) + '</div>' : '') +
          (p.tldr ? '<div class="pr-meta">' + escapeHtml(p.tldr) + '</div>' : '') +
          (p.arxiv_id ? '<button class="view-btn" onclick="readResultPaper(' + gi + ')">Read</button>' +
            '<a target="_blank" href="https://arxiv.org/abs/' + escapeHtml(p.arxiv_id) + '">arXiv ↗</a>' : '') +
          '</div>';
      });
      div.innerHTML = html;
      container.appendChild(div);
      scrollChat();
    }

    function readResultPaper(i) {
      const p = resultPapers[i];
      if (!p || !p.arxiv_id) return;
      pinPaper({ arxiv_id: p.arxiv_id, title: p.title || '', authors: p.authors || '',
                 abstract: p.abstract || '', source: p.source || 'discover' });
      switchView('text');
    }

    // ---------- Prompt building ----------

    function buildSystemPrompt() {
      const toolsNote = 'You have tools: search_papers(query) to find papers on a topic, ' +
        'find_related(arxiv_id) for similar papers, citation_graph(arxiv_id, kind) for ' +
        'cited-by/references, and web_search(query) for general web info.';
      const methodNote = 'Method: for a complex request, first decompose it into 2-5 concrete ' +
        'sub-questions; resolve each with the most suitable tool or the paper content; then ' +
        'synthesize ONE coherent answer. ' + toolsNote;
      const formatNote = 'Answer format (Markdown, concise): ' +
        '"## Answer" (the direct answer); ' +
        '"## Evidence" (up to 3 exact verbatim quotes from the paper, each on its own line ' +
        'prefixed "QUOTE: " — these are highlighted for the reader; omit if none); ' +
        '"## Related papers" (most relevant papers you found, title + arXiv id; omit if none); ' +
        '"## Sources & caveats" (web URLs relied on and any uncertainty; omit if none). ' +
        'Do not invent results, numbers, or author statements.';
      if (!pinned) {
        return 'You are ArXistant, a research assistant for an astronomer. Help the user answer ' +
          'questions and find papers. ' + methodNote + ' ' +
          'Answer format (Markdown, concise): "## Answer"; "## Related papers" (title + arXiv id); ' +
          '"## Sources & caveats" (URLs + uncertainty; omit if none).';
      }
      const hasFull = fullText && fullText.length > 200;
      const body = hasFull ? fullText.substring(0, 24000) : (pinned.abstract || '(no abstract available)');
      return [
        'You are ArXistant, a paper reading helper. The user is reading ONE arXiv paper right now.',
        '',
        'Title: ' + pinned.title,
        'Authors: ' + (pinned.authors || 'unknown'),
        'arXiv ID: ' + pinned.arxiv_id,
        '',
        (hasFull ? 'Full text (may be truncated):' : 'Abstract:'),
        body,
        '',
        'Rules:',
        '- Ground your answers in the provided paper content. If a question needs details not present, say so briefly, then answer what you can.',
        '- ' + methodNote,
        '- ' + formatNote
      ].join('\\n');
    }

    // ---------- Chat ----------

    function updateEmptyHint() {
      const el = $('emptyChat');
      if (!el) return;
      if (!configured) {
        el.textContent = 'Configure your LLM in the Settings panel (red arrow on the left), pick a paper, and start asking.';
      } else if (pinned) {
        el.textContent = 'Reading: “' + pinned.title + '”. Ask anything about it below.';
      } else {
        el.textContent = 'Pick a paper to read, or just ask a general question.';
      }
    }

    function addMessage(role, text) {
      const container = $('chatMessages');
      const emptyEl = container.querySelector('.empty-chat');
      if (emptyEl) emptyEl.remove();
      const div = document.createElement('div');
      div.className = 'message ' + role;
      let label = '';
      if (role === 'user') label = 'You';
      else if (role === 'assistant') label = 'Assistant';
      div.innerHTML = (label ? '<div class="label">' + label + '</div>' : '') + '<div class="msg-content"></div>';
      const contentEl = div.querySelector('.msg-content');
      if (role === 'assistant') contentEl.innerHTML = renderMarkdown(text);
      else contentEl.textContent = text;
      container.appendChild(div);
      scrollChat();
      return div;
    }

    function addSystemMessage(text) { addMessage('system', text); }

    // Single self-updating status box (the yellow one). While the model works,
    // every status update rewrites this one box in place; as soon as the answer
    // starts streaming, the box is removed and replaced by the result box.
    let statusBox = null;
    function setStatus(text) {
      const container = $('chatMessages');
      if (!statusBox || !statusBox.isConnected) {
        statusBox = addMessage('system', text);
      } else {
        statusBox.querySelector('.msg-content').textContent = text;
        container.appendChild(statusBox);  // keep it as the newest line
      }
      scrollChat();
    }
    function clearStatus() {
      if (statusBox && statusBox.isConnected) statusBox.remove();
      statusBox = null;
    }

    function scrollChat() {
      const container = $('chatMessages');
      container.scrollTop = container.scrollHeight;
    }

    function renderQuickPrompts() {
      const el = $('quickPrompts');
      if (!pinned || chatHistory.length > 0 || streaming) { el.innerHTML = ''; return; }
      el.innerHTML = QUICK_PROMPTS.map(q =>
        '<button class="quick-chip" onclick="askQuick(this)">' + escapeHtml(q) + '</button>').join('');
    }

    function askQuick(btn) {
      $('chatInput').value = btn.textContent;
      sendMessage();
    }

    function newChat() {
      if (chatHistory.length && !confirm('Start a new chat? The current conversation will be cleared.')) return;
      chatHistory = [];
      statusBox = null;
      $('chatMessages').innerHTML = '<div class="empty-chat" id="emptyChat"></div>';
      renderQuickPrompts();
      updateEmptyHint();
    }

    async function sendMessage() {
      const input = $('chatInput');
      const btn = $('sendBtn');
      const message = input.value.trim();
      if (!message || streaming) return;
      if (!configured) {
        addSystemMessage('⚠️ Configure the LLM first: open the Settings panel (red arrow on the left), pick a provider preset (or fill base URL, model and API key) and click Save Settings.');
        return;
      }
      input.value = '';
      addMessage('user', message);
      setStatus('💭 Thinking…');
      let holder = null;
      let contentEl = null;
      const startResultBox = () => {
        clearStatus();
        holder = addMessage('assistant', '');
        contentEl = holder.querySelector('.msg-content');
      };
      renderQuickPrompts();

      let llmMessage = message;
      if (activeSelection) {
        llmMessage = 'I selected this excerpt from the paper:\\n' + activeSelection + '\\n\\nMy question: ' + message;
      }

      const messages = [
        { role: 'system', content: buildSystemPrompt() },
        ...chatHistory,
        { role: 'user', content: llmMessage }
      ];

      streaming = true;
      btn.disabled = true;
      let acc = '';
      try {
        const resp = await fetch('/api/chat', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({ messages: messages })
        });
        const ctype = resp.headers.get('Content-Type') || '';
        if (!resp.ok || ctype.includes('application/json')) {
          let msg = 'Request failed (HTTP ' + resp.status + ')';
          try {
            const d = await resp.json();
            if (d.error) msg = d.error;
            if (d.details) msg += '\\n' + d.details;
          } catch (e) { /* keep default message */ }
          throw new Error(msg);
        }
        const reader = resp.body.getReader();
        const decoder = new TextDecoder();
        let buffer = '';
        for (;;) {
          const { done, value } = await reader.read();
          if (done) break;
          buffer += decoder.decode(value, { stream: true });
          const lines = buffer.split('\\n');
          buffer = lines.pop();
          for (const raw of lines) {
            const line = raw.trim();
            if (!line.startsWith('data:')) continue;
            const payload = line.slice(5).trim();
            if (payload === '[DONE]') continue;
            let obj;
            try { obj = JSON.parse(payload); } catch (e) { continue; }
            if (obj.error) throw new Error(obj.error);
            if (obj.status === 'web_search') {
              setStatus('🔎 Searching the web: ' + (obj.query || ''));
              continue;
            }
            if (obj.status === 'papers') {
              renderPaperResults(obj.papers || []);
              continue;
            }
            const choice = obj.choices && obj.choices[0];
            const delta = choice && ((choice.delta && choice.delta.content) ||
                                     (choice.message && choice.message.content));
            if (delta) {
              if (!holder) startResultBox();  // the result box replaces the status box
              acc += delta;
              contentEl.innerHTML = renderMarkdown(acc);
              scrollChat();
            }
          }
        }
        let display = acc;
        let quotes = (acc.match(/^QUOTE:\\s*(.+)$/gm) || [])
          .map(s => s.replace(/^QUOTE:\\s*/, '').trim()).filter(Boolean);
        if (!holder) startResultBox();  // e.g. an empty response with no deltas
        if (quotes.length) {
          display = acc.replace(/^QUOTE:.*$/gm, '').trim();
          contentEl.innerHTML = renderMarkdown(display);
        }
        if (!display) contentEl.innerHTML = '<em>(empty response)</em>';
        if (quotes.length && pinned) {
          pendingHighlights = quotes;
          if (textLoadedFor === normalizeId(pinned.arxiv_id)) {
            const nH = highlightQuotes(quotes);
            pendingHighlights = [];
            switchView('text');
            if (nH) addSystemMessage('️ Highlighted ' + nH + ' passage' + (nH > 1 ? 's' : '') + ' in the paper.');
          } else {
            switchView('text');   // loadText will apply pendingHighlights
          }
        }
        clearSelection();
        chatHistory.push({ role: 'user', content: llmMessage });
        chatHistory.push({ role: 'assistant', content: display });
        if (chatHistory.length > 40) chatHistory = chatHistory.slice(-40);
      } catch (e) {
        if (holder) holder.remove();
        clearStatus();
        addSystemMessage('⚠️ ' + e.message);
      } finally {
        streaming = false;
        clearStatus();
        btn.disabled = false;
        input.focus();
        renderQuickPrompts();
      }
    }

    // ---------- Markdown rendering ----------

    function renderMarkdown(text) {
      if (!text) return '';
      let codeBlocks = [];
      text = text.replace(/```(\\w+)?\\n([\\s\\S]*?)```/g, function(match, lang, code) {
        const id = codeBlocks.length;
        codeBlocks.push('<pre><code>' + escapeHtml(code) + '</code></pre>');
        return '___CODEBLOCK_' + id + '___';
      });
      let inlineCodes = [];
      text = text.replace(/`([^`]+)`/g, function(match, code) {
        const id = inlineCodes.length;
        inlineCodes.push('<code>' + escapeHtml(code) + '</code>');
        return '___INLINECODE_' + id + '___';
      });
      text = escapeHtml(text);
      text = text.replace(/\\*\\*([^*]+)\\*\\*/g, '<strong>$1</strong>');
      text = text.replace(/\\*([^*]+)\\*/g, '<em>$1</em>');
      text = text.replace(/^#{1,6}\\s+(.+)$/gm, '<strong>$1</strong>');
      text = text.replace(/^\\s*[-•]\\s+(.+)$/gm, '• $1');
      text = text.replace(/\\n/g, '<br>');
      inlineCodes.forEach((code, i) => { text = text.replace('___INLINECODE_' + i + '___', code); });
      codeBlocks.forEach((code, i) => { text = text.replace('___CODEBLOCK_' + i + '___', code); });
      return text;
    }

    // ---------- Init ----------

    (async function init() {
      buildPresetOptions();
      initPanes();
      updateEmptyHint();
      await loadConfig();
      await loadLibrary();
      const params = new URLSearchParams(location.search);
      const pid = params.get('paper');
      if (pid) await pinFromUrl(decodeURIComponent(pid));
      updateEmptyHint();
    })();
  </script>
</body>
</html>
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
    .tag-bar { margin: 0 0 16px 0; padding: 10px 12px; background: #f0f7f6; border: 1px solid #d5e8e5; border-radius: 6px; }
    .tag-bar-title { font-size: 0.8em; font-weight: bold; color: #00695c; margin-bottom: 6px; }
    .tag-bar-title .hint { font-weight: normal; color: #888; }
    .tag-bar-chips { display: flex; flex-wrap: wrap; gap: 6px; }
    .tag-bar-chips:empty::after { content: 'No tags yet. Add tags with the 🏷️ button on a paper below, or from the Daily/Recent pages.'; color: #888; font-size: 0.8em; }
    .tag-filter-chip { display: inline-flex; align-items: center; gap: 5px; background: white; color: #00695c; border: 1px solid #b2dfdb; border-radius: 12px; padding: 2px 10px; font-size: 0.8em; cursor: pointer; user-select: none; }
    .tag-filter-chip:hover { background: #e0f2f1; }
    .tag-filter-chip.selected { background: #00796b; color: white; border-color: #00796b; }
    .tag-filter-chip .count { opacity: 0.7; font-size: 0.85em; }
    .tag-clear { background: none; border: none; color: #b31b1b; font-size: 0.8em; cursor: pointer; margin-left: 8px; font-weight: normal; }
    .paper-tags { margin: 6px 0; display: flex; flex-wrap: wrap; gap: 6px; align-items: center; }
    .paper-tag { background: #e0f2f1; color: #00695c; border: 1px solid #b2dfdb; border-radius: 10px; padding: 1px 8px; font-size: 0.75em; }
    .tag-edit-btn { background: none; border: 1px solid #b2dfdb; color: #00695c; border-radius: 10px; padding: 1px 8px; font-size: 0.75em; cursor: pointer; }
    .tag-edit-btn:hover { background: #e0f2f1; }
    .tag-editor { margin-top: 8px; padding: 10px 12px; background: #fff; border: 1px solid #d7d7d7; border-radius: 6px; clear: both; }
    .tag-editor-title { font-size: 0.8em; font-weight: bold; color: #00695c; margin-bottom: 6px; }
    .tag-chips { display: flex; flex-wrap: wrap; gap: 6px; margin-bottom: 8px; }
    .tag-chips:empty::after { content: 'No tags yet.'; color: #999; font-size: 0.75em; }
    .tag-chip { display: inline-flex; align-items: center; gap: 5px; background: #e0f2f1; color: #00695c; border: 1px solid #b2dfdb; border-radius: 10px; padding: 1px 8px; font-size: 0.75em; }
    .tag-chip button { background: none; border: none; color: #00695c; cursor: pointer; padding: 0; font-size: 1em; line-height: 1; }
    .tag-chip button:hover { color: #c62828; }
    .tag-input-row { display: flex; gap: 6px; margin-bottom: 8px; }
    .tag-input-row input { flex: 1; min-width: 0; padding: 4px 8px; border: 1px solid #ccc; border-radius: 4px; font-size: 0.8em; }
    .tag-input-row button { padding: 4px 10px; background: #00796b; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 0.75em; }
    .tag-editor-actions { display: flex; gap: 6px; }
    .tag-editor-actions .tag-save { padding: 4px 10px; background: #b31b1b; color: white; border: none; border-radius: 4px; cursor: pointer; font-size: 0.75em; }
    .tag-editor-actions .tag-save:disabled { background: #ccc; cursor: wait; }
    .tag-editor-actions .tag-close { padding: 4px 10px; background: #eee; color: #555; border: none; border-radius: 4px; cursor: pointer; font-size: 0.75em; }
    .delete-btn { float: right; background: #c62828; color: white; border: none; border-radius: 4px; padding: 4px 10px; cursor: pointer; font-size: 0.8em; }
    .chat-btn { float: right; margin-right: 8px; background: #555; color: white; border-radius: 4px; padding: 4px 10px; font-size: 0.8em; text-decoration: none; }
    .chat-btn:hover { background: #333; }
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
    <a href="/cloud-sync.html">☁️ Cloud Sync</a>
  </div>
  <h1>My arXiv Paper Database</h1>
  <input type="text" class="search-box" id="searchInput" placeholder="Search by title, author, abstract, or notes..." oninput="searchPapers()">
  <div id="tagBar" class="tag-bar"></div>
  <p class="stats" id="stats">Loading...</p>
  <div id="paperList"></div>

  <script>
    let allPapers = [];
    let selectedTags = new Set();  // lowercased tag keys; papers must carry ALL of them
    let openEditorId = null;

    function parseTags(value) {
      return String(value || '').split(',').map(t => t.trim()).filter(Boolean);
    }
    function paperTags(p) { return parseTags(p.tags); }
    function tagKey(t) { return t.toLowerCase(); }
    function cardId(arxivId) { return 'papercard-' + arxivId.replace(/\\./g, '_'); }
    function editorId(arxivId) { return 'tageditor-' + arxivId.replace(/\\./g, '_'); }

    async function loadPapers() {
      const resp = await fetch('/api/papers');
      const data = await resp.json();
      allPapers = data.papers;
      renderTagBar();
      applyFilters();
    }

    function searchPapers() {
      applyFilters();
    }

    // ── Tag filtering (issue #3) ──
    function allTagCounts() {
      const counts = new Map();  // key -> { label, count }
      allPapers.forEach(p => {
        const seen = new Set();
        paperTags(p).forEach(t => {
          const k = tagKey(t);
          if (seen.has(k)) return;
          seen.add(k);
          const entry = counts.get(k) || { label: t, count: 0 };
          entry.count += 1;
          counts.set(k, entry);
        });
      });
      return [...counts.entries()].sort((a, b) => a[0].localeCompare(b[0]));
    }

    function renderTagBar() {
      const bar = document.getElementById('tagBar');
      const entries = allTagCounts();
      let html = '<div class="tag-bar-title">🏷️ Filter by tags <span class="hint">(papers must have ALL selected tags)</span>' +
        (selectedTags.size ? '<button class="tag-clear" data-action="clear">Clear filter</button>' : '') +
        '</div>';
      html += '<div class="tag-bar-chips">' + entries.map(([k, e]) =>
        `<span class="tag-filter-chip${selectedTags.has(k) ? ' selected' : ''}" data-tag="${escapeAttr(k)}">${escapeHtml(e.label)} <span class="count">${e.count}</span></span>`
      ).join('') + '</div>';
      bar.innerHTML = html;
    }

    document.getElementById('tagBar').addEventListener('click', (e) => {
      const chip = e.target.closest('.tag-filter-chip');
      if (chip) {
        const k = chip.dataset.tag;
        if (selectedTags.has(k)) selectedTags.delete(k); else selectedTags.add(k);
        renderTagBar();
        applyFilters();
        return;
      }
      if (e.target.closest('[data-action="clear"]')) {
        selectedTags.clear();
        renderTagBar();
        applyFilters();
      }
    });

    async function applyFilters() {
      const q = document.getElementById('searchInput').value.trim();
      let base = allPapers;
      if (q) {
        const resp = await fetch('/api/search?q=' + encodeURIComponent(q));
        const data = await resp.json();
        base = data.papers;
      }
      if (selectedTags.size) {
        base = base.filter(p => {
          const have = new Set(paperTags(p).map(tagKey));
          return [...selectedTags].every(k => have.has(k));
        });
      }
      renderPapers(base);
    }

    function renderPapers(papers) {
      const container = document.getElementById('paperList');
      const filtered = papers.length !== allPapers.length;
      document.getElementById('stats').textContent = filtered
        ? 'Showing ' + papers.length + ' of ' + allPapers.length + ' saved papers'
        : papers.length + ' paper' + (papers.length !== 1 ? 's' : '') + ' saved';

      if (papers.length === 0) {
        container.innerHTML = '<p class="empty">No papers found. Go to the daily list and click "💾 Save to DB" on papers you want to keep.</p>';
        return;
      }

      container.innerHTML = papers.map(p => {
        const tags = paperTags(p);
        const tagsHtml = tags.map(t => `<span class="paper-tag">${escapeHtml(t)}</span>`).join('');
        return `
        <div class="paper" id="${cardId(p.arxiv_id)}">
          <button class="delete-btn" onclick="deletePaper('${p.arxiv_id}')">🗑 Delete</button>
          <a class="chat-btn" href="/chat.html?paper=${p.arxiv_id}">💬 Chat</a>
          <h2><a href="https://arxiv.org/abs/${p.arxiv_id}" target="_blank">${escapeHtml(p.title)}</a></h2>
          <span class="score">Relevance: ${p.relevance_score}</span>
          <p class="authors"><strong>Authors:</strong> ${escapeHtml(p.authors)}</p>
          <p class="meta">Saved: ${p.date_saved} | Fetched: ${p.date_fetched || 'N/A'}</p>
          <div class="paper-tags">${tagsHtml}<button class="tag-edit-btn" onclick="openTagEditor('${p.arxiv_id}')">🏷️ ${tags.length ? 'Edit tags' : 'Add tags'}</button></div>
          <details>
            <summary><strong style="color:#b31b1b;">View abstract</strong></summary>
            <p class="abstract-full">${escapeHtml(p.abstract)}</p>
          </details>
          <div class="notes">
            <textarea id="note-${p.arxiv_id}" placeholder="Add your notes here...">${escapeHtml(p.notes || '')}</textarea>
            <button onclick="saveNotes('${p.arxiv_id}')">💾 Save Notes</button>
          </div>
        </div>
      `}).join('');
    }

    // ── Tag editing (issue #3) ──
    function closeTagEditor() {
      if (openEditorId) {
        const el = document.getElementById(editorId(openEditorId));
        if (el) el.remove();
        openEditorId = null;
      }
    }

    function renderTagChips(container, working) {
      container.innerHTML = '';
      working.forEach((tag, idx) => {
        const chip = document.createElement('span');
        chip.className = 'tag-chip';
        const label = document.createElement('span');
        label.textContent = tag;
        const x = document.createElement('button');
        x.textContent = '✕';
        x.title = 'Remove tag';
        x.onclick = () => { working.splice(idx, 1); renderTagChips(container, working); };
        chip.appendChild(label);
        chip.appendChild(x);
        container.appendChild(chip);
      });
    }

    function openTagEditor(arxivId) {
      if (openEditorId === arxivId) { closeTagEditor(); return; }
      closeTagEditor();
      const paper = allPapers.find(p => p.arxiv_id === arxivId);
      const card = document.getElementById(cardId(arxivId));
      if (!paper || !card) return;

      const editor = document.createElement('div');
      editor.className = 'tag-editor';
      editor.id = editorId(arxivId);
      editor.innerHTML =
        '<div class="tag-editor-title">🏷️ Tags for this paper</div>' +
        '<div class="tag-chips"></div>' +
        '<div class="tag-input-row"><input type="text" maxlength="60" placeholder="Add a tag (e.g. JWST, black holes)…"><button class="tag-add">Add</button></div>' +
        '<div class="tag-editor-actions"><button class="tag-save">💾 Save tags</button><button class="tag-close">Close</button></div>';

      const working = paperTags(paper).slice();
      const chips = editor.querySelector('.tag-chips');
      const input = editor.querySelector('input');
      renderTagChips(chips, working);

      const addFromInput = () => {
        const tag = input.value.trim();
        if (!tag) return;
        if (!working.some(t => tagKey(t) === tagKey(tag))) working.push(tag);
        input.value = '';
        renderTagChips(chips, working);
      };
      editor.querySelector('.tag-add').onclick = addFromInput;
      input.onkeydown = (e) => { if (e.key === 'Enter') { e.preventDefault(); addFromInput(); } };
      editor.querySelector('.tag-close').onclick = closeTagEditor;
      editor.querySelector('.tag-save').onclick = async () => {
        const saveEl = editor.querySelector('.tag-save');
        saveEl.disabled = true;
        try {
          const resp = await fetch('/api/update_tags', {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({ arxiv_id: arxivId, tags: working })
          });
          const data = await resp.json();
          if (data.success) {
            paper.tags = (data.tags || []).join(', ');
            openEditorId = null;
            renderTagBar();
            applyFilters();
          } else {
            alert('Could not save tags: ' + (data.error || 'unknown error'));
          }
        } catch (e) {
          alert('Could not save tags: ' + e.message);
        } finally {
          saveEl.disabled = false;
        }
      };

      card.appendChild(editor);
      openEditorId = arxivId;
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

    function escapeAttr(text) {
      return escapeHtml(text).replace(/'/g, '&#39;').replace(/"/g, '&quot;');
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
    <a href="/cloud-sync.html">☁️ Cloud Sync</a>
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
    ensure_data_dirs()
    init_db()
    arxistant_sync.maybe_auto_sync_on_start()
    arxistant_sync.start_periodic_sync()
    # Bind to a specific loopback address when requested (e.g. 127.0.0.1 on
    # Android, where "localhost" can resolve to ::1 and not match the WebView).
    bind_host = os.environ.get("ARXISTANT_BIND", "localhost")
    # Threading server: chat completions stream for a long time and must not
    # block the rest of the app. SQLite connections are opened per request,
    # and the shared retrain state is guarded by a lock, so this is safe.
    server = ThreadingHTTPServer((bind_host, port), Handler)
    print(f"Server running at http://{bind_host}:{port}")
    print(f"  Daily papers:     http://{bind_host}:{port}/")
    print(f"  Recent papers:    http://{bind_host}:{port}/recent.html")
    print(f"  Saved papers:     http://{bind_host}:{port}/database.html")
    print(f"  My publications:  http://{bind_host}:{port}/publications.html")
    print(f"  My ML features:   http://{bind_host}:{port}/ml-features.html")
    print(f"  Search arXiv/ADS: http://{bind_host}:{port}/search-arxiv.html")
    print(f"  Chat with papers: http://{bind_host}:{port}/chat.html")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped")


if __name__ == '__main__':
    run_server(int(os.environ.get("ARXISTANT_PORT", "8765")))
