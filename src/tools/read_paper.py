"""Tool 2: Executive reading summary with structured notes."""

from __future__ import annotations

import json
import logging
import re
import time

from openai import APIConnectionError, InternalServerError, RateLimitError

from ..config import Settings
from ..llm_client import create_client, chat_completion
from ..storage import get_paper, get_reading_note, upsert_reading_note
from ..tree import format_tree_for_prompt
from .html_parser import fetch_and_parse, PaperHtmlUnavailableError
from .prompts import READ_PAPER_PROMPT
from .types import ReadingNote, TreeConnection

logger = logging.getLogger(__name__)

# Version prefix to invalidate caches from the old pipeline format.
_CACHE_VERSION = "v2:"

# Regex patterns for fuzzy section title matching, in priority order.
_SECTION_PATTERNS: list[tuple[str, re.Pattern]] = [
    ("introduction", re.compile(r"^introduction$", re.IGNORECASE)),
    ("conclusion", re.compile(r"^concl(u|us)ion(s)?$", re.IGNORECASE)),
    ("summary", re.compile(r"^(main )?(summary|abstract)$", re.IGNORECASE)),
    ("results", re.compile(r"^(main )?result(s)?$", re.IGNORECASE)),
    ("experiments", re.compile(r"^experiment(s)?$", re.IGNORECASE)),
    ("findings", re.compile(r"^finding(s)?$", re.IGNORECASE)),
    ("discussion", re.compile(r"^discussion$", re.IGNORECASE)),
]


def _section_priority(section_title: str) -> int:
    """Return priority rank for a section title (lower = higher priority).

    Sections not matched get priority 99 (lowest).
    """
    title = section_title.strip()
    for idx, (_, pattern) in enumerate(_SECTION_PATTERNS):
        if pattern.match(title):
            return idx
    return 99


def _select_executive_sections(
    sections: list[dict],
    abstract: str,
    max_chars: int,
) -> str:
    """Pick the most important sections within a character budget.

    Always includes the abstract.  Sections are selected in priority order:
    Introduction, Conclusion/Summary, Results/Experiments/Findings,
    Discussion, then everything else — until the budget is exhausted.

    Returns the concatenated text ready to send to the LLM.
    """
    # Sort sections by priority (stable sort preserves document order for ties)
    ranked = sorted(
        enumerate(sections),
        key=lambda pair: (_section_priority(pair[1]["title"]), pair[0]),
    )

    budget = max_chars
    parts: list[str] = []

    # Always include abstract
    if abstract:
        abstract_text = f"## Abstract\n{abstract}\n\n"
        if len(abstract_text) <= budget:
            parts.append(abstract_text)
            budget -= len(abstract_text)

    for _orig_idx, section in ranked:
        text = section.get("text", "").strip()
        if not text:
            continue
        header = f"## {section['title']}\n"
        block = header + text + "\n\n"
        if len(block) <= budget:
            parts.append(block)
            budget -= len(block)

    return "".join(parts)


def _sanitize_json_escapes(s: str) -> str:
    """Fix invalid JSON escape sequences (e.g. LaTeX \\lambda, \\odot) by doubling the backslash."""
    return re.sub(
        r"\\(?![\\\"/bfnrtu])",
        r"\\\\",
        s,
    )


def _parse_read_response(text: str) -> dict:
    """Extract JSON from LLM response for read_paper."""
    text = text.strip()

    # Try direct parse
    try:
        result = json.loads(text)
        if isinstance(result, dict):
            return result
    except json.JSONDecodeError:
        pass

    # Try code fences
    fence_match = re.search(r"```(?:json)?\s*\n?(.*?)\n?\s*```", text, re.DOTALL)
    if fence_match:
        try:
            result = json.loads(fence_match.group(1).strip())
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            pass

    # Try brace extraction with escape sanitization (handles LaTeX in LLM output)
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            result = json.loads(brace_match.group(0))
            if isinstance(result, dict):
                return result
        except json.JSONDecodeError:
            sanitized = _sanitize_json_escapes(brace_match.group(0))
            try:
                result = json.loads(sanitized)
                if isinstance(result, dict):
                    return result
            except json.JSONDecodeError:
                pass

    logger.warning("Could not parse read_paper response as JSON")
    return {}


def _truncate_text(text: str, max_chars: int) -> str:
    """Truncate text to max_chars, keeping the beginning."""
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[... truncated ...]"


def _versioned_hash(text_hash: str) -> str:
    """Prefix the hash with a version tag to invalidate old caches."""
    return _CACHE_VERSION + text_hash


def _get_cached_note(db_conn, arxiv_id: str, text_hash: str) -> ReadingNote | None:
    """Check if a cached reading note exists for this paper version."""
    cached = get_reading_note(db_conn, arxiv_id)
    if cached and cached["full_text_hash"] == _versioned_hash(text_hash):
        return _row_to_reading_note(arxiv_id, cached)
    return None


def _row_to_reading_note(arxiv_id: str, row: dict) -> ReadingNote:
    """Convert a database row to a ReadingNote dataclass.

    Column reuse mapping:
      DB 'summary'   -> 'background'
      DB 'methodology' -> 'evaluation'
      DB 'results'   -> 'authors'
    """
    return ReadingNote(
        arxiv_id=arxiv_id,
        title=row["title"],
        authors=row["results"] or "",
        background=row["summary"] or "",
        key_findings=json.loads(row["key_findings"]) if row["key_findings"] else [],
        evaluation=row["methodology"] or "",
        tree_connections=json.loads(row["tree_connections"]) if row["tree_connections"] else [],
        cached=True,
    )


def read_paper(
    arxiv_id: str,
    settings: Settings,
    db_conn,
) -> ReadingNote:
    """Produce a concise executive reading summary of a paper.

    Args:
        arxiv_id: The arXiv identifier.
        settings: Application settings.
        db_conn: SQLite database connection.

    Returns:
        ReadingNote with background, key findings, and evaluation.
    """
    # 1. Fetch and parse HTML
    logger.info("Reading paper %s", arxiv_id)
    try:
        parsed = fetch_and_parse(
            arxiv_id,
            timeout=getattr(settings, "html_timeout", 30),
        )
    except PaperHtmlUnavailableError as e:
        raise RuntimeError(str(e)) from e

    # 2. Check cache
    cached = _get_cached_note(db_conn, arxiv_id, parsed.full_text_hash)
    if cached is not None:
        logger.info("Returning cached reading note for %s", arxiv_id)
        return cached

    # 3. Load authors from papers table
    authors = ""
    stored_paper = get_paper(db_conn, arxiv_id)
    if stored_paper:
        authors = stored_paper.authors or ""

    # 4. Load knowledge tree
    tree_prompt = format_tree_for_prompt(db_conn)

    # 5. Select most important sections within budget
    max_chars = getattr(settings, "executive_read_max_chars", 30000)
    text_to_send = _select_executive_sections(
        parsed.sections, parsed.abstract, max_chars,
    )

    # 6. Build user prompt
    author_line = f"**Authors:** {authors}" if authors else ""
    user_prompt = (
        f"## Paper\n**Title:** {parsed.title}\n{author_line}\n\n"
        f"## Selected Sections\n\n{text_to_send}\n\n"
        f"## Knowledge Tree\n\n{tree_prompt}"
    )

    # 7. Call LLM with retry
    client = create_client(settings)
    max_retries = 3
    base_delay = 15
    response_text = None

    for attempt in range(max_retries + 1):
        try:
            response_text = chat_completion(
                client=client,
                model=settings.llm_model,
                system_prompt=READ_PAPER_PROMPT,
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
            logger.exception("LLM call failed for read_paper")
            break

    if response_text is None:
        raise RuntimeError(f"Failed to get LLM response for reading {arxiv_id}")

    if response_text:
        logger.info("LLM response for read_paper %s: %d chars", arxiv_id, len(response_text))
    else:
        logger.warning("LLM returned empty response for read_paper %s", arxiv_id)

    # 8. Parse response
    parsed_response = _parse_read_response(response_text)

    if not parsed_response:
        logger.warning("Failed to parse read_paper response for %s. Raw text (first 500 chars): %s",
                       arxiv_id, response_text[:500] if response_text else "<empty>")

    key_findings = parsed_response.get("key_findings", [])[:3]
    tree_connections = []
    for tc in parsed_response.get("tree_connections", []):
        tree_connections.append(TreeConnection(
            node_name=tc["node_name"],
            connection=tc.get("connection", ""),
        ))

    note = ReadingNote(
        arxiv_id=arxiv_id,
        title=parsed.title,
        authors=authors,
        background=parsed_response.get("background", ""),
        key_findings=key_findings,
        evaluation=parsed_response.get("evaluation", ""),
        tree_connections=tree_connections,
        cached=False,
    )

    # 9. Store in DB — but only if LLM produced usable content.
    #    Do not cache empty/garbage results so the next read can retry.
    has_content = note.background or note.key_findings or note.evaluation
    if has_content:
        upsert_reading_note(
            db_conn,
            arxiv_id=arxiv_id,
            title=parsed.title,
            full_text_hash=_versioned_hash(parsed.full_text_hash),
            summary=note.background,       # DB 'summary' stores 'background'
            key_findings=json.dumps(note.key_findings),
            methodology=note.evaluation,   # DB 'methodology' stores 'evaluation'
            results=note.authors,          # DB 'results' stores 'authors'
            tree_connections=json.dumps(
                [{"node_name": tc.node_name, "connection": tc.connection}
                 for tc in note.tree_connections]
            ),
            unfamiliar_concepts="",        # no longer used
        )
        logger.info("Reading note stored for %s", arxiv_id)
    else:
        logger.warning("Not caching empty reading note for %s", arxiv_id)

    logger.info("Reading note stored for %s", arxiv_id)
    return note
