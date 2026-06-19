#!/usr/bin/env python3
"""
Daily arXiv Astro-ph Paper Fetcher and Ranker - HTML Output with Expandable Abstracts

Usage:
    python arxiv_daily_ranker_html.py --interests-file interests.txt --output output.html
"""

import argparse
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta
import html as html_module


SECTION_ORDER = ['New submissions', 'Cross submissions', 'Replacement submissions']


def fetch_arxiv_papers_by_date(date_str):
    """Fetch astro-ph papers submitted on the given date (YYYYMMDD)."""
    start = f"{date_str}0000"
    end = f"{date_str}2359"
    url = (
        f"https://export.arxiv.org/api/query?"
        f"search_query=cat:astro-ph*+AND+submittedDate:[{start}+TO+{end}]"
        f"&start=0&max_results=200&sortBy=submittedDate&sortOrder=descending"
    )
    
    req = urllib.request.Request(url, headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(req)
    data = resp.read().decode('utf-8')
    
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


def fetch_arxiv_papers(date_str=None):
    """Fetch astro-ph papers. If date_str is None, fetch from arXiv 'new' page.
    If date_str is provided, use submittedDate API query."""
    if date_str is not None:
        return fetch_arxiv_papers_by_date(date_str)
    
    # Fetch exact paper list from arXiv 'new' page
    new_page_url = "https://arxiv.org/list/astro-ph/new"
    req = urllib.request.Request(new_page_url, headers={'User-Agent': 'Mozilla/5.0'})
    resp = urllib.request.urlopen(req)
    html = resp.read().decode('utf-8')
    
    # Parse HTML to determine sections for each item
    item_section_map = {}
    current_section = 'New submissions'
    
    for match in re.finditer(
        r'(?:<h3>(New submissions|Cross submissions|Replacement submissions)[^<]*</h3>)'
        r'|(?:<a name=["\']item(\d+)["\'])',
        html
    ):
        section_name = match.group(1)
        item_num = match.group(2)
        if section_name:
            current_section = section_name
        elif item_num:
            item_section_map[int(item_num)] = current_section
    
    # Extract arXiv IDs in order, along with their item numbers and sections
    id_pattern = re.compile(r'href\s*=\s*"/abs/(\d{4}\.\d{5,})"')
    ids_with_items = []
    item_num = 1
    for match in id_pattern.finditer(html):
        paper_id = match.group(1)
        section = item_section_map.get(item_num, 'New submissions')
        ids_with_items.append((paper_id, item_num, section))
        item_num += 1
    
    if not ids_with_items:
        print("Warning: no paper IDs found on arXiv new page, falling back to yesterday's API query")
        yesterday = datetime.now() - timedelta(days=1)
        return fetch_arxiv_papers_by_date(yesterday.strftime('%Y%m%d'))
    
    print(f"Found {len(ids_with_items)} paper IDs on arXiv new page")
    section_counts = {}
    for _, _, section in ids_with_items:
        section_counts[section] = section_counts.get(section, 0) + 1
    for section, count in section_counts.items():
        print(f"  {section}: {count}")
    
    # Build ID → section lookup
    id_to_section = {pid: sec for pid, _, sec in ids_with_items}
    
    # Fetch metadata via API in batches
    papers = []
    batch_size = 100
    ns = {'atom': 'http://www.w3.org/2005/Atom'}
    
    all_ids = [pid for pid, _, _ in ids_with_items]
    for i in range(0, len(all_ids), batch_size):
        batch = all_ids[i:i+batch_size]
        id_list = ','.join(batch)
        api_url = (
            f"https://export.arxiv.org/api/query?"
            f"id_list={id_list}&max_results={len(batch)}"
        )
        req = urllib.request.Request(api_url, headers={'User-Agent': 'Mozilla/5.0'})
        resp = urllib.request.urlopen(req)
        data = resp.read().decode('utf-8')
        
        root = ET.fromstring(data)
        entries = root.findall('atom:entry', ns)
        
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
            
            section = id_to_section.get(short_id, 'New submissions')
            
            papers.append({
                'id': short_id,
                'title': title,
                'authors': authors,
                'abstract': abstract,
                'section': section
            })
    
    return papers


def load_interests(interests_file):
    """Load weighted interests from a text file. Returns list of (keyword, weight) tuples."""
    if not os.path.exists(interests_file):
        return []
    interests = []
    with open(interests_file, 'r', encoding='utf-8') as f:
        for line in f:
            line = line.strip()
            if not line or line.startswith('#'):
                continue
            parts = line.split('\t')
            if len(parts) >= 2:
                keyword = parts[0].strip()
                try:
                    weight = int(parts[1].strip())
                except ValueError:
                    weight = 1
            else:
                keyword = line
                weight = 1
            if keyword:
                interests.append((keyword, weight))
    return interests


def score_paper(paper, interests):
    """Score a paper using weighted keyword matching (title=3×, abstract=1×)."""
    if not interests:
        return 0
    
    title_lower = paper['title'].lower()
    text_lower = (paper['title'] + ' ' + paper['abstract']).lower()
    score = 0
    
    for keyword, weight in interests:
        keyword_lower = keyword.lower()
        if keyword_lower in title_lower:
            score += 3 * weight
        elif keyword_lower in text_lower:
            score += 1 * weight
    
    return score


def rank_papers(papers, interests):
    """Rank papers by relevance score, normalized to 0-100."""
    scored = []
    for paper in papers:
        raw_score = score_paper(paper, interests)
        scored.append((raw_score, paper))
    
    # Normalize to 0-100 for comprehensibility
    max_score = max((s for s, _ in scored), default=0)
    normalized = []
    for raw_score, paper in scored:
        if max_score > 0:
            norm_score = round((raw_score / max_score) * 100)
        else:
            norm_score = 0
        normalized.append((norm_score, raw_score, paper))
    
    normalized.sort(key=lambda x: (-x[0], x[2]['id']))
    return normalized


def escape_html(text):
    """Escape HTML special characters."""
    return html_module.escape(text)


def format_paper_list_html(scored_papers, date_str=None):
    """Generate HTML output with toggle-abstract buttons, grouped by section."""
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')

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
    lines.append(f'  <title>arXiv Astro-ph New Papers — {date_str}</title>')
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
    lines.append('    .nav-btn { display: inline-block; padding: 8px 16px; background: #b31b1b; color: white; text-decoration: none; border-radius: 6px; font-size: 0.9em; font-weight: 500; transition: background 0.2s; }')
    lines.append('    .nav-btn:hover { background: #8a1515; }')
    lines.append('    .nav-btn-secondary { display: inline-block; padding: 8px 16px; background: #555; color: white; text-decoration: none; border-radius: 6px; font-size: 0.9em; font-weight: 500; transition: background 0.2s; }')
    lines.append('    .nav-btn-secondary:hover { background: #333; }')
    lines.append('    .scroll-top { position: fixed; bottom: 20px; right: 20px; padding: 10px 16px; background: #b31b1b; color: white; text-decoration: none; border-radius: 50%; font-size: 1.1em; font-weight: bold; cursor: pointer; border: none; box-shadow: 0 2px 8px rgba(0,0,0,0.3); z-index: 1000; transition: background 0.2s; }')
    lines.append('    .scroll-top:hover { background: #8a1515; }')
    lines.append('  </style>')
    lines.append('</head>')
    lines.append('<body>')
    lines.append(f'  <h1>arXiv Astro-ph New Papers — {date_str}</h1>')
    lines.append('  <div class="nav-bar">')
    lines.append('    <a class="nav-btn-secondary" href="http://localhost:8765/database.html" target="_blank">📚 My Database</a>')
    lines.append('    <a class="nav-btn-secondary" href="http://localhost:8765/publications.html" target="_blank">📄 My Publications</a>')
    lines.append('    <a class="nav-btn-secondary" href="http://localhost:8765/interests.html" target="_blank">⚙️ My Interests</a>')
    lines.append('  </div>')
    lines.append(f'  <p class="total">Total papers: {len(scored_papers)}</p>')

    for section in SECTION_ORDER:
        if section not in sections:
            continue
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
            lines.append(f'    <h2>{i}. <span class="arxiv-id"><a href="{arxiv_url}" target="_blank">arXiv:{paper["id"]}</a></span> — <a href="{arxiv_url}" target="_blank">{title_escaped}</a></h2>')
            if norm_score > 0:
                lines.append(f'    <span class="score">Relevance: {norm_score}/100</span>')
            lines.append(f'    <p class="authors"><strong>Authors:</strong> {author_str}</p>')
            lines.append(f'    <button class="abstract-btn" onclick="document.getElementById(\'{abstract_id}\').style.display = (document.getElementById(\'{abstract_id}\').style.display === \'block\' ? \'none\' : \'block\'); this.textContent = (document.getElementById(\'{abstract_id}\').style.display === \'block\' ? \'▾ Hide abstract\' : \'▸ Show abstract\');">▸ Show abstract</button>')
            lines.append(f'    <p class="abstract-full" id="{abstract_id}">{abstract_escaped}</p>')
            lines.append('  </div>')

    lines.append('  <div class="nav-bar-bottom">')
    lines.append('    <a class="nav-btn-secondary" href="http://localhost:8765/database.html" target="_blank">📚 My Database</a>')
    lines.append('    <a class="nav-btn-secondary" href="http://localhost:8765/publications.html" target="_blank">📄 My Publications</a>')
    lines.append('    <a class="nav-btn-secondary" href="http://localhost:8765/interests.html" target="_blank">⚙️ My Interests</a>')
    lines.append('  </div>')
    lines.append('  <button class="scroll-top" onclick="window.scrollTo({top: 0, behavior: \'smooth\'})" title="To the top">▲</button>')
    lines.append('</body>')
    lines.append('</html>')

    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description='Fetch and rank arXiv astro-ph papers')
    parser.add_argument('--interests-file', default='interests.txt', help='Path to interests file')
    parser.add_argument('--output', default='arxiv_ranked.html', help='Output HTML file')
    parser.add_argument('--date', help='Date to fetch (YYYYMMDD), default yesterday')
    parser.add_argument('--json-output', help='Optional JSON output path')
    args = parser.parse_args()
    
    print(f"Fetching arXiv papers for: {args.date or 'today (new page)'}...")
    papers = fetch_arxiv_papers(args.date)
    print(f"Found {len(papers)} papers")
    
    interests = load_interests(args.interests_file)
    print(f"Loaded {len(interests)} interest keywords from {args.interests_file}")
    
    scored_papers = rank_papers(papers, interests)
    
    html = format_paper_list_html(scored_papers, args.date)
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(html)
    print(f"Saved ranked list to {args.output}")
    
    if args.json_output:
        with open(args.json_output, 'w', encoding='utf-8') as f:
            json.dump([{'score': s, 'raw_score': r, **p} for s, r, p in scored_papers], f, indent=2)
        print(f"Saved JSON to {args.json_output}")


if __name__ == '__main__':
    main()
