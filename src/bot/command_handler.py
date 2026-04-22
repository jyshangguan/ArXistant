"""Execute commands: /scan, /read, /report, /tree, /help, /prefs, /reset."""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
from collections import defaultdict

from ..config import Settings
from ..storage import (
    get_all_tree_nodes,
    get_analyzed_papers,
    get_links_for_paper,
    count_papers,
    get_tree_node_by_name,
)

logger = logging.getLogger(__name__)


async def handle_command(
    cmd,
    chat_id: str,
    message_id: str,
    raw_text: str,
    feishu=None,
    db=None,
    settings=None,
) -> None:
    """Route a parsed command to the appropriate handler.

    Sends a "Processing..." acknowledgment for long-running commands.
    """
    if feishu is None or db is None or settings is None:
        from .server import get_feishu, get_db, get_app_settings
        feishu = get_feishu()
        db = get_db()
        settings = get_app_settings()

    try:
        # Acknowledge long-running commands
        if cmd.name in ("scan", "read", "report", "chat"):
            await feishu.reply_text(message_id, "Processing...")

        if cmd.name == "scan":
            await _handle_scan(cmd.args, chat_id, message_id, feishu, db, settings)
        elif cmd.name == "read":
            await _handle_read(cmd.args, chat_id, message_id, feishu, db, settings)
        elif cmd.name == "report":
            await _handle_report(cmd.args, chat_id, message_id, feishu, db, settings)
        elif cmd.name == "tree":
            await _handle_tree(chat_id, message_id, feishu, db)
        elif cmd.name == "help":
            await _handle_help(chat_id, message_id, feishu)
        elif cmd.name == "prefs":
            await _handle_prefs(chat_id, message_id, feishu, db)
        elif cmd.name == "reset":
            await _handle_reset(chat_id, message_id, feishu, db)
        elif cmd.name == "chat":
            await _handle_chat(chat_id, raw_text, feishu, db, settings)
    except Exception as e:
        logger.exception("Command handler failed: %s", cmd.name)
        try:
            from .card_builder import _error_card
            await feishu.reply_card(message_id, _error_card(f"Error: {e}"))
        except Exception:
            logger.exception("Failed to send error message")


async def handle_card_callback(
    callback_type: str,
    arxiv_id: str,
    chat_id: str,
    feishu,
    db: sqlite3.Connection,
    settings: Settings,
) -> None:
    """Handle interactive card button callbacks."""
    from .card_builder import build_scan_result_card, build_reading_note_card

    try:
        if callback_type == "read":
            from ..tools.read_paper import read_paper
            from .preference_store import boost_weight, ensure_preference

            loop = asyncio.get_event_loop()
            note = await loop.run_in_executor(
                None, lambda: read_paper(arxiv_id, settings, db)
            )

            # Boost preferences for connected nodes
            for tc in note.tree_connections:
                node = get_tree_node_by_name(db, tc.node_name)
                if node:
                    boost_weight(db, node.id, amount=2.0)

            card = build_reading_note_card(note)
            await feishu.send_card(chat_id, card)

        elif callback_type == "scan":
            from ..tools.scan_paper import scan_paper
            from .preference_store import boost_weight, ensure_preference

            loop = asyncio.get_event_loop()
            result = await loop.run_in_executor(
                None, lambda: scan_paper(arxiv_id, settings, db)
            )

            # Boost preferences for high-relevance nodes
            for link in result.tree_links:
                if link.relevance_score >= 3:
                    node = get_tree_node_by_name(db, link.node_name)
                    if node:
                        boost_weight(db, node.id, amount=1.0)

            card = build_scan_result_card(result)
            await feishu.send_card(chat_id, card)

    except Exception as e:
        logger.exception("Card callback failed: type=%s, arxiv=%s", callback_type, arxiv_id)
        try:
            from .card_builder import _error_card
            await feishu.send_card(chat_id, _error_card(f"Error: {e}"))
        except Exception:
            logger.exception("Failed to send error message")


# ── Command handlers ────────────────────────────────────────────────────


async def _handle_scan(
    arxiv_id: str,
    chat_id: str,
    message_id: str,
    feishu,
    db: sqlite3.Connection,
    settings: Settings,
) -> None:
    """Execute /scan command."""
    from ..tools.scan_paper import scan_paper
    from .card_builder import build_scan_result_card
    from .preference_store import boost_weight

    arxiv_id = arxiv_id.strip()
    if not arxiv_id:
        await feishu.reply_text(message_id, "Usage: /scan <arxiv_id>")
        return

    loop = asyncio.get_event_loop()
    result = await loop.run_in_executor(
        None, lambda: scan_paper(arxiv_id, settings, db)
    )

    # Boost preferences for high-relevance nodes
    for link in result.tree_links:
        if link.relevance_score >= 3:
            node = get_tree_node_by_name(db, link.node_name)
            if node:
                boost_weight(db, node.id, amount=1.0)

    card = build_scan_result_card(result)
    await feishu.reply_card(message_id, card)


async def _handle_read(
    arxiv_id: str,
    chat_id: str,
    message_id: str,
    feishu,
    db: sqlite3.Connection,
    settings: Settings,
) -> None:
    """Execute /read command."""
    from ..tools.read_paper import read_paper
    from .card_builder import build_reading_note_card
    from .preference_store import boost_weight

    arxiv_id = arxiv_id.strip()
    if not arxiv_id:
        await feishu.reply_text(message_id, "Usage: /read <arxiv_id>")
        return

    loop = asyncio.get_event_loop()
    note = await loop.run_in_executor(
        None, lambda: read_paper(arxiv_id, settings, db)
    )

    # Boost preferences for connected nodes
    for tc in note.tree_connections:
        node = get_tree_node_by_name(db, tc.node_name)
        if node:
            boost_weight(db, node.id, amount=2.0)

    card = build_reading_note_card(note)
    await feishu.reply_card(message_id, card)


async def _handle_report(
    args: str,
    chat_id: str,
    message_id: str,
    feishu,
    db: sqlite3.Connection,
    settings: Settings,
) -> None:
    """Execute /report command."""
    from .card_builder import build_report_card
    from .preference_store import get_weighted_score, initialize_all_preferences

    # Parse category filter
    filter_cat = args.strip().upper() if args.strip() else "ALL"
    valid_filters = {"ALL", "GA", "HE"}
    if filter_cat not in valid_filters:
        await feishu.reply_text(
            message_id,
            f"Unknown category filter: {filter_cat}. Use GA, HE, or all.",
        )
        return

    # Initialize preferences for all nodes (lazy init)
    initialize_all_preferences(db)

    # Get analyzed papers with quality >= threshold
    threshold = settings.relevance_threshold
    papers = get_analyzed_papers(db, min_quality=threshold)

    if not papers:
        await feishu.reply_text(message_id, "No relevant papers found in the database.")
        return

    # Build paper→links mapping
    paper_links_map: dict[str, list[dict]] = {}
    for p in papers:
        links = get_links_for_paper(db, p.arxiv_id)
        if links:
            paper_links_map[p.arxiv_id] = links

    # Group by category and compute weighted scores
    papers_by_category: dict[str, list[dict]] = defaultdict(list)

    for p in papers:
        cats = [c.strip() for c in p.categories.split(",")] if p.categories else []

        # Apply category filter
        if filter_cat != "ALL":
            cat_match = any(filter_cat in c for c in cats)
            if not cat_match:
                continue

        # Compute weighted score
        weighted_score = get_weighted_score(db, p.arxiv_id, p.quality_score or 0)

        # Find primary category group
        primary_group = "Other"
        for c in cats:
            if "GA" in c:
                primary_group = "Galactic Dynamics (GA)"
                break
            elif "HE" in c:
                primary_group = "High-Energy Transients (HE)"
                break

        links = paper_links_map.get(p.arxiv_id, [])

        paper_dict = {
            "arxiv_id": p.arxiv_id,
            "title": p.title,
            "quality_score": p.quality_score or 0,
            "quality_reason": p.quality_reason,
            "tree_links": links,
            "sort_key": weighted_score,
            "categories": cats,
        }

        papers_by_category[primary_group].append(paper_dict)

    # Sort by weighted score within each category
    for cat_papers in papers_by_category.values():
        cat_papers.sort(key=lambda x: x["sort_key"], reverse=True)

    total_scanned = count_papers(db)
    total_relevant = len(papers)

    categories = ["astro-ph.GA", "astro-ph.HE"]  # TODO: read from config

    card = build_report_card(
        papers_by_category=papers_by_category,
        total_scanned=total_scanned,
        total_relevant=total_relevant,
        categories=categories,
    )

    await feishu.reply_card(message_id, card)


async def _handle_tree(
    chat_id: str,
    message_id: str,
    feishu,
    db: sqlite3.Connection,
) -> None:
    """Execute /tree command."""
    from .card_builder import build_tree_card

    nodes = get_all_tree_nodes(db)
    if not nodes:
        await feishu.reply_text(message_id, "No knowledge tree nodes found.")
        return

    # Build parent→children mapping
    node_children: dict[int, list] = defaultdict(list)
    for n in nodes:
        if n.parent_id is not None:
            node_children[n.parent_id].append(n)

    card = build_tree_card(nodes, node_children)
    await feishu.reply_card(message_id, card)


async def _handle_help(
    chat_id: str,
    message_id: str,
    feishu,
) -> None:
    """Execute /help command."""
    from .card_builder import build_help_card
    await feishu.reply_card(message_id, build_help_card())


async def _handle_prefs(
    chat_id: str,
    message_id: str,
    feishu,
    db: sqlite3.Connection,
) -> None:
    """Execute /prefs command."""
    from .card_builder import build_prefs_card
    from .preference_store import get_all_preferences

    prefs = get_all_preferences(db)
    card = build_prefs_card(prefs)
    await feishu.reply_card(message_id, card)


async def _handle_reset(
    chat_id: str,
    message_id: str,
    feishu,
    db: sqlite3.Connection,
) -> None:
    """Execute /reset command."""
    from .session_store import clear_session

    deleted = clear_session(db, chat_id)
    await feishu.reply_text(
        message_id,
        f"Session reset. Cleared {deleted} message(s) from history.",
    )


async def _handle_chat(
    chat_id: str,
    user_text: str,
    feishu,
    db: sqlite3.Connection,
    settings: Settings,
) -> None:
    """Handle natural language conversation."""
    from .conversation import handle_conversation

    response = await handle_conversation(chat_id, user_text, db, settings)
    await feishu.send_text(chat_id, response)
