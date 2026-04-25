"""APScheduler for daily cron fetch card push."""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timezone

import sqlite3

from ..config import Settings

logger = logging.getLogger(__name__)

_scheduler = None

# Concurrency guard for scheduled fetch
_scheduled_fetch_active = False

# Standard cron day-of-week: 0=Sun, 1=Mon, ..., 6=Sat
# APScheduler ISO day-of-week: 0=Mon, 1=Tue, ..., 6=Sun
_CRON_TO_APSCHED_DOW = {0: 6, 1: 0, 2: 1, 3: 2, 4: 3, 5: 4, 6: 5}


def _convert_cron_dow(dow_str: str) -> str:
    """Convert standard cron day-of-week to APScheduler ISO convention.

    Standard cron: 0=Sun, 1=Mon, ..., 6=Sat (also accepts mon-sun names).
    APScheduler:   0=Mon, 1=Tue, ..., 6=Sun.
    """
    import re

    # Handle named ranges like mon-fri — APScheduler accepts them natively
    if dow_str == "*" or re.fullmatch(r"[a-zA-Z]+(-[a-zA-Z]+)?(,[a-zA-Z]+(-[a-zA-Z]+)?)*", dow_str):
        return dow_str

    def _convert_token(token: str) -> str:
        token = token.strip()
        if re.fullmatch(r"\d+", token):
            return str(_CRON_TO_APSCHED_DOW[int(token)])
        if re.fullmatch(r"\d+-\d+", token):
            lo, hi = token.split("-")
            return f"{_CRON_TO_APSCHED_DOW[int(lo)]}-{_CRON_TO_APSCHED_DOW[int(hi)]}"
        return token

    return ",".join(_convert_token(part) for part in dow_str.split(","))


def start_scheduler(
    settings: Settings,
    db_conn: sqlite3.Connection,
    feishu_client,
) -> None:
    """Start the APScheduler with the daily fetch cron job."""
    global _scheduler

    if _scheduler is not None:
        return

    if not settings.target_chat_id:
        logger.info("No target_chat_id configured, scheduler not started")
        return

    from apscheduler.schedulers.asyncio import AsyncIOScheduler
    from apscheduler.triggers.cron import CronTrigger

    _scheduler = AsyncIOScheduler()

    # Parse cron expression (convert standard cron DOW to APScheduler ISO)
    cron_parts = settings.report_cron.strip().split()
    if len(cron_parts) == 5:
        dow = _convert_cron_dow(cron_parts[4])
        trigger = CronTrigger(
            minute=cron_parts[0],
            hour=cron_parts[1],
            day=cron_parts[2],
            month=cron_parts[3],
            day_of_week=dow,
        )
    else:
        logger.warning("Invalid cron expression: %s, using default (30 10 * * 1-5)", settings.report_cron)
        trigger = CronTrigger(hour=10, minute=30, day_of_week="mon-fri")

    _scheduler.add_job(
        _push_daily_report,
        trigger=trigger,
        args=[settings, db_conn, feishu_client],
        id="daily_report",
        name="Daily fetch card push",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info("Scheduler started: daily fetch at %s", settings.report_cron)


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
    """Fetch new papers, keyword-filter, and push a fetch card to Feishu.

    Mirrors the /fetch command flow (no LLM):
      1. collect_and_store()  — fetch from arXiv API, store in DB
      2. keyword_pre_filter() — filter against knowledge tree keywords
      3. build_fetch_list_card() — build interactive card with [Scan]/[Read] buttons
    """
    global _scheduled_fetch_active

    from .card_builder import build_fetch_list_card
    from .debug import new_request_id, record_error

    req_id = new_request_id()

    chat_id = settings.target_chat_id
    if not chat_id:
        logger.warning("No target_chat_id, skipping scheduled report")
        return

    # Safety check: skip on weekends (cron should handle this, but just in case)
    if datetime.now().weekday() >= 5:
        logger.info("Skipping scheduled fetch: weekend")
        return

    logger.info("Generating scheduled fetch card for %s", chat_id)

    try:
        if not _scheduled_fetch_active:
            _scheduled_fetch_active = True
            try:
                from ..main import collect_and_store
                from ..storage import get_recent_papers
                from ..filter import keyword_pre_filter

                # 1. Collect and store via listing page (fast, no LLM)
                loop = asyncio.get_event_loop()
                today = datetime.now(timezone.utc)
                stats = await loop.run_in_executor(
                    None, lambda: collect_and_store(db_conn, settings, target_date=today)
                )
                logger.info("Scheduled fetch complete: %s", stats)

                # 2. Keyword pre-filter: only papers published on target_date
                target_date_str = today.strftime('%Y-%m-%d')
                recent = get_recent_papers(db_conn, target_date=target_date_str)
                pre_filter_max = getattr(settings, "pre_filter_max", 30)
                relevant = keyword_pre_filter(recent, db_conn, max_papers=pre_filter_max)

                # 3. Handle empty results
                if not relevant:
                    await feishu_client.send_text(
                        chat_id,
                        "Daily fetch: No new relevant papers found today.",
                    )
                    return

                # 4. Build and send fetch list card
                card = build_fetch_list_card(relevant, stats)
                await feishu_client.send_card(chat_id, card)
                logger.info("Fetch card pushed to %s (%d relevant papers)", chat_id, len(relevant))

            except Exception as e:
                record_error(req_id, "scheduler:daily_report", e)
                logger.error("Scheduled fetch failed [%s]: %s", req_id, e,
                             exc_info=e, extra={"req_id": req_id})
                try:
                    await feishu_client.send_text(
                        chat_id,
                        f"Failed to generate daily fetch: {e}",
                    )
                except Exception:
                    logger.exception("Failed to send error notification")
            finally:
                _scheduled_fetch_active = False

    except Exception as e:
        record_error(req_id, "scheduler:daily_report", e)
        logger.error("Failed to push daily fetch [%s]: %s", req_id, e,
                      exc_info=e, extra={"req_id": req_id})
        try:
            await feishu_client.send_text(
                chat_id,
                f"Failed to generate daily fetch: {e}",
            )
        except Exception:
            logger.exception("Failed to send error notification")
