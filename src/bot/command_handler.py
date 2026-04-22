"""Execute commands: /scan, /read, /report, /fetch, /tree, /build, /help, /prefs, /reset."""

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
    get_build_session,
    delete_build_session,
)

logger = logging.getLogger(__name__)

# Concurrency guard for /fetch
_active_fetches: set[str] = set()


async def handle_command(
    cmd,
    chat_id: str,
    message_id: str,
    raw_text: str,
    feishu=None,
    db=None,
    settings=None,
    req_id: str = "",
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
        if cmd.name in ("scan", "read", "report", "fetch", "chat", "debug"):
            await feishu.reply_text(message_id, "Processing...")

        if cmd.name == "scan":
            await _handle_scan(cmd.args, chat_id, message_id, feishu, db, settings)
        elif cmd.name == "read":
            await _handle_read(cmd.args, chat_id, message_id, feishu, db, settings)
        elif cmd.name == "report":
            await _handle_report(cmd.args, chat_id, message_id, feishu, db, settings)
        elif cmd.name == "fetch":
            await _handle_fetch(chat_id, message_id, feishu, db, settings)
        elif cmd.name == "tree":
            await _handle_tree(chat_id, message_id, feishu, db)
        elif cmd.name == "build":
            await _handle_build(chat_id, message_id, feishu, db, settings)
        elif cmd.name == "help":
            await _handle_help(chat_id, message_id, feishu)
        elif cmd.name == "prefs":
            await _handle_prefs(chat_id, message_id, feishu, db)
        elif cmd.name == "reset":
            await _handle_reset(chat_id, message_id, feishu, db)
        elif cmd.name == "debug":
            await _handle_debug(cmd.args, chat_id, message_id, feishu)
        elif cmd.name == "chat":
            await _handle_chat(chat_id, raw_text, feishu, db, settings)
    except Exception as e:
        from .debug import record_error
        record = record_error(req_id, f"cmd:{cmd.name}", e)
        logger.error("Command handler failed [%s]: %s", req_id, cmd.name,
                      exc_info=e, extra={"req_id": req_id})
        try:
            card = _build_debug_error_card(record, chat_id)
            if message_id:
                await feishu.reply_card(message_id, card)
            else:
                await feishu.send_card(chat_id, card)
        except Exception:
            logger.exception("Failed to send error message", extra={"req_id": req_id})


async def handle_card_callback(
    callback_type: str,
    arxiv_id: str,
    chat_id: str,
    feishu,
    db: sqlite3.Connection,
    settings: Settings,
    req_id: str = "",
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

        elif callback_type == "report":
            # "View Report" button from fetch result card
            await _handle_report("all", chat_id, "", feishu, db, settings)

        elif callback_type == "build_accept":
            await _handle_build_accept(chat_id, feishu, db, settings)

        elif callback_type == "build_reject":
            await _handle_build_reject(chat_id, feishu, db)

    except Exception as e:
        from .debug import record_error
        record = record_error(req_id, f"callback:{callback_type}", e)
        logger.error("Card callback failed [%s]: type=%s, arxiv=%s", req_id, callback_type, arxiv_id,
                      exc_info=e, extra={"req_id": req_id})
        try:
            card = _build_debug_error_card(record, chat_id)
            await feishu.send_card(chat_id, card)
        except Exception:
            logger.exception("Failed to send error message", extra={"req_id": req_id})


# ── Command handlers ────────────────────────────────────────────────────


async def _handle_debug(
    args: str,
    chat_id: str,
    message_id: str,
    feishu,
) -> None:
    """Execute /debug command: show errors or toggle verbose mode."""
    from .debug import get_recent_errors, is_verbose, set_verbose
    from .card_builder import build_debug_card

    arg = args.strip().lower()

    if arg == "on":
        set_verbose(chat_id, True)
        await feishu.reply_text(message_id, "Verbose mode enabled. Error cards will now include full tracebacks.")
        return

    if arg == "off":
        set_verbose(chat_id, False)
        await feishu.reply_text(message_id, "Verbose mode disabled.")
        return

    # Default: show recent errors
    errors = get_recent_errors(10)
    verbose = is_verbose(chat_id)
    card = build_debug_card(errors, verbose)
    await feishu.reply_card(message_id, card)


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


async def _handle_fetch(
    chat_id: str,
    message_id: str,
    feishu,
    db: sqlite3.Connection,
    settings: Settings,
) -> None:
    """Execute /fetch command: collect and analyze papers from arXiv."""
    from .card_builder import build_fetch_result_card
    from ..main import run_collect_and_analyze

    # Concurrency guard
    if chat_id in _active_fetches:
        await feishu.reply_text(message_id, "A fetch is already in progress. Please wait.")
        return

    _active_fetches.add(chat_id)
    try:
        loop = asyncio.get_event_loop()
        stats = await loop.run_in_executor(
            None, lambda: run_collect_and_analyze(db, settings)
        )

        card = build_fetch_result_card(stats)
        await feishu.reply_card(message_id, card)
        logger.info("Fetch complete for %s: %s", chat_id, stats)
    except Exception as e:
        logger.exception("Fetch failed for %s", chat_id)
        await feishu.reply_card(message_id, _build_error_card(f"Fetch failed: {e}"))
    finally:
        _active_fetches.discard(chat_id)


def _build_error_card(message: str) -> dict:
    """Helper to build an error card from command_handler."""
    from .card_builder import _error_card
    return _error_card(message)


def _build_debug_error_card(record, chat_id: str) -> dict:
    """Build an error card with request ID, source, and optional traceback."""
    from .debug import is_verbose

    elements = [
        {
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**Error:** {record.error_message}",
            },
        },
        {
            "tag": "div",
            "fields": [
                {
                    "is_short": True,
                    "text": {"tag": "lark_md", "content": f"**Request ID:** `{record.request_id}`"},
                },
                {
                    "is_short": True,
                    "text": {"tag": "lark_md", "content": f"**Source:** `{record.source}`"},
                },
                {
                    "is_short": True,
                    "text": {"tag": "lark_md", "content": f"**Time:** {record.timestamp.strftime('%H:%M:%S UTC')}"},
                },
            ],
        },
    ]

    if is_verbose(chat_id):
        tb = record.traceback_text[-2000:]
        elements.append({"tag": "hr"})
        elements.append({
            "tag": "div",
            "text": {
                "tag": "lark_md",
                "content": f"**Traceback:**\n```\n{tb}\n```",
            },
        })

    elements.append({
        "tag": "note",
        "elements": [
            {"tag": "plain_text", "content": "Use /debug to view recent errors."},
        ],
    })

    return {
        "config": {"wide_screen_mode": True},
        "header": {
            "title": {"tag": "plain_text", "content": "Error"},
            "template": "red",
        },
        "elements": elements,
    }


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
    from ..tree import build_category_groups, get_root_categories

    # Build category mapping from tree root nodes
    cat_groups = build_category_groups(db)
    root_categories = get_root_categories(db)

    # Parse category filter
    filter_cat = args.strip().upper() if args.strip() else "ALL"
    # Build valid filters from tree root short codes + "ALL"
    valid_shorts = set(cat_groups.keys())
    valid_filters = {"ALL"} | valid_shorts
    if filter_cat not in valid_filters:
        short_list = ", ".join(sorted(valid_shorts)) if valid_shorts else "none"
        await feishu.reply_text(
            message_id,
            f"Unknown category filter: {filter_cat}. Valid: all, {short_list}",
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

        # Find primary category group from tree roots
        primary_group = "Other"
        for c in cats:
            short = c.split(".")[-1] if "." in c else c
            if short in cat_groups:
                primary_group = cat_groups[short]
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

    card = build_report_card(
        papers_by_category=papers_by_category,
        total_scanned=total_scanned,
        total_relevant=total_relevant,
        categories=root_categories,
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
    """Handle natural language conversation.

    Intercepts messages when a build session is in 'awaiting_interests' stage.
    """
    from .card_builder import build_tree_preview_card
    from .build_engine import generate_tree_from_interests
    from ..storage import upsert_build_session

    # Check if this chat has an active build session
    session = get_build_session(db, chat_id)
    if session and session["stage"] == "awaiting_interests":
        await feishu.send_text(chat_id, "Generating knowledge tree based on your interests...")

        loop = asyncio.get_event_loop()
        try:
            nodes = await loop.run_in_executor(
                None,
                lambda: generate_tree_from_interests(user_text, settings),
            )

            if not nodes:
                await feishu.send_text(chat_id, "Failed to generate a tree. Please try again with more specific interests.")
                delete_build_session(db, chat_id)
                return

            # Store generated tree YAML in session for later import
            import yaml
            tree_yaml_str = yaml.dump({"tree": nodes}, default_flow_style=False, allow_unicode=True)
            upsert_build_session(db, chat_id, stage="confirming", interests=user_text, tree_yaml=tree_yaml_str)

            card = build_tree_preview_card(nodes)
            await feishu.send_card(chat_id, card)
        except Exception as e:
            logger.exception("Build engine failed for %s", chat_id)
            await feishu.send_text(chat_id, f"Error generating tree: {e}")
            delete_build_session(db, chat_id)
        return

    # Default: normal conversation
    from .conversation import handle_conversation

    response = await handle_conversation(chat_id, user_text, db, settings)
    await feishu.send_text(chat_id, response)


async def _handle_build(
    chat_id: str,
    message_id: str,
    feishu,
    db: sqlite3.Connection,
    settings: Settings,
) -> None:
    """Execute /build command: start or manage interactive tree building."""
    from .card_builder import build_build_prompt_card
    from ..storage import upsert_build_session

    session = get_build_session(db, chat_id)

    if session and session["stage"] == "confirming":
        await feishu.reply_text(message_id, "You have a pending tree to review. Please check the preview card and click Accept or Reject.")
        return

    # Start a new build session
    upsert_build_session(db, chat_id, stage="awaiting_interests")
    card = build_build_prompt_card()
    await feishu.reply_card(message_id, card)


async def _handle_build_accept(
    chat_id: str,
    feishu,
    db: sqlite3.Connection,
    settings: Settings,
) -> None:
    """Accept a generated tree: import it into the DB."""
    from ..tree import import_tree_from_yaml_force
    from ..storage import upsert_build_session
    import yaml
    import tempfile

    session = get_build_session(db, chat_id)
    if not session or session["stage"] != "confirming":
        await feishu.send_text(chat_id, "No pending tree to accept.")
        return

    tree_yaml_str = session.get("tree_yaml", "")
    if not tree_yaml_str:
        await feishu.send_text(chat_id, "No tree data found in session.")
        delete_build_session(db, chat_id)
        return

    try:
        # Write to a temp file and import
        with tempfile.NamedTemporaryFile(mode="w", suffix=".yaml", delete=False) as f:
            f.write(tree_yaml_str)
            tmp_path = f.name

        imported = import_tree_from_yaml_force(db, tmp_path)
        logger.info("Build accept: imported %d tree nodes for %s", imported, chat_id)

        # Re-initialize preferences for new nodes
        from .preference_store import initialize_all_preferences
        initialize_all_preferences(db)

        await feishu.send_text(
            chat_id,
            f"Knowledge tree updated! Imported {imported} nodes. Use /tree to view it, then /fetch to collect papers.",
        )
    except Exception as e:
        logger.exception("Failed to import generated tree for %s", chat_id)
        await feishu.send_text(chat_id, f"Failed to import tree: {e}")
    finally:
        import os
        try:
            os.unlink(tmp_path)
        except OSError:
            pass
        upsert_build_session(db, chat_id, stage="done")


async def _handle_build_reject(
    chat_id: str,
    feishu,
    db: sqlite3.Connection,
) -> None:
    """Reject a generated tree: cancel the build session."""
    session = get_build_session(db, chat_id)
    if not session or session["stage"] != "confirming":
        await feishu.send_text(chat_id, "No pending tree to reject.")
        return

    delete_build_session(db, chat_id)
    await feishu.send_text(chat_id, "Tree generation cancelled. Use /build to start again.")
