"""Tool 2: Detailed full-text reading with structured notes."""

from __future__ import annotations

import json
import logging
import re
import time

from openai import RateLimitError

from ..config import Settings
from ..llm_client import create_client, chat_completion
from ..storage import get_reading_note, upsert_reading_note
from ..tree import format_tree_for_prompt
from .html_parser import fetch_and_parse
from .prompts import READ_PAPER_PROMPT
from .types import ReadingNote, TreeConnection

logger = logging.getLogger(__name__)


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

    # Try brace extraction
    brace_match = re.search(r"\{.*\}", text, re.DOTALL)
    if brace_match:
        try:
            result = json.loads(brace_match.group(0))
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


def _get_cached_note(db_conn, arxiv_id: str, text_hash: str) -> ReadingNote | None:
    """Check if a cached reading note exists for this paper version."""
    cached = get_reading_note(db_conn, arxiv_id)
    if cached and cached["full_text_hash"] == text_hash:
        return _row_to_reading_note(arxiv_id, cached)
    return None


def _row_to_reading_note(arxiv_id: str, row: dict) -> ReadingNote:
    """Convert a database row to a ReadingNote dataclass."""
    return ReadingNote(
        arxiv_id=arxiv_id,
        title=row["title"],
        summary=row["summary"],
        key_findings=json.loads(row["key_findings"]) if row["key_findings"] else [],
        methodology=row["methodology"],
        results=row["results"],
        tree_connections=json.loads(row["tree_connections"]) if row["tree_connections"] else [],
        unfamiliar_concepts=json.loads(row["unfamiliar_concepts"]) if row["unfamiliar_concepts"] else [],
        cached=True,
    )


def read_paper(
    arxiv_id: str,
    settings: Settings,
    db_conn,
) -> ReadingNote:
    """Perform a detailed reading of a paper's full text.

    Args:
        arxiv_id: The arXiv identifier.
        settings: Application settings.
        db_conn: SQLite database connection.

    Returns:
        ReadingNote with structured analysis of the paper.
    """
    # 1. Fetch and parse HTML
    logger.info("Reading paper %s", arxiv_id)
    parsed = fetch_and_parse(
        arxiv_id,
        timeout=getattr(settings, "html_timeout", 30),
    )

    # 2. Check cache
    cached = _get_cached_note(db_conn, arxiv_id, parsed.full_text_hash)
    if cached is not None:
        logger.info("Returning cached reading note for %s", arxiv_id)
        return cached

    # 3. Load knowledge tree
    tree_prompt = format_tree_for_prompt(db_conn)

    # 4. Truncate text if needed
    max_chars = getattr(settings, "max_text_chars", 80000)
    text_to_send = _truncate_text(parsed.full_text_markdown, max_chars)

    # 5. Build user prompt
    user_prompt = (
        f"## Paper Full Text\n\n{text_to_send}\n\n"
        f"## Knowledge Tree\n\n{tree_prompt}"
    )

    # 6. Call LLM with retry
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
        except RateLimitError:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning("Rate limited, retrying in %ds (attempt %d/%d)", delay, attempt + 1, max_retries)
                time.sleep(delay)
            else:
                logger.exception("Rate limited, all retries exhausted")
        except Exception:
            logger.exception("LLM call failed for read_paper")
            break

    if response_text is None:
        raise RuntimeError(f"Failed to get LLM response for reading {arxiv_id}")

    # 7. Parse response
    parsed_response = _parse_read_response(response_text)

    key_findings = parsed_response.get("key_findings", [])
    tree_connections = []
    for tc in parsed_response.get("tree_connections", []):
        tree_connections.append(TreeConnection(
            node_name=tc["node_name"],
            connection=tc.get("connection", ""),
        ))

    unfamiliar_concepts = parsed_response.get("unfamiliar_concepts", [])

    note = ReadingNote(
        arxiv_id=arxiv_id,
        title=parsed.title,
        summary=parsed_response.get("summary", ""),
        key_findings=key_findings,
        methodology=parsed_response.get("methodology", ""),
        results=parsed_response.get("results", ""),
        tree_connections=tree_connections,
        unfamiliar_concepts=unfamiliar_concepts,
        cached=False,
    )

    # 8. Store in DB
    upsert_reading_note(
        db_conn,
        arxiv_id=arxiv_id,
        title=parsed.title,
        full_text_hash=parsed.full_text_hash,
        summary=note.summary,
        key_findings=json.dumps(note.key_findings),
        methodology=note.methodology,
        results=note.results,
        tree_connections=json.dumps(
            [{"node_name": tc.node_name, "connection": tc.connection}
             for tc in note.tree_connections]
        ),
        unfamiliar_concepts=json.dumps(note.unfamiliar_concepts),
    )

    logger.info("Reading note stored for %s", arxiv_id)
    return note
