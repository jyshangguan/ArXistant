"""Filter papers by relevance using keyword matching (fast, no LLM)."""

from __future__ import annotations

import json
import logging
import re
import sqlite3
from dataclasses import dataclass

from .config import Settings, Topic
from .collector import RawPaper
from .llm_client import OpenAI, chat_completion, create_client
from .storage import StoredPaper

logger = logging.getLogger(__name__)

# Generic stop-words that produce false positives in keyword matching
_STOP_WORDS = frozenset({
    "the", "a", "an", "and", "or", "but", "in", "on", "at", "to", "for",
    "of", "with", "by", "from", "as", "is", "was", "are", "were", "been",
    "be", "have", "has", "had", "do", "does", "did", "will", "would",
    "could", "should", "may", "might", "shall", "can", "need", "dare",
    "ought", "used", "this", "that", "these", "those", "it", "its",
    "not", "no", "nor", "if", "then", "than", "too", "very", "just",
    "also", "such", "each", "every", "all", "any", "both", "few", "more",
    "most", "other", "some", "what", "which", "who", "whom", "how",
    "when", "where", "why", "we", "our", "us", "they", "their", "them",
    "study", "studies", "result", "results", "model", "models", "method",
    "methods", "analysis", "based", "using", "used", "data", "new",
    "paper", "papers", "work", "show", "shows", "propose", "proposed",
    "observation", "observations", "measurement", "measurements",
})


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


# ── Keyword pre-filter (no LLM) ────────────────────────────────────


def _extract_tree_keywords(conn: sqlite3.Connection) -> list[str]:
    """Extract keyword phrases from all active tree nodes.

    Uses node names (with parenthetical stripping), abbreviations,
    and multi-word phrases from descriptions. Returns deduplicated
    phrases (lowercased), longest first.
    """
    from .storage import get_all_tree_nodes

    nodes = get_all_tree_nodes(conn)
    phrases: set[str] = set()

    for node in nodes:
        name = node.name.strip()

        # Strip parentheticals like "(AGN)", "(BLR)" from node name
        stripped = re.sub(r'\s*\([^)]*\)\s*', ' ', name).strip()
        if len(stripped) >= 4:
            phrases.add(stripped.lower())

        # Add abbreviations from parens, e.g. "AGN", "VLBI"
        for abbr in re.findall(r'\(([A-Z]{2,6})\)', name):
            phrases.add(abbr.lower())

        # Extract meaningful phrases from description
        desc = node.description or ""
        if not desc:
            continue

        # Split on commas AND sentence-ending punctuation
        clauses = re.split(r'[,.;:!?]', desc)
        for clause in clauses:
            clause = clause.strip()
            if not clause:
                continue
            words = clause.split()
            # Extract 2-5 word phrases from each clause
            for length in range(2, min(6, len(words) + 1)):
                for start in range(len(words) - length + 1):
                    phrase_words = words[start:start + length]
                    if any(w.lower() not in _STOP_WORDS for w in phrase_words):
                        phrases.add(' '.join(w.lower() for w in phrase_words))

    # Remove phrases that are entirely stop-words or single words
    filtered = [
        p for p in phrases
        if any(w not in _STOP_WORDS for w in p.split())
    ]

    # Sort longest first (more specific matches first)
    filtered.sort(key=len, reverse=True)

    logger.info("Extracted %d keyword phrases from %d tree nodes", len(filtered), len(nodes))
    return filtered


@dataclass
class PreFilteredPaper:
    """A paper that matched keyword pre-filter, with match info."""
    paper: StoredPaper
    match_count: int
    matched_keywords: list[str]
    status: str  # "new", "scanned", "read"


def keyword_pre_filter(
    papers: list[StoredPaper],
    conn: sqlite3.Connection,
    max_papers: int = 30,
) -> list[PreFilteredPaper]:
    """Fast keyword-based relevance filter using tree node names and descriptions.

    No LLM calls. Matches keyword phrases against paper title + abstract.
    Returns papers sorted by match count, capped at max_papers.
    """
    if not papers:
        return []

    keywords = _extract_tree_keywords(conn)
    if not keywords:
        logger.warning("No keywords extracted from tree, returning all papers")
        return [PreFilteredPaper(p, 0, [], _paper_status(conn, p.arxiv_id)) for p in papers[:max_papers]]

    results: list[PreFilteredPaper] = []

    for p in papers:
        text = f"{p.title} {p.abstract}".lower()

        matched = []
        seen_substrings: set[str] = set()  # avoid double-counting sub-phrases

        for kw in keywords:
            if kw in text:
                # Only count if not a substring of an already-matched longer phrase
                is_substring = any(kw != existing and kw in existing for existing in matched)
                if not is_substring:
                    matched.append(kw)

        if matched:
            results.append(PreFilteredPaper(
                paper=p,
                match_count=len(matched),
                matched_keywords=matched[:5],  # keep top 5 for display
                status=_paper_status(conn, p.arxiv_id),
            ))

    # Sort by match count descending
    results.sort(key=lambda r: r.match_count, reverse=True)

    logger.info("Keyword pre-filter: %d/%d papers matched (%d keywords used)",
                len(results), len(papers), len(keywords))
    return results[:max_papers]


def _paper_status(conn: sqlite3.Connection, arxiv_id: str) -> str:
    """Determine a paper's analysis status: 'new', 'scanned', or 'read'."""
    has_reading = conn.execute(
        "SELECT 1 FROM reading_notes WHERE arxiv_id = ?", (arxiv_id,)
    ).fetchone()
    if has_reading:
        return "read"

    has_analysis = conn.execute(
        "SELECT 1 FROM papers WHERE arxiv_id = ? AND quality_score IS NOT NULL", (arxiv_id,)
    ).fetchone()
    if has_analysis:
        return "scanned"

    return "new"
