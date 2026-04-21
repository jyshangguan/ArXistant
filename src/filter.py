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
You are a strict research relevance evaluator for arXiv papers.

## Scoring scale (1–5)

**Score 1 — Not relevant.** The paper's subject matter has no meaningful connection to any of the listed research topics. This is the DEFAULT score. If in doubt, score 1.

**Score 2 — Tangentially related.** The paper shares a broad field or methodology with the topics but does not address any specific research question described in the topic descriptions or keywords.

**Score 3 — Moderate overlap.** The paper discusses a related phenomenon or uses a relevant technique, but the core focus is not directly about any listed topic. (Example: a paper on galaxy formation is not automatically relevant to "High-Energy Transients" just because both are astrophysics.)

**Score 4 — Highly relevant.** The paper directly addresses a specific research question, object class, or method described in the topic. You would flag this for a colleague working on this exact topic.

**Score 5 — Perfect match.** The paper is a must-read for anyone working on this specific topic — e.g., it proposes a new model, presents a breakthrough result, or provides a comprehensive review directly covering the topic.

## Expected score distribution

For a typical batch of recent arXiv papers in broad astrophysical categories:
- ~50–60% should receive score 1
- ~20–30% should receive score 2
- ~10–15% should receive score 3
- ~5% should receive score 4
- ~1–2% should receive score 5

**Self-check:** If more than 40% of the papers in your batch receive a score ≥ 3, you are scoring too generously. Re-evaluate and lower scores accordingly.

## Cumulative decision criteria

Start at score 1. Only raise the score if ALL conditions for that level are met:

- **Score 2 or above:** The paper's subject shares a broad field with at least one topic.
- **Score 3 or above:** The paper explicitly discusses a phenomenon, object, or technique listed in the topic description or keywords.
- **Score 4 or above:** The paper's primary research question directly aligns with a specific aspect of a topic description.
- **Score 5:** The paper is a landmark or must-read result for this specific topic.

## Rules

- Score based on the paper's title, authors, and abstract only.
- A paper may match zero topics. If none match, give score 1 and set matched_topic to "none".
- Do NOT inflate scores. Academic relevance is high-bar, not low-bar.
- Respond ONLY with a JSON array, no other text.

## Few-shot examples

Example 1:
Topics: ["Galactic Dynamics"] (keywords: galactic dynamics, Milky Way, spiral arms, bar formation)
Paper: "Cosmic ray propagation in the interstellar medium" — a study of cosmic ray diffusion coefficients.
Score: 1, matched_topic: "none", reason: "Cosmic rays are not related to galactic dynamics or structure."

Example 2:
Topics: ["High-Energy Transients"] (keywords: gamma-ray burst, supernova, tidal disruption event)
Paper: "Spectroscopic survey of nearby star-forming galaxies" — optical spectroscopy of HII regions.
Score: 1, matched_topic: "none", reason: "Star-forming galaxy spectroscopy is unrelated to high-energy transients."

Example 3:
Topics: ["Galactic Dynamics"] (keywords: galactic dynamics, Milky Way, spiral arms, bar formation)
Paper: "The bar fraction in local disk galaxies from SDSS" — measures bar fraction vs. stellar mass.
Score: 4, matched_topic: "Galactic Dynamics", reason: "Directly studies bar formation, a core keyword of the topic."

Example 4:
Topics: ["High-Energy Transients"] (keywords: gamma-ray burst, supernova, tidal disruption event)
Paper: "Radio follow-up of Swift-detected GRB 2504A reveals late-time rebrightening."
Score: 5, matched_topic: "High-Energy Transients", reason: "Direct observation of a GRB with detailed multi-frequency analysis — a must-read for GRB researchers."

## Response format

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
