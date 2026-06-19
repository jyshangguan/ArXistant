#!/usr/bin/env python3
"""
Lightweight HTTP server for arXiv paper database + My Publications database.
Serves HTML pages and provides REST API for SQLite database.

Usage:
    python arxiv_db_server.py
    # Then open http://localhost:8765 in your browser
"""

import json
import os
import sqlite3
import urllib.parse
from http.server import HTTPServer, BaseHTTPRequestHandler
from datetime import datetime

DB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arxiv_papers.db")
DAILY_HTML = os.path.join(os.path.dirname(os.path.abspath(__file__)), "arxiv_ranked_personalized.html")
PUBLICATIONS_JSON = os.path.join(os.path.dirname(os.path.abspath(__file__)), "shangguan_papers_metadata.json")
BIB_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "scix_library_20.bib")
INTERESTS_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "interests.txt")

# Import the weighted keyword extraction pipeline
import sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import interest_generator


def regenerate_interests():
    """Regenerate interests.txt using the weighted keyword extraction pipeline."""
    return interest_generator.regenerate_interests(DB_PATH, INTERESTS_FILE)


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
    
    # Regenerate interests.txt from all database content
    regenerate_interests()


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
                if 'saveToDatabase' not in html:
                    html = html.replace('</body>', SAVE_BUTTON_SCRIPT + '</body>')
                self._send_html(html)
            else:
                self._send_text("Daily paper list not found. Run the ranker first.", 404)

        elif path == "/database.html":
            self._send_html(DATABASE_VIEWER_HTML)

        elif path == "/publications.html":
            self._send_html(PUBLICATIONS_VIEWER_HTML)

        elif path == "/interests.html":
            self._send_html(INTERESTS_EDITOR_HTML)

        elif path == "/api/interests":
            if os.path.exists(INTERESTS_FILE):
                with open(INTERESTS_FILE, 'r', encoding='utf-8') as f:
                    content = f.read()
            else:
                content = ""
            self._send_json({"content": content, "count": len([l for l in content.splitlines() if l.strip() and not l.strip().startswith('#')])})

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

        else:
            self._send_text("Not found", 404)

    def do_POST(self):
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path

        content_length = int(self.headers.get('Content-Length', 0))
        body = self.rfile.read(content_length).decode('utf-8')
        data = json.loads(body) if body else {}

        if path == "/api/save":
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            try:
                c.execute('''
                    INSERT OR REPLACE INTO saved_papers 
                    (arxiv_id, title, authors, abstract, relevance_score, date_fetched, notes)
                    VALUES (?, ?, ?, ?, ?, ?, ?)
                ''', (
                    data.get("arxiv_id", ""),
                    data.get("title", ""),
                    data.get("authors", ""),
                    data.get("abstract", ""),
                    data.get("relevance_score", 0),
                    data.get("date_fetched", ""),
                    data.get("notes", "")
                ))
                conn.commit()
                self._send_json({"success": True, "message": "Paper saved"})
                
                # Regenerate interests.txt in background (non-blocking)
                try:
                    regenerate_interests()
                except Exception as e:
                    print(f"Warning: failed to regenerate interests.txt: {e}")
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)
            finally:
                conn.close()

        elif path == "/api/delete":
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("DELETE FROM saved_papers WHERE arxiv_id = ?", (data.get("arxiv_id"),))
            conn.commit()
            conn.close()
            self._send_json({"success": True, "message": "Paper deleted"})
            
            # Regenerate interests.txt after deletion too
            try:
                regenerate_interests()
            except Exception as e:
                print(f"Warning: failed to regenerate interests.txt: {e}")

        elif path == "/api/update_notes":
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute("UPDATE saved_papers SET notes = ? WHERE arxiv_id = ?", 
                      (data.get("notes", ""), data.get("arxiv_id", "")))
            conn.commit()
            conn.close()
            self._send_json({"success": True, "message": "Notes updated"})

        elif path == "/api/regenerate-interests":
            try:
                regenerate_interests()
                self._send_json({"success": True, "message": "Interests regenerated"})
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)

        elif path == "/api/interests":
            try:
                with open(INTERESTS_FILE, 'w', encoding='utf-8') as f:
                    f.write(data.get("content", ""))
                self._send_json({"success": True, "message": "Interests saved"})
            except Exception as e:
                self._send_json({"success": False, "error": str(e)}, 500)

        else:
            self._send_text("Not found", 404)


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
                btn.textContent = '💾 Save to DB';
                btn.style.background = '#b31b1b';
                btn.style.cursor = 'pointer';
                btn.dataset.saved = 'false';
                // Regenerate interests.txt in background
                fetch('/api/regenerate-interests', {method: 'POST'}).catch(() => {});
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
                btn.textContent = '✓ Saved';
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
        btn.style.cssText = 'margin-top:8px;padding:4px 12px;color:white;border:none;border-radius:4px;cursor:pointer;font-size:0.85em;';

        if (savedIds.has(arxivId)) {
            btn.textContent = '✓ Saved';
            btn.style.background = '#2e7d32';
            btn.dataset.saved = 'true';
        } else {
            btn.textContent = '💾 Save to DB';
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
    <a href="/">← Daily Papers</a>
    <a href="/publications.html">📚 My Publications</a>
    <a href="/interests.html">⚙️ My Interests</a>
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
  <title>My Publications — Jinyi Shangguan</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; max-width: 1000px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #333; }
    h1 { color: #1a1a1a; border-bottom: 2px solid #b31b1b; padding-bottom: 10px; }
    .search-box { width: 100%; padding: 10px 14px; font-size: 1em; border: 2px solid #ddd; border-radius: 6px; margin-bottom: 20px; box-sizing: border-box; }
    .search-box:focus { outline: none; border-color: #b31b1b; }
    .stats { color: #666; margin-bottom: 20px; }
    .paper { border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; margin-bottom: 16px; background: #fafafa; }
    .paper:hover { background: #f5f5f5; }
    h2 { font-size: 1.1em; margin-top: 0; }
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
    .nav { margin-bottom: 20px; display: flex; gap: 16px; }
    .nav a { color: #b31b1b; text-decoration: none; font-weight: bold; }
    .nav a:hover { text-decoration: underline; }
    .empty { color: #888; font-style: italic; text-align: center; padding: 40px; }
  </style>
</head>
<body>
  <div class="nav">
    <a href="/">← Daily Papers</a>
    <a href="/database.html">📂 Saved Papers</a>
    <a href="/interests.html">⚙️ My Interests</a>
  </div>
  <h1>📚 My Publications — Jinyi Shangguan</h1>
  <input type="text" class="search-box" id="searchInput" placeholder="Search my publications by title, author, abstract, or keywords..." oninput="searchPubs()">
  <p class="stats" id="stats">Loading...</p>
  <div id="pubList"></div>

  <script>
    let allPubs = [];

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
      document.getElementById('stats').textContent = pubs.length + ' publication' + (pubs.length !== 1 ? 's' : '') + ' found';
      
      if (pubs.length === 0) {
        container.innerHTML = '<p class="empty">No publications found matching your search.</p>';
        return;
      }
      
      container.innerHTML = pubs.map(p => `
        <div class="paper">
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

    loadPubs();
  </script>
</body>
</html>
"""


INTERESTS_EDITOR_HTML = """<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8">
  <meta name="viewport" content="width=device-width, initial-scale=1.0">
  <title>My Research Interests</title>
  <style>
    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #333; }
    h1 { color: #1a1a1a; border-bottom: 2px solid #b31b1b; padding-bottom: 10px; }
    .nav { margin-bottom: 20px; display: flex; gap: 16px; }
    .nav a { color: #b31b1b; text-decoration: none; font-weight: bold; }
    .nav a:hover { text-decoration: underline; }
    textarea { width: 100%; min-height: 500px; padding: 12px; font-family: monospace; font-size: 0.9em; border: 2px solid #ddd; border-radius: 6px; box-sizing: border-box; line-height: 1.5; }
    textarea:focus { outline: none; border-color: #b31b1b; }
    .toolbar { display: flex; gap: 12px; margin: 12px 0; flex-wrap: wrap; }
    button { padding: 8px 16px; border: none; border-radius: 6px; cursor: pointer; font-size: 0.9em; font-weight: 500; }
    .btn-save { background: #b31b1b; color: white; }
    .btn-save:hover { background: #8a1515; }
    .btn-regenerate { background: #555; color: white; }
    .btn-regenerate:hover { background: #333; }
    .status { padding: 10px 14px; border-radius: 6px; margin-top: 12px; font-size: 0.9em; display: none; }
    .status.ok { display: block; background: #e8f5e9; color: #2e7d32; border: 1px solid #c8e6c9; }
    .status.error { display: block; background: #ffebee; color: #c62828; border: 1px solid #ffcdd2; }
    .stats { color: #666; margin-bottom: 12px; font-size: 0.9em; }
    .hint { color: #888; font-size: 0.85em; margin-top: 8px; }
  </style>
</head>
<body>
  <div class="nav">
    <a href="/">← Daily Papers</a>
    <a href="/database.html">📂 Saved Papers</a>
    <a href="/publications.html">📚 My Publications</a>
  </div>
  <h1>My Research Interests</h1>
  <p class="stats" id="stats">Loading...</p>
  <div class="toolbar">
    <button class="btn-save" onclick="saveInterests()">💾 Save</button>
    <button class="btn-regenerate" onclick="regenerateInterests()">🔄 Regenerate from DB</button>
  </div>
  <textarea id="interestsText" placeholder="# Add your research interests here, one per line..."></textarea>
  <p class="hint">Lines starting with # are comments. Each non-empty line is a keyword used for ranking. Save to apply changes.</p>
  <div class="status" id="status"></div>

  <script>
    async function loadInterests() {
      try {
        const resp = await fetch('/api/interests');
        const data = await resp.json();
        document.getElementById('interestsText').value = data.content;
        document.getElementById('stats').textContent = data.count + ' keywords loaded';
      } catch (e) {
        showStatus('Failed to load interests', true);
      }
    }

    async function saveInterests() {
      const content = document.getElementById('interestsText').value;
      try {
        const resp = await fetch('/api/interests', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({content: content})
        });
        const data = await resp.json();
        if (data.success) {
          showStatus('Saved! Refresh the daily paper page to see new rankings.', false);
          const count = content.split('\\n').filter(l => l.trim() && !l.trim().startsWith('#')).length;
          document.getElementById('stats').textContent = count + ' keywords';
        } else {
          showStatus('Save failed: ' + (data.error || 'unknown'), true);
        }
      } catch (e) {
        showStatus('Save failed: ' + e.message, true);
      }
    }

    async function regenerateInterests() {
      if (!confirm('This will regenerate the list from saved papers + your publications. Custom keywords you added will be preserved. Continue?')) return;
      try {
        const resp = await fetch('/api/regenerate-interests', {method: 'POST'});
        const data = await resp.json();
        if (data.success) {
          showStatus('Regenerated! Reloading...', false);
          setTimeout(loadInterests, 500);
        } else {
          showStatus('Regeneration failed: ' + (data.error || 'unknown'), true);
        }
      } catch (e) {
        showStatus('Regeneration failed: ' + e.message, true);
      }
    }

    function showStatus(msg, isError) {
      const el = document.getElementById('status');
      el.textContent = msg;
      el.className = 'status ' + (isError ? 'error' : 'ok');
      setTimeout(() => el.className = 'status', 3000);
    }

    loadInterests();
  </script>
</body>
</html>
"""


def run_server(port=8765):
    init_db()
    server = HTTPServer(("localhost", port), Handler)
    print(f"Server running at http://localhost:{port}")
    print(f"  Daily papers:    http://localhost:{port}/")
    print(f"  Saved papers:     http://localhost:{port}/database.html")
    print(f"  My publications:  http://localhost:{port}/publications.html")
    print(f"  My interests:     http://localhost:{port}/interests.html")
    print("Press Ctrl+C to stop")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nServer stopped")


if __name__ == '__main__':
    run_server()
