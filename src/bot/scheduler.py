"""APScheduler for daily cron report push."""

from __future__ import annotations

import asyncio
import logging
from collections import defaultdict

import sqlite3

from ..config import Settings
from ..storage import (
    get_all_tree_nodes,
    get_analyzed_papers,
    get_links_for_paper,
    count_papers,
    get_tree_node_by_name,
)

logger = logging.getLogger(__name__)

_scheduler = None

# Concurrency guard for scheduled fetch
_scheduled_fetch_active = False


def start_scheduler(
    settings: Settings,
    db_conn: sqlite3.Connection,
    feishu_client,
) -> None:
    """Start the APScheduler with the daily report cron job."""
    global _scheduler

    if _scheduler is not None:
        return

    if not settings.target_chat_id:
        logger.info("No target_chat_id configured, scheduler not started")
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    _scheduler = AsyncIOScheduler()

    # Parse cron expression
    cron_parts = settings.report_cron.strip().split()
    if len(cron_parts) == 5:
        trigger = CronTrigger(
            minute=cron_parts[0],
            hour=cron_parts[1],
            day=cron_parts[2],
            month=cron_parts[3],
            day_of_week=cron_parts[4],
        )
    else:
        logger.warning("Invalid cron expression: %s, using default (0 9 * * *)", settings.report_cron)
        trigger = CronTrigger(hour=9, minute=0)

    _scheduler.add_job(
        _push_daily_report,
        trigger=trigger,
        args=[settings, db_conn, feishu_client],
        id="daily_report",
        name="Daily report push",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info("Scheduler started: daily report at %s", settings.report_cron)


def stop_scheduler() -> None:
    """Stop the scheduler if running."""
    global _scheduler
    if _scheduler is not None:
        _scheduler.shutdown(wait=False)
        _scheduler = None
        logger.info("Scheduler stopped")


async def _push_daily_report(
    settings: Settings,
    db_conn: sqlite3.Connection,
    feishu_client,
) -> None:
    """Fetch new papers, then generate and push the daily report to Feishu."""
    global _scheduled_fetch_active

    from .card_builder import build_report_card
    from .preference_store import get_weighted_score, initialize_all_preferences
    from ..tree import build_category_groups, get_root_categories

    chat_id = settings.target_chat_id
    if not chat_id:
        logger.warning("No target_chat_id, skipping scheduled report")
        return

    logger.info("Generating scheduled daily report for %s", chat_id)

    try:
        # Fetch new papers before reporting
        if not _scheduled_fetch_active:
            _scheduled_fetch_active = True
            try:
                from ..main import run_collect_and_analyze
                loop = asyncio.get_event_loop()
                stats = await loop.run_in_executor(
                    None, lambda: run_collect_and_analyze(db_conn, settings)
                )
                logger.info("Scheduled fetch complete: %s", stats)
            except Exception as e:
                logger.exception("Scheduled fetch failed: %s", e)
            finally:
                _scheduled_fetch_active = False

        # Build category mapping from tree root nodes
        cat_groups = build_category_groups(db_conn)
        root_categories = get_root_categories(db_conn)

        # Initialize preferences
        initialize_all_preferences(db_conn)

        threshold = settings.relevance_threshold
        papers = get_analyzed_papers(db_conn, min_quality=threshold)

        if not papers:
            await feishu_client.send_text(
                chat_id,
                "Daily report: No new relevant papers found today.",
            )
            return

        # Build paper→links mapping
        paper_links_map: dict[str, list[dict]] = {}
        for p in papers:
            links = get_links_for_paper(db_conn, p.arxiv_id)
            if links:
                paper_links_map[p.arxiv_id] = links

        # Group by category
        papers_by_category: dict[str, list[dict]] = defaultdict(list)

        for p in papers:
            cats = [c.strip() for c in p.categories.split(",")] if p.categories else []
            weighted_score = get_weighted_score(db_conn, p.arxiv_id, p.quality_score or 0)

            # Find primary category group from tree roots
            primary_group = "Other"
            for c in cats:
                short = c.split(".")[-1] if "." in c else c
                if short in cat_groups:
                    primary_group = cat_groups[short]
                    break

            links = paper_links_map.get(p.arxiv_id, [])

            papers_by_category[primary_group].append({
                "arxiv_id": p.arxiv_id,
                "title": p.title,
                "quality_score": p.quality_score or 0,
                "quality_reason": p.quality_reason,
                "tree_links": links,
                "sort_key": weighted_score,
                "categories": cats,
            })

        # Sort by weighted score
        for cat_papers in papers_by_category.values():
            cat_papers.sort(key=lambda x: x["sort_key"], reverse=True)

        total_scanned = count_papers(db_conn)
        total_relevant = len(papers)

        card = build_report_card(
            papers_by_category=papers_by_category,
            total_scanned=total_scanned,
            total_relevant=total_relevant,
            categories=root_categories,
        )

        await feishu_client.send_card(chat_id, card)
        logger.info("Daily report pushed to %s (%d relevant papers)", chat_id, total_relevant)

    except Exception as e:
        logger.exception("Failed to push daily report: %s", e)
        try:
            await feishu_client.send_text(
                chat_id,
                f"Failed to generate daily report: {e}",
            )
        except Exception:
            logger.exception("Failed to send error notification")
