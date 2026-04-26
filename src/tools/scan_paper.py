"""Tool 1: Quick relevance scan of a single paper."""

from __future__ import annotations

import logging
import time

import arxiv
from openai import APIConnectionError, InternalServerError, RateLimitError

from ..config import Settings
from ..llm_client import create_client, chat_completion
from ..storage import insert_paper, paper_exists
from ..tree import format_tree_for_prompt
from .json_utils import parse_llm_json
from .prompts import SCAN_PAPER_PROMPT
from .types import ScanResult, TreeLink

logger = logging.getLogger(__name__)


def scan_paper(
    arxiv_id: str,
    settings: Settings,
    db_conn,
) -> ScanResult:
    """Perform a quick relevance scan of a single paper.

    Args:
        arxiv_id: The arXiv identifier.
        settings: Application settings.
        db_conn: SQLite database connection.

    Returns:
        ScanResult with quality score, tree links, and reading recommendation.
    """
    # 1. Fetch metadata from arXiv API
    logger.info("Scanning paper %s", arxiv_id)
    search = arxiv.Search(id_list=[arxiv_id])
    results = list(search.results())

    if not results:
        raise ValueError(f"Paper {arxiv_id} not found on arXiv")

    entry = results[0]

    # Ensure paper exists in DB
    if not paper_exists(db_conn, arxiv_id):
        insert_paper(db_conn, {
            "arxiv_id": arxiv_id,
            "title": entry.title,
            "authors": ", ".join(str(a) for a in entry.authors),
            "abstract": entry.summary,
            "published": entry.published.isoformat() if entry.published else "",
            "categories": ", ".join(entry.categories),
            "primary_category": entry.primary_category,
            "pdf_url": entry.pdf_url,
            "entry_url": entry.entry_id,
        })

    # 2. Load knowledge tree
    tree_prompt = format_tree_for_prompt(db_conn)

    # 3. Build user prompt
    authors_str = ", ".join(str(a) for a in entry.authors[:3])
    if len(entry.authors) > 3:
        authors_str += f" et al. ({len(entry.authors)} authors)"

    user_prompt = (
        f"## Paper\n\n"
        f"**Title:** {entry.title}\n\n"
        f"**Authors:** {authors_str}\n\n"
        f"**Categories:** {', '.join(entry.categories)}\n\n"
        f"**Abstract:**\n{entry.summary}\n\n"
        f"## Knowledge Tree\n\n{tree_prompt}"
    )

    # 4. Call LLM with retry
    client = create_client(settings)
    max_retries = 3
    base_delay = 15
    response_text = None

    for attempt in range(max_retries + 1):
        try:
            response_text = chat_completion(
                client=client,
                model=settings.llm_model,
                system_prompt=SCAN_PAPER_PROMPT,
                user_prompt=user_prompt,
                temperature=settings.llm_temperature,
            )
            break
        except (RateLimitError, InternalServerError, APIConnectionError) as e:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning("LLM error, retrying in %ds (attempt %d/%d): %s", delay, attempt + 1, max_retries, type(e).__name__)
                time.sleep(delay)
            else:
                logger.exception("LLM error, all retries exhausted")
        except Exception:
            logger.exception("LLM call failed for scan_paper")
            break

    if response_text is None:
        raise RuntimeError(f"Failed to get LLM response for scan of {arxiv_id}")

    # 5. Parse response
    parsed = parse_llm_json(response_text)

    quality_score = max(1, min(5, int(parsed.get("quality_score", 1))))
    quality_reason = parsed.get("quality_reason", "")

    tree_links = []
    for link in parsed.get("tree_links", []):
        relevance = max(1, min(5, int(link.get("relevance_score", 1))))
        tree_links.append(TreeLink(
            node_name=link["node_name"],
            relevance_score=relevance,
            relevance_reason=link.get("relevance_reason", ""),
        ))

    return ScanResult(
        arxiv_id=arxiv_id,
        title=entry.title,
        quality_score=quality_score,
        quality_reason=quality_reason,
        tree_links=tree_links,
        recommend_reading=parsed.get("recommend_reading", False),
        rationale=parsed.get("rationale", ""),
    )
