"""Fetch papers from arXiv by category."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

import arxiv

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


def collect_papers(topics: list[Topic], settings: Settings) -> list[RawPaper]:
    """Fetch papers from arXiv for all configured topic categories.

    Fetches recent papers sorted by submission date and filters client-side
    by the configured days_back window. Deduplicates by arxiv_id.
    """
    seen_ids: set[str] = set()
    papers: list[RawPaper] = []

    cutoff = datetime.now(timezone.utc) - timedelta(days=settings.days_back)

    # Collect all unique categories from all topics
    all_categories = sorted(set(cat for t in topics for cat in t.categories))

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
                # Client-side date filter: stop once we're past the cutoff
                if entry.published.replace(tzinfo=timezone.utc) < cutoff:
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
