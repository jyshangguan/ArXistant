#!/usr/bin/env python3
"""
Daily arXiv Astro-ph Paper Fetcher and Ranker

Usage:
    python arxiv_daily_ranker.py --interests-file interests.txt --output output.md

The interests file should contain keywords, paper titles, or research topics
that describe your research interests, one per line or as paragraphs.
"""

import argparse
import json
import os
import re
import urllib.request
import xml.etree.ElementTree as ET
from datetime import datetime, timedelta


def fetch_arxiv_papers(date_str=None):
    """Fetch astro-ph papers submitted on the given date (YYYYMMDD)."""
    if date_str is None:
        # arXiv new listings are for papers submitted the previous day
        yesterday = datetime.now() - timedelta(days=1)
        date_str = yesterday.strftime('%Y%m%d')
    
    # Query for papers submitted on that date
    start = f"{date_str}0000"
    end = f"{date_str}2359"
    url = (
        f"http://export.arxiv.org/api/query?"
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
        # Remove version suffix for cleaner URL
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
            'abstract': abstract
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
    """Score a paper using weighted keyword matching."""
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
    """Rank papers by relevance score."""
    scored = []
    for paper in papers:
        score = score_paper(paper, interests)
        scored.append((score, paper))
    
    # Sort by score descending, then by arXiv ID ascending for stability
    scored.sort(key=lambda x: (-x[0], x[1]['id']))
    return scored


def format_paper_list(scored_papers, date_str=None):
    """Generate markdown output."""
    if date_str is None:
        date_str = datetime.now().strftime('%Y-%m-%d')
    
    lines = []
    lines.append(f"# arXiv Astro-ph New Papers — {date_str}")
    lines.append(f"\nTotal papers: {len(scored_papers)}\n")
    lines.append("---\n")
    
    for i, (score, paper) in enumerate(scored_papers, 1):
        arxiv_url = f"https://arxiv.org/abs/{paper['id']}"
        lines.append(f"### {i}. [{paper['title']}]({arxiv_url})")
        if score > 0:
            lines.append(f"\n**Relevance Score:** {score}")
        lines.append("")
        
        authors = paper['authors']
        if len(authors) <= 5:
            author_str = ", ".join(authors)
        else:
            first_five = ", ".join(authors[:5])
            author_str = f"{first_five}, et al. ({len(authors)} authors)"
        lines.append(f"**Authors:** {author_str}")
        lines.append("")
        
        abstract = paper['abstract']
        if len(abstract) > 300:
            folded = abstract[:300].rstrip() + " ..."
        else:
            folded = abstract
        lines.append(f"**Abstract:** {folded}")
        lines.append("")
        lines.append("---\n")
    
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description='Fetch and rank arXiv astro-ph papers')
    parser.add_argument('--interests-file', default='interests.txt', help='Path to interests file')
    parser.add_argument('--output', default='arxiv_ranked.md', help='Output markdown file')
    parser.add_argument('--date', help='Date to fetch (YYYYMMDD), default yesterday')
    parser.add_argument('--json-output', help='Optional JSON output path')
    args = parser.parse_args()
    
    print(f"Fetching arXiv papers for date: {args.date or 'yesterday'}...")
    papers = fetch_arxiv_papers(args.date)
    print(f"Found {len(papers)} papers")
    
    interests = load_interests(args.interests_file)
    print(f"Loaded {len(interests)} interest keywords from {args.interests_file}")
    
    scored_papers = rank_papers(papers, interests)
    
    md = format_paper_list(scored_papers, args.date)
    with open(args.output, 'w', encoding='utf-8') as f:
        f.write(md)
    print(f"Saved ranked list to {args.output}")
    
    if args.json_output:
        with open(args.json_output, 'w', encoding='utf-8') as f:
            json.dump([{'score': s, **p} for s, p in scored_papers], f, indent=2)
        print(f"Saved JSON to {args.json_output}")


if __name__ == '__main__':
    main()
