#!/usr/bin/env python3
"""
Daily arXiv Astro-ph Paper Fetcher and Ranker - HTML Output with Toggle Abstracts

Usage:
    python src/arxiv_daily_ranker_html.py --interests-file local/interests.txt --output local/output.html
"""

import argparse
import json
import os
import re
import time
import urllib.error
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import html as html_module

from arxistant_paths import data_path


def _fetch_url(url, timeout=45, attempts=3):
    """Fetch a URL with bounded retries and useful arXiv error reporting."""
    last_error = "unknown network error"
    for attempt in range(1, attempts + 1):
        try:
            request = urllib.request.Request(
                url, headers={'User-Agent': 'ArXistant/0.1.1 (personal arXiv reader)'}
            )
            with urllib.request.urlopen(request, timeout=timeout) as response:
                body = response.read()
                charset = response.headers.get_content_charset() or 'utf-8'
                text = body.decode(charset, errors='replace')
                if text.strip():
                    return text
                last_error = "server returned an empty response"
        except urllib.error.HTTPError as error:
            detail = error.read(200).decode('utf-8', errors='replace').strip()
            last_error = f"HTTP {error.code}: {detail or error.reason}"
        except (urllib.error.URLError, TimeoutError, OSError) as error:
            last_error = str(getattr(error, 'reason', error))
        if attempt < attempts:
            time.sleep(3 * attempt)
    raise RuntimeError(
        f"arXiv did not respond after {attempts} attempts ({last_error}). "
        "Please wait a few minutes and refresh again."
    )


SECTION_ORDER = ['New submissions', 'Cross submissions', 'Replacement submissions']


def get_arxiv_release_date(dt=None):
    """Return the most recent arXiv release date (Mon-Fri)."""
    if dt is None:
        dt = datetime.now()
    weekday = dt.weekday()  # Monday=0, Sunday=6
    if weekday < 5:  # Mon-Fri
        return dt.strftime('%Y-%m-%d')
    # Saturday or Sunday: go back to Friday
    days_back = weekday - 4
    friday = dt - timedelta(days=days_back)
    return friday.strftime('%Y-%m-%d')


SAVE_BUTTON_SCRIPT = """<script>
async function togglePaper(arxivId, title, authors, abstract, score, dateFetched) {
    const btn = document.getElementById('save-btn-' + arxivId);
    const isSaved = btn.dataset.saved === 'true';

    if (isSaved) {
        if (!confirm('Remove this paper from your database?')) return;
        try {
            const resp = await fetch('http://localhost:8765/api/delete', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({ arxiv_id: arxivId })
            });
            const data = await resp.json();
            if (data.success) {
                btn.textContent = '💾';
                btn.title = 'Save to database';
                btn.style.background = '#b31b1b';
                btn.dataset.saved = 'false';
                fetch('http://localhost:8765/api/regenerate-interests', {method: 'POST'}).catch(() => {});
            } else {
                btn.textContent = '✗ Error';
                btn.style.background = '#c62828';
            }
        } catch (e) {
            if (window.location.protocol === 'file:') {
                alert('Save button requires viewing via http://localhost:8765/ (server not accessible from file://).');
            } else {
                btn.textContent = '✗ Error';
                btn.style.background = '#c62828';
            }
        }
    } else {
        try {
            const resp = await fetch('http://localhost:8765/api/save', {
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
                btn.title = 'Saved to database';
                btn.style.background = '#2e7d32';
                btn.dataset.saved = 'true';
            } else {
                btn.textContent = '✗ Error';
                btn.style.background = '#c62828';
            }
        } catch (e) {
            if (window.location.protocol === 'file:') {
                alert('Save button requires viewing via http://localhost:8765/ (server not accessible from file://).');
            } else {
                btn.textContent = '✗ Error';
                btn.style.background = '#c62828';
            }
        }
    }
}

document.addEventListener('DOMContentLoaded', async () => {
    let savedIds = new Set();
    try {
        const resp = await fetch('http://localhost:8765/api/papers');
        const data = await resp.json();
        savedIds = new Set(data.papers.map(p => p.arxiv_id));
    } catch (e) {
        console.warn('Could not fetch saved papers (server may be off or CORS from file://):', e);
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
            btn.title = 'Saved to database';
            btn.style.background = '#2e7d32';
            btn.dataset.saved = 'true';
        } else {
            btn.textContent = '💾';
            btn.title = 'Save to database';
            btn.style.background = '#b31b1b';
            btn.dataset.saved = 'false';
        }
        btn.onclick = () => togglePaper(arxivId, title, authors, abstract, score, dateFetched);
        paper.appendChild(btn);
    });
});
</script>
<!-- save-button-embedded -->"""


def fetch_arxiv_papers_by_date(date_str):
    """Fetch astro-ph papers submitted on the given date (YYYYMMDD)."""
    start = f"{date_str}0000"
    end = f"{date_str}2359"
    url = (
        f"https://export.arxiv.org/api/query?"
        f"search_query=cat:astro-ph*+AND+submittedDate:[{start}+TO+{end}]"
        f"&start=0&max_results=200&sortBy=submittedDate&sortOrder=descending"
    )
    
    data = _fetch_url(url)
    
    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    root = ET.fromstring(data)
    entries = root.findall('atom:entry', ns)
    
    papers = []
    for entry in entries:
        id_elem = entry.find('atom:id', ns)
        title_elem = entry.find('atom:title', ns)
        summary_elem = entry.find('atom:summary', ns)
        
        arxiv_id = id_elem.text if id_elem is not None else ''
        short_id = arxiv_id.split('/')[-1].replace('abs/', '') if arxiv_id else ''
        short_id = re.sub(r'v\d+$', '', short_id)
        
        title = title_elem.text.strip() if title_elem is not None else ''
        abstract = summary_elem.text.strip() if summary_elem is not None else ''
        
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
            'section': 'New submissions'
        })
    
    return papers


def _plain_html(fragment):
    """Convert a small arXiv listing fragment to normalized plain text."""
    without_tags = re.sub(r'<[^>]+>', ' ', fragment)
    return re.sub(r'\s+', ' ', html_module.unescape(without_tags)).strip()


def parse_arxiv_listing(page_html, recent=False):
    """Parse complete paper metadata directly from an arXiv listing page."""
    item_section_map = {}
    current_section = 'Recent submissions' if recent else 'New submissions'
    for match in re.finditer(
        r'(?:<h3>(New submissions|Cross submissions|Replacement submissions)[^<]*</h3>)'
        r'|(?:<a name=["\']item(\d+)["\'])',
        page_html
    ):
        section_name = match.group(1)
        item_num = match.group(2)
        if section_name:
            current_section = section_name
        elif item_num:
            item_section_map[int(item_num)] = current_section

    papers = []
    seen_ids = set()
    blocks = re.finditer(
        r'<dt>(?P<dt>.*?)</dt>\s*<dd>(?P<dd>.*?)</dd>',
        page_html, re.DOTALL | re.IGNORECASE
    )
    for item_num, block in enumerate(blocks, start=1):
        id_match = re.search(r'href\s*=\s*["\']/abs/(\d{4}\.\d{5,})', block.group('dt'))
        if not id_match or id_match.group(1) in seen_ids:
            continue
        paper_id = id_match.group(1)
        seen_ids.add(paper_id)
        metadata = block.group('dd')
        title_match = re.search(
            r'<div class=["\']list-title[^"\']*["\']>.*?</span>(.*?)</div>',
            metadata, re.DOTALL | re.IGNORECASE
        )
        authors_match = re.search(
            r'<div class=["\']list-authors["\']>(.*?)</div>',
            metadata, re.DOTALL | re.IGNORECASE
        )
        abstract_match = re.search(
            r'<p class=["\']mathjax["\']>(.*?)</p>',
            metadata, re.DOTALL | re.IGNORECASE
        )
        author_names = (
            [_plain_html(name) for name in re.findall(r'<a\b[^>]*>(.*?)</a>',
                                                       authors_match.group(1),
                                                       re.DOTALL | re.IGNORECASE)]
            if authors_match else []
        )
        papers.append({
            'id': paper_id,
            'title': _plain_html(title_match.group(1)) if title_match else '',
            'authors': author_names,
            'abstract': _plain_html(abstract_match.group(1)) if abstract_match else '',
            'section': ('Recent submissions' if recent else
                        item_section_map.get(item_num, 'New submissions')),
        })
    return papers


def fetch_arxiv_papers(date_str=None, recent=False):
    """Fetch astro-ph papers from a listing page, or the API for an exact date."""
    if date_str is not None:
        return fetch_arxiv_papers_by_date(date_str)

    page_url = (
        "https://arxiv.org/list/astro-ph/recent?show=2000"
        if recent else "https://arxiv.org/list/astro-ph/new"
    )
    page_html = _fetch_url(page_url)
    papers = parse_arxiv_listing(page_html, recent=recent)
    if not papers:
        if recent:
            print("Warning: no papers found on arXiv recent page")
            return []
        print("Warning: no papers found on arXiv new page; falling back to yesterday's API query")
        yesterday = datetime.now() - timedelta(days=1)
        return fetch_arxiv_papers_by_date(yesterday.strftime('%Y%m%d'))

    print(f"Found {len(papers)} papers on arXiv {'recent' if recent else 'new'} page")
    section_counts = {}
    for paper in papers:
        section = paper['section']
        section_counts[section] = section_counts.get(section, 0) + 1
    for section, count in section_counts.items():
        print(f"  {section}: {count}")
    return papers


def rank_papers(papers):
    """Rank papers using ML-based scoring, normalized to 0-100."""
    try:
        import arxiv_ml_ranker
        scored = arxiv_ml_ranker.score_papers(papers)
        if scored:
            probs = [s[0] for s in scored]
            max_p = max(probs)
            min_p = min(probs)
            range_p = max_p - min_p if max_p > min_p else 1.0
            normalized = []
            for prob, paper in scored:
                norm_score = round(((prob - min_p) / range_p) * 100) if range_p > 0 else 0
                raw_score = round(prob * 100)
                normalized.append((norm_score, raw_score, paper))
            return normalized
    except Exception as e:
        print(f"WARNING: ML scoring failed ({e}).")
    
    return [(0, 0, p) for p in papers]


def escape_html(text):
    """Escape HTML special characters."""
    return html_module.escape(text)


def format_paper_list_html(scored_papers, date_str=None, page_type='new'):
    """Generate HTML output with toggle-abstract buttons, grouped by section."""
    generated_at = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

    if page_type == 'recent':
        if date_str is None:
            date_str = datetime.now().strftime('%Y-%m-%d')
        title_text = f'arXiv Astro-ph Recent Papers — {date_str}'
        h1_text = f'arXiv Astro-ph Recent Papers — {date_str}'
        nav_extra = '    <a class="nav-btn-secondary" href="http://localhost:8765/daily.html"><span class="icon">📅</span><span class="label">Daily Papers</span></a>'
        refresh_onclick = 'refreshRecent()'
    else:
        # Use most recent arXiv release date (weekdays only)
        display_date = get_arxiv_release_date()
        title_text = f'arXiv Astro-ph New Papers — {display_date}'
        h1_text = f'arXiv Astro-ph New Papers — {display_date}'
        nav_extra = '    <a class="nav-btn-secondary" href="http://localhost:8765/recent.html"><span class="icon">📅</span><span class="label">Recent Papers</span></a>'
        refresh_onclick = 'refreshDaily()'

    # Group papers by section
    sections = {}
    for norm_score, raw_score, paper in scored_papers:
        section = paper.get('section', 'New submissions')
        if section not in sections:
            sections[section] = []
        sections[section].append((norm_score, raw_score, paper))

    lines = []
    lines.append('<!DOCTYPE html>')
    lines.append('<html lang="en">')
    lines.append('<head>')
    lines.append('  <meta charset="UTF-8">')
    lines.append('  <meta name="viewport" content="width=device-width, initial-scale=1.0">')
    lines.append(f'  <title>{title_text}</title>')
    lines.append('  <style>')
    lines.append('    body { font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif; max-width: 900px; margin: 0 auto; padding: 20px; line-height: 1.6; color: #333; }')
    lines.append('    h1 { color: #1a1a1a; border-bottom: 2px solid #b31b1b; padding-bottom: 10px; }')
    lines.append('    h2 { font-size: 1.1em; margin-top: 0; }')
    lines.append('    h2 a { color: #b31b1b; text-decoration: none; }')
    lines.append('    h2 a:hover { text-decoration: underline; }')
    lines.append('    .paper { border: 1px solid #e0e0e0; border-radius: 8px; padding: 16px; margin-bottom: 16px; background: #fafafa; }')
    lines.append('    .paper:hover { background: #f5f5f5; }')
    lines.append('    .score { display: inline-block; background: #b31b1b; color: white; padding: 2px 8px; border-radius: 12px; font-size: 0.85em; font-weight: bold; margin-left: 8px; }')
    lines.append('    .authors { color: #555; font-size: 0.95em; margin: 8px 0; }')
    lines.append('    .et-al { color: #888; }')
    lines.append('    .abstract-btn { cursor: pointer; color: #b31b1b; font-size: 0.9em; font-weight: bold; background: none; border: none; padding: 0; margin-top: 8px; }')
    lines.append('    .abstract-btn:hover { text-decoration: underline; }')
    lines.append('    .abstract-full { display: none; color: #333; font-size: 0.95em; margin-top: 8px; padding-top: 8px; border-top: 1px dashed #ccc; }')
    lines.append('    .meta { font-size: 0.85em; color: #888; margin-top: 4px; }')
    lines.append('    .total { color: #666; margin-bottom: 20px; }')
    lines.append('    .section-header { color: #1a1a1a; font-size: 1.4em; font-weight: bold; margin-top: 30px; margin-bottom: 15px; padding-bottom: 8px; border-bottom: 2px solid #b31b1b; }')
    lines.append('    .arxiv-id { color: #666; font-size: 0.85em; font-weight: normal; }')
    lines.append('    .arxiv-id a { color: #666; text-decoration: none; }')
    lines.append('    .arxiv-id a:hover { text-decoration: underline; }')
    lines.append('    .nav-bar { display: flex; gap: 12px; margin: 12px 0 20px 0; flex-wrap: wrap; }')
    lines.append('    .nav-bar-bottom { display: flex; gap: 12px; margin: 30px 0 10px 0; flex-wrap: wrap; }')
    lines.append('    .nav-btn { display: inline-block; padding: 6px 10px; background: #b31b1b; color: white; text-decoration: none; border-radius: 6px; font-size: 1em; font-weight: 500; transition: background 0.2s, padding 0.5s ease; overflow: hidden; white-space: nowrap; }')
    lines.append('    .nav-btn:hover { background: #8a1515; padding: 6px 14px; }')
    lines.append('    .nav-btn-secondary { display: inline-block; padding: 6px 10px; background: #555; color: white; text-decoration: none; border-radius: 6px; font-size: 1em; font-weight: 500; transition: background 0.2s, padding 0.5s ease; overflow: hidden; white-space: nowrap; }')
    lines.append('    .nav-btn-secondary:hover { background: #333; padding: 6px 14px; }')
    lines.append('    .nav-btn .icon, .nav-btn-secondary .icon, .refresh-btn .icon { display: inline-block; vertical-align: middle; font-size: 1.2em; }')
    lines.append('    .nav-btn .label, .nav-btn-secondary .label, .refresh-btn .label { display: inline-block; max-width: 0; overflow: hidden; white-space: nowrap; vertical-align: middle; opacity: 0; transition: max-width 0.5s ease, opacity 0.5s ease, padding 0.5s ease; padding-left: 0; }')
    lines.append('    .nav-btn:hover .label, .nav-btn-secondary:hover .label, .refresh-btn:hover .label { max-width: 200px; opacity: 1; padding-left: 6px; }')
    lines.append('    .scroll-top { position: fixed; bottom: 20px; right: 20px; padding: 10px 16px; background: #b31b1b; color: white; text-decoration: none; border-radius: 50%; font-size: 1.1em; font-weight: bold; cursor: pointer; border: none; box-shadow: 0 2px 8px rgba(0,0,0,0.3); z-index: 1000; transition: background 0.2s; }')
    lines.append('    .scroll-top:hover { background: #8a1515; }')
    lines.append('    .save-btn { margin-top: 6px; padding: 1px 6px; color: white; border: none; border-radius: 3px; cursor: pointer; font-size: 0.7em; white-space: nowrap; transition: background 0.2s; }')
    lines.append('    .save-btn:hover { opacity: 0.9; }')
    lines.append('    .refresh-btn { display: inline-block; padding: 6px 10px; background: #1976d2; color: white; border: none; border-radius: 6px; font-size: 1em; font-weight: 500; cursor: pointer; transition: background 0.2s, padding 0.5s ease; overflow: hidden; white-space: nowrap; }')
    lines.append('    .refresh-btn:hover { background: #1565c0; padding: 6px 14px; }')
    lines.append('    .refresh-btn:disabled { background: #90a4ae; cursor: not-allowed; }')
    lines.append('    .footnote { font-size: 0.8em; color: #888; margin-top: 30px; padding-top: 10px; border-top: 1px solid #e0e0e0; }')
    lines.append('  </style>')
    lines.append('</head>')
    lines.append('<body>')
    lines.append('  <div class="nav-bar">')
    lines.append('    <button class="refresh-btn" id="refreshBtn" onclick="' + refresh_onclick + '"><span class="icon">🔄</span><span class="label">Refresh</span></button>')
    lines.append(nav_extra)
    lines.append('    <a class="nav-btn-secondary" href="http://localhost:8765/search-arxiv.html"><span class="icon">🔍</span><span class="label">Search arXiv</span></a>')
    lines.append('    <a class="nav-btn-secondary" href="http://localhost:8765/chat.html"><span class="icon">💬</span><span class="label">Chat</span></a>')
    lines.append('    <a class="nav-btn-secondary" href="http://localhost:8765/database.html"><span class="icon">📂</span><span class="label">Saved Papers</span></a>')
    lines.append('    <a class="nav-btn-secondary" href="http://localhost:8765/publications.html"><span class="icon">📚</span><span class="label">My Publications</span></a>')
    lines.append('    <a class="nav-btn-secondary" href="http://localhost:8765/ml-features.html"><span class="icon">🧠</span><span class="label">ML Features</span></a>')
    lines.append('  </div>')
    lines.append(f'  <h1>{h1_text}</h1>')
    lines.append(f'  <p class="total">Total papers: {len(scored_papers)} <span class="footnote" style="margin-left: 12px; border: none; padding: 0;">(generated: {generated_at})</span></p>')

    # Build ordered section list: SECTION_ORDER first, then any remaining sections
    ordered_sections = [s for s in SECTION_ORDER if s in sections]
    for s in sections:
        if s not in ordered_sections:
            ordered_sections.append(s)

    for section in ordered_sections:
        section_papers = sections[section]
        lines.append(f'  <h2 class="section-header">{section} ({len(section_papers)} entries)</h2>')

        for i, (norm_score, raw_score, paper) in enumerate(section_papers, 1):
            arxiv_url = f"https://arxiv.org/abs/{paper['id']}"
            title_escaped = escape_html(paper['title'])
            abstract_escaped = escape_html(paper['abstract'])

            authors = paper['authors']
            if len(authors) <= 5:
                author_str = ", ".join(authors)
            else:
                first_five = ", ".join(authors[:5])
                author_str = f'{first_five}<span class="et-al">, et al. ({len(authors)} authors)</span>'

            abstract_id = f"abs-{paper['id']}".replace('.', '_')

            lines.append('  <div class="paper">')
            lines.append(f'    <h2>{i}. <span class="arxiv-id"><a href="{arxiv_url}" target="_blank">arXiv:{paper["id"]}</a></span> — <a href="https://alphaxiv.org/abs/{paper["id"]}" target="_blank">{title_escaped}</a></h2>')
            if norm_score > 0:
                lines.append(f'    <span class="score">Relevance: {norm_score}/100</span>')
            lines.append(f'    <p class="authors"><strong>Authors:</strong> {author_str}</p>')
            lines.append(f'    <button class="abstract-btn" onclick="document.getElementById(\'{abstract_id}\').style.display = (document.getElementById(\'{abstract_id}\').style.display === \'block\' ? \'none\' : \'block\'); this.textContent = (document.getElementById(\'{abstract_id}\').style.display === \'block\' ? \'▾ Hide abstract\' : \'▸ Show abstract\');">▸ Show abstract</button>')
            lines.append(f'    <p class="abstract-full" id="{abstract_id}">{abstract_escaped}</p>')
            lines.append('  </div>')

    lines.append('  <div class="nav-bar-bottom">')
    lines.append('    <a class="nav-btn-secondary" href="http://localhost:8765/search-arxiv.html"><span class="icon">🔍</span><span class="label">Search arXiv</span></a>')
    lines.append('    <a class="nav-btn-secondary" href="http://localhost:8765/chat.html"><span class="icon">💬</span><span class="label">Chat</span></a>')
    lines.append('    <a class="nav-btn-secondary" href="http://localhost:8765/database.html"><span class="icon">📂</span><span class="label">Saved Papers</span></a>')
    lines.append('    <a class="nav-btn-secondary" href="http://localhost:8765/publications.html"><span class="icon">📚</span><span class="label">My Publications</span></a>')
    lines.append('    <a class="nav-btn-secondary" href="http://localhost:8765/ml-features.html"><span class="icon">🧠</span><span class="label">ML Features</span></a>')
    lines.append(nav_extra)
    lines.append('  </div>')
    lines.append('  <button class="scroll-top" onclick="window.scrollTo({top: 0, behavior: \'smooth\'})" title="To the top">▲</button>')
    lines.append('''<script>
async function refreshDaily() {
    if (!confirm('Re-fetch and re-rank papers from arXiv?')) return;
    const btn = document.getElementById('refreshBtn');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<span class="icon">⏳</span><span class="label">Refreshing...</span>';
    btn.disabled = true;
    try {
        const resp = await fetch('/api/refresh-daily', {method: 'POST'});
        const data = await resp.json();
        if (data.success) {
            window.location.reload();
        } else {
            alert('Refresh failed: ' + (data.error || 'Unknown error'));
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    } catch (e) {
        alert('Refresh failed: ' + e.message);
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}
async function refreshRecent() {
    if (!confirm('Re-fetch and re-rank recent papers from arXiv?')) return;
    const btn = document.getElementById('refreshBtn');
    const originalText = btn.innerHTML;
    btn.innerHTML = '<span class="icon">⏳</span><span class="label">Refreshing...</span>';
    btn.disabled = true;
    try {
        const resp = await fetch('/api/refresh-recent', {method: 'POST'});
        const data = await resp.json();
        if (data.success) {
            window.location.reload();
        } else {
            alert('Refresh failed: ' + (data.error || 'Unknown error'));
            btn.innerHTML = originalText;
            btn.disabled = false;
        }
    } catch (e) {
        alert('Refresh failed: ' + e.message);
        btn.innerHTML = originalText;
        btn.disabled = false;
    }
}
</script>''')
    lines.append(SAVE_BUTTON_SCRIPT)
    lines.append('</body>')
    lines.append('</html>')

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description='Fetch and rank arXiv astro-ph papers')
    parser.add_argument('--recent', action='store_true', help='Fetch from arXiv recent page (last ~5 days) instead of new page')
    parser.add_argument('--interests-file', default=data_path('interests.txt'), help='DEPRECATED: no longer used')
    parser.add_argument('--output', default=data_path('arxiv_ranked.html'), help='Output HTML file')
    parser.add_argument('--date', help='Date to fetch (YYYYMMDD), default yesterday')
    parser.add_argument('--json-output', help='Optional JSON output path')
    parser.add_argument('--ml', action='store_true', help='DEPRECATED: ML is always used')
    args = parser.parse_args()
    
    if args.recent:
        print("Fetching arXiv papers from recent page (last ~5 days)...")
        papers = fetch_arxiv_papers(recent=True)
    else:
        print(f"Fetching arXiv papers for: {args.date or 'today (new page)'}...")
        papers = fetch_arxiv_papers(args.date)
    print(f"Found {len(papers)} papers")
    
    if args.interests_file and args.interests_file != data_path('interests.txt'):
        print("NOTE: --interests-file is deprecated and ignored. ML scoring is always used.")
    
    scored_papers = rank_papers(papers)
    
    page_type = 'recent' if args.recent else 'new'
    html = format_paper_list_html(scored_papers, args.date, page_type=page_type)
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Saved ranked list to {args.output}")
    
    if args.json_output:
        with open(args.json_output, 'w', encoding='utf-8') as f:
            json.dump([{'score': s, 'raw_score': r, **p} for s, r, p in scored_papers], f, indent=2)
        print(f"Saved JSON to {args.json_output}")


if __name__ == '__main__':
    main()
