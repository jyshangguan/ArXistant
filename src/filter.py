"""Filter papers by relevance using LLM scoring."""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass

from .config import Settings, Topic
from .collector import RawPaper
from .llm_client import OpenAI, chat_completion, create_client

logger = logging.getLogger(__name__)


@dataclass
class RelevantPaper:
    paper: RawPaper
    score: int          # 1-5
    matched_topic: str  # name of the best-matching topic
    reason: str         # brief explanation


SYSTEM_PROMPT = """\
You are a research assistant that evaluates arXiv papers for relevance to given research topics.

For each paper, assign:
- A relevance score from 1 to 5:
  1 = Not relevant at all
  2 = Tangentially related
  3 = Moderately relevant — worth a quick glance
  4 = Highly relevant — should read carefully
  5 = Directly on target — immediate priority
- The name of the best-matching topic
- A brief reason (one sentence)

Rules:
- Score based on the paper's title, authors, and abstract only.
- A paper may match zero topics. If none match well, give score 1 and set matched_topic to "none".
- Be honest — do not inflate scores. Most papers should be 1 or 2.
- Respond ONLY with a JSON array, no other text.

Response format:
[
  {
    "index": <0-based paper index>,
    "score": <1-5>,
    "matched_topic": "<topic name or 'none'>",
    "reason": "<brief explanation>"
  }
]
"""


def _format_topics(topics: list[Topic]) -> str:
    """Format topic definitions for the LLM prompt."""
    parts = []
    for i, t in enumerate(topics, 1):
        kw = ", ".join(t.keywords)
        parts.append(f"{i}. {t.name}\n   Description: {t.description}\n   Keywords: {kw}")
    return "\n\n".join(parts)


def _format_papers(papers: list[RawPaper]) -> str:
    """Format a batch of papers for the LLM prompt."""
    parts = []
    for i, p in enumerate(papers):
        authors_str = ", ".join(p.authors[:3])
        if len(p.authors) > 3:
            authors_str += f" et al. ({len(p.authors)} authors)"
        parts.append(
            f"[{i}] Title: {p.title}\n"
            f"    Authors: {authors_str}\n"
            f"    Categories: {', '.join(p.categories)}\n"
            f"    Abstract: {p.abstract}"
        )
    return "\n\n".join(parts)


def _parse_response(text: str) -> list[dict]:
    """Extract JSON array from the LLM response.

    Tries: direct JSON → code fence extraction → bracket extraction.
    """
    text = text.strip()

    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, list):
            return result
    except json.JSONDecodeError:
        pass

    # Try extracting from code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence_match:
        try:
            result = json.loads(fence_match.group(1).strip())
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    # Try finding first [ ... ] bracket pair
    bracket_match = re.search(r"\[.*\]", text, re.DOTALL)
    if bracket_match:
        try:
            result = json.loads(bracket_match.group(0))
            if isinstance(result, list):
                return result
        except json.JSONDecodeError:
            pass

    logger.warning("Could not parse LLM response as JSON array")
    return []


def filter_papers(
    papers: list[RawPaper],
    topics: list[Topic],
    settings: Settings,
) -> list[RelevantPaper]:
    """Score papers for relevance using the LLM in batches."""
    if not papers:
        return []

    client = create_client(settings)
    batch_size = settings.batch_size
    relevant: list[RelevantPaper] = []

    topics_block = _format_topics(topics)
    user_header = (
        f"## Research Topics\n\n{topics_block}\n\n"
        "## Papers to Evaluate\n\n"
    )

    total_batches = (len(papers) + batch_size - 1) // batch_size
    for batch_num in range(total_batches):
        start = batch_num * batch_size
        end = min(start + batch_size, len(papers))
        batch = papers[start:end]
        logger.info("Scoring batch %d/%d (papers %d-%d of %d)",
                     batch_num + 1, total_batches, start, end - 1, len(papers))

        papers_block = _format_papers(batch)
        user_prompt = user_header + papers_block

        try:
            response_text = chat_completion(
                client=client,
                model=settings.llm_model,
                system_prompt=SYSTEM_PROMPT,
                user_prompt=user_prompt,
                temperature=settings.llm_temperature,
            )
            scores = _parse_response(response_text)
        except Exception:
            logger.exception("LLM call failed for batch %d, skipping", batch_num + 1)
            continue

        for item in scores:
            try:
                idx = item["index"]
                if idx < 0 or idx >= len(batch):
                    continue
                score = int(item.get("score", 1))
                matched_topic = item.get("matched_topic", "none")
                reason = item.get("reason", "")
                if score >= settings.relevance_threshold and matched_topic != "none":
                    relevant.append(RelevantPaper(
                        paper=batch[idx],
                        score=score,
                        matched_topic=matched_topic,
                        reason=reason,
                    ))
            except (KeyError, ValueError, TypeError):
                logger.warning("Malformed score item in batch %d: %s", batch_num + 1, item)

    # Sort by score descending
    relevant.sort(key=lambda r: r.score, reverse=True)
    logger.info("Filtering complete: %d/%d papers above threshold", len(relevant), len(papers))
    return relevant
