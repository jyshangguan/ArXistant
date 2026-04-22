"""Fetch papers from arXiv by category."""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import arxiv
import requests
from bs4 import BeautifulSoup

from .config import Settings, Topic

logger = logging.getLogger(__name__)


@dataclass
class RawPaper:
    arxiv_id: str
    title: str
    authors: list[str]
    abstract: str
    published: datetime
    categories: list[str]
    primary_category: str
    pdf_url: str
    entry_url: str


def _parse_entry(entry: arxiv.Result) -> RawPaper:
    """Convert an arxiv.Result to a RawPaper."""
    authors = [a.name for a in entry.authors]
    arxiv_id = entry.entry_id.split("/abs/")[-1]
    return RawPaper(
        arxiv_id=arxiv_id,
        title=entry.title.strip(),
        authors=authors,
        abstract=entry.summary.strip(),
        published=entry.published,
        categories=entry.categories,
        primary_category=entry.primary_category,
        pdf_url=entry.pdf_url,
        entry_url=entry.entry_id,
    )


def _fetch_listing_ids(category: str, target_date: datetime) -> list[str]:
    """Fetch arXiv paper IDs from the listing page for a specific date.

    Uses arXiv's recent listing page with show=2000 to get all recent papers,
    then extracts IDs only from the section matching target_date.
    Returns list of versionless arXiv IDs.
    """
    # arXiv headings use "DD Mon YYYY" format, e.g. "22 Apr 2026"
    date_label = target_date.strftime('%d %b %Y')
    iso_date = target_date.strftime('%Y-%m-%d')
    url = f"https://arxiv.org/list/{category}/recent?show=2000"

    try:
        resp = requests.get(url, timeout=15)
        if resp.status_code != 200:
            logger.warning("Failed to fetch listing page for %s: HTTP %d",
                           category, resp.status_code)
            return []
    except Exception:
        logger.exception("Failed to fetch listing page for %s", category)
        return []

    soup = BeautifulSoup(resp.text, 'lxml')

    # Find the <h3> heading that matches the target date, e.g.
    # "Wed, 22 Apr 2026 (showing 33 of 33 entries )"
    target_heading = None
    for h3 in soup.find_all('h3'):
        if date_label in h3.get_text():
            target_heading = h3
            break

    if target_heading is None:
        logger.info("No listing section found for %s on %s", category, iso_date)
        return []

    # Collect paper IDs from all <dt> tags between this heading and the next one
    ids = []
    for sibling in target_heading.find_next_siblings():
        if sibling.name == 'h3':
            break  # reached the next date section
        if sibling.name == 'dt':
            a = sibling.find('a', title='Abstract')
            if a:
                href = a.get('href', '')
                arxiv_id = href.split('/abs/')[-1].strip()
                # Strip version suffix (e.g. "2510.12345v1" → "2510.12345")
                arxiv_id = re.sub(r'v\d+$', '', arxiv_id)
                if arxiv_id:
                    ids.append(arxiv_id)

    logger.info("Found %d papers for %s on %s from listing page",
                len(ids), category, iso_date)
    return ids


def _collect_papers_by_date(
    categories: list[str],
    target_date: datetime,
) -> list[RawPaper]:
    """Collect papers for a specific date using arXiv listing pages.

    1. Scrape listing pages to get arXiv IDs (one HTTP request per category).
    2. Fetch full metadata via the arXiv API id_list endpoint.
    """
    target_date = target_date.replace(tzinfo=timezone.utc)

    # 1. Gather all arXiv IDs from listing pages
    all_ids: list[str] = []
    for cat in categories:
        ids = _fetch_listing_ids(cat, target_date)
        all_ids.extend(ids)

    if not all_ids:
        date_str = target_date.strftime('%Y-%m-%d')
        logger.info("No papers found on listing pages for %s", date_str)
        return []

    # 2. Fetch full metadata from API (in batches of 200)
    logger.info("Fetching metadata for %d papers from arXiv API", len(all_ids))
    papers: list[RawPaper] = []
    seen_ids: set[str] = set()
    batch_size = 200

    for i in range(0, len(all_ids), batch_size):
        batch = all_ids[i:i + batch_size]
        try:
            client = arxiv.Client()
            search = arxiv.Search(id_list=batch)
            for entry in client.results(search):
                paper = _parse_entry(entry)
                if paper.arxiv_id not in seen_ids:
                    seen_ids.add(paper.arxiv_id)
                    papers.append(paper)
        except Exception:
            logger.exception("Failed to fetch metadata for batch starting at %d", i)

    logger.info("Total papers collected: %d", len(papers))
    return papers


def collect_papers(
    topics: list[Topic],
    settings: Settings,
    target_date: datetime | None = None,
) -> list[RawPaper]:
    """Fetch papers from arXiv for all configured topic categories.

    Default mode (no target_date): fetches recent papers sorted by submission
    date, filtered client-side by the days_back window.

    Specific-date mode (target_date): uses arXiv listing pages to get paper
    IDs for that date, then fetches full metadata via the API. Much faster
    than paginating through the search API for past dates.
    """
    all_categories = sorted(set(cat for t in topics for cat in t.categories))

    if target_date is not None:
        return _collect_papers_by_date(all_categories, target_date)

    # Default mode: recent papers via search API
    seen_ids: set[str] = set()
    papers: list[RawPaper] = []
    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.days_back)

    for cat in all_categories:
        logger.info("Fetching papers for category: %s", cat)

        try:
            client = arxiv.Client()
            search = arxiv.Search(
                query=f"cat:{cat}",
                max_results=settings.max_results,
                sort_by=arxiv.SortCriterion.SubmittedDate,
                sort_order=arxiv.SortOrder.Descending,
            )
            count = 0
            for entry in client.results(search):
                pub = entry.published.replace(tzinfo=timezone.utc)
                if pub < cutoff:
                    break
                paper = _parse_entry(entry)
                if paper.arxiv_id not in seen_ids:
                    seen_ids.add(paper.arxiv_id)
                    papers.append(paper)
                    count += 1
            logger.info("Fetched %d papers from %s (total unique: %d)", count, cat, len(papers))
        except Exception:
            logger.exception("Failed to fetch papers for category: %s", cat)

    logger.info("Total papers collected: %d", len(papers))
    return papers
