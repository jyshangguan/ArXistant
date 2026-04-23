"""Tests for src/bot/scheduler — daily fetch card push."""

import asyncio
import sqlite3
from datetime import datetime, timedelta, timezone
from unittest.mock import patch, MagicMock, AsyncMock

import pytest

from src.config import Settings


@pytest.fixture
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture
def settings():
    return Settings(
        target_chat_id="oc_test_chat",
        days_back=3,
        pre_filter_max=30,
        db_path="data/arxistant.db",
        llm_api_key="test-key",
    )


@pytest.fixture
def db_conn():
    conn = sqlite3.connect(":memory:")
    conn.execute("""
        CREATE TABLE IF NOT EXISTS papers (
            arxiv_id TEXT PRIMARY KEY,
            title TEXT NOT NULL,
            abstract TEXT,
            categories TEXT,
            published TEXT,
            updated TEXT,
            authors TEXT,
            pdf_url TEXT,
            first_seen_at TEXT DEFAULT (datetime('now')),
            quality_score INTEGER,
            quality_reason TEXT
        )
    """)
    conn.execute("""
        CREATE TABLE IF NOT EXISTS reading_notes (
            arxiv_id TEXT PRIMARY KEY,
            summary TEXT,
            created_at TEXT DEFAULT (datetime('now'))
        )
    """)
    conn.commit()
    yield conn
    conn.close()


@pytest.fixture
def feishu_client():
    client = MagicMock()
    client.send_text = AsyncMock()
    client.send_card = AsyncMock()
    return client


# ── Helpers ─────────────────────────────────────────────────────────────────


def _run(coro):
    """Run an async coroutine in a new event loop."""
    loop = asyncio.new_event_loop()
    try:
        return loop.run_until_complete(coro)
    finally:
        loop.close()


# ── Tests ──────────────────────────────────────────────────────────────────


@patch("src.bot.scheduler.datetime")
def test_push_daily_report_happy_path(mock_datetime, settings, db_conn, feishu_client):
    """Verify _push_daily_report calls collect_and_store -> keyword_pre_filter -> build_fetch_list_card -> send_card."""
    from src.bot.scheduler import _push_daily_report

    mock_datetime.now.return_value = datetime(2025, 1, 6)  # Monday

    mock_stats = {"papers_collected": 50, "papers_new": 12}
    mock_relevant = [MagicMock(arxiv_id="2501.00001", title="Test Paper")]
    mock_card = {"config": {"wide_screen_mode": True}, "elements": []}

    with (
        patch("src.main.collect_and_store", return_value=mock_stats) as mock_collect,
        patch("src.storage.get_recent_papers", return_value=[]) as mock_recent,
        patch("src.filter.keyword_pre_filter", return_value=mock_relevant) as mock_filter,
        patch("src.bot.card_builder.build_fetch_list_card", return_value=mock_card) as mock_build,
    ):
        _run(_push_daily_report(settings, db_conn, feishu_client))

    mock_collect.assert_called_once_with(db_conn, settings)
    mock_recent.assert_called_once_with(db_conn, days_back=settings.days_back)
    mock_filter.assert_called_once()
    mock_build.assert_called_once_with(mock_relevant, mock_stats)
    feishu_client.send_card.assert_called_once_with("oc_test_chat", mock_card)


@patch("src.bot.scheduler.datetime")
def test_push_daily_report_skips_weekend(mock_datetime, settings, db_conn, feishu_client):
    """Weekend (Saturday) should cause early return without any fetch/filter calls."""
    from src.bot.scheduler import _push_daily_report

    mock_datetime.now.return_value = datetime(2025, 1, 11)  # Saturday, weekday()=5

    with patch("src.main.collect_and_store") as mock_collect:
        _run(_push_daily_report(settings, db_conn, feishu_client))

    mock_collect.assert_not_called()
    feishu_client.send_text.assert_not_called()
    feishu_client.send_card.assert_not_called()


@patch("src.bot.scheduler.datetime")
def test_push_daily_report_no_papers_sends_text(mock_datetime, settings, db_conn, feishu_client):
    """When keyword_pre_filter returns empty list, send a 'no papers' text message."""
    from src.bot.scheduler import _push_daily_report

    mock_datetime.now.return_value = datetime(2025, 1, 6)  # Monday

    mock_stats = {"papers_collected": 50, "papers_new": 12}

    with (
        patch("src.main.collect_and_store", return_value=mock_stats),
        patch("src.storage.get_recent_papers", return_value=[]),
        patch("src.filter.keyword_pre_filter", return_value=[]),
    ):
        _run(_push_daily_report(settings, db_conn, feishu_client))

    feishu_client.send_text.assert_called_once_with(
        "oc_test_chat",
        "Daily fetch: No new relevant papers found today.",
    )
    feishu_client.send_card.assert_not_called()


@patch("src.bot.scheduler.datetime")
def test_push_daily_report_error_sends_text(mock_datetime, settings, db_conn, feishu_client):
    """When collect_and_store raises, an error text is sent to the chat."""
    from src.bot.scheduler import _push_daily_report

    mock_datetime.now.return_value = datetime(2025, 1, 6)  # Monday

    with (
        patch("src.main.collect_and_store", side_effect=RuntimeError("API timeout")),
        patch("src.bot.debug.record_error"),
    ):
        _run(_push_daily_report(settings, db_conn, feishu_client))

    feishu_client.send_text.assert_called_once()
    msg = feishu_client.send_text.call_args[0][1]
    assert "API timeout" in msg
    feishu_client.send_card.assert_not_called()


@patch("src.bot.scheduler.datetime")
def test_push_daily_report_skips_no_chat_id(mock_datetime, db_conn, feishu_client):
    """Empty target_chat_id causes early return."""
    from src.bot.scheduler import _push_daily_report

    empty_settings = Settings(target_chat_id="")
    mock_datetime.now.return_value = datetime(2025, 1, 6)

    with patch("src.main.collect_and_store") as mock_collect:
        _run(_push_daily_report(empty_settings, db_conn, feishu_client))

    mock_collect.assert_not_called()


def test_start_scheduler_no_chat_id():
    """start_scheduler should not create a scheduler when target_chat_id is empty."""
    from src.bot.scheduler import start_scheduler
    import src.bot.scheduler as sched_mod

    sched_mod._scheduler = None

    settings = Settings(target_chat_id="")
    db_conn = sqlite3.connect(":memory:")
    feishu_client = MagicMock()

    start_scheduler(settings, db_conn, feishu_client)

    assert sched_mod._scheduler is None

    db_conn.close()
    sched_mod._scheduler = None


def test_start_scheduler_creates_job():
    """start_scheduler should create an APScheduler job when target_chat_id is set."""
    from src.bot.scheduler import start_scheduler
    import src.bot.scheduler as sched_mod

    sched_mod._scheduler = None

    settings = Settings(
        target_chat_id="oc_test",
        report_cron="30 8 * * 1-5",
    )
    db_conn = MagicMock()
    feishu_client = MagicMock()

    with patch("apscheduler.schedulers.asyncio.AsyncIOScheduler") as mock_scheduler_cls:
        mock_scheduler = MagicMock()
        mock_scheduler_cls.return_value = mock_scheduler

        start_scheduler(settings, db_conn, feishu_client)

        mock_scheduler.add_job.assert_called_once()
        job_args = mock_scheduler.add_job.call_args
        assert job_args[1]["id"] == "daily_report"
        assert job_args[0][0].__name__ == "_push_daily_report"

    sched_mod._scheduler = None


# ── Cron DOW conversion tests ──────────────────────────────────────────────


class TestConvertCronDow:
    """Tests for _convert_cron_dow: standard cron -> APScheduler ISO day-of-week."""

    def test_range_1_5_to_0_4(self):
        from src.bot.scheduler import _convert_cron_dow
        assert _convert_cron_dow("1-5") == "0-4"

    def test_wildcard_passthrough(self):
        from src.bot.scheduler import _convert_cron_dow
        assert _convert_cron_dow("*") == "*"

    def test_named_passthrough(self):
        from src.bot.scheduler import _convert_cron_dow
        assert _convert_cron_dow("mon-fri") == "mon-fri"

    def test_single_digit(self):
        from src.bot.scheduler import _convert_cron_dow
        # 0=Sun -> 6 in ISO, 1=Mon -> 0 in ISO
        assert _convert_cron_dow("0") == "6"
        assert _convert_cron_dow("1") == "0"
        assert _convert_cron_dow("6") == "5"

    def test_comma_list(self):
        from src.bot.scheduler import _convert_cron_dow
        assert _convert_cron_dow("1,3,5") == "0,2,4"
        assert _convert_cron_dow("0,6") == "6,5"

    def test_range_and_list(self):
        from src.bot.scheduler import _convert_cron_dow
        assert _convert_cron_dow("1-3,5") == "0-2,4"


# ── Concurrency guard tests ───────────────────────────────────────────────


@patch("src.bot.scheduler.datetime")
def test_push_daily_report_concurrency_guard(mock_datetime, settings, db_conn, feishu_client):
    """If _scheduled_fetch_active is already True, the inner block is skipped."""
    from src.bot.scheduler import _push_daily_report
    import src.bot.scheduler as sched_mod

    mock_datetime.now.return_value = datetime(2025, 1, 6)  # Monday

    # Set the guard to True before calling
    sched_mod._scheduled_fetch_active = True

    with patch("src.main.collect_and_store") as mock_collect:
        _run(_push_daily_report(settings, db_conn, feishu_client))

    # collect_and_store should NOT have been called
    mock_collect.assert_not_called()
    # Nothing should be sent
    feishu_client.send_text.assert_not_called()
    feishu_client.send_card.assert_not_called()

    sched_mod._scheduled_fetch_active = False


def test_start_scheduler_idempotent():
    """Calling start_scheduler twice should not create a second scheduler."""
    from src.bot.scheduler import start_scheduler
    import src.bot.scheduler as sched_mod

    sched_mod._scheduler = None

    settings = Settings(target_chat_id="oc_test", report_cron="30 8 * * 1-5")
    db_conn = MagicMock()
    feishu_client = MagicMock()

    with patch("apscheduler.schedulers.asyncio.AsyncIOScheduler") as mock_cls:
        mock_scheduler = MagicMock()
        mock_cls.return_value = mock_scheduler

        start_scheduler(settings, db_conn, feishu_client)
        start_scheduler(settings, db_conn, feishu_client)

        # Should only create one scheduler instance
        mock_cls.assert_called_once()
        mock_scheduler.add_job.assert_called_once()

    sched_mod._scheduler = None


def test_start_scheduler_invalid_cron_uses_default():
    """Invalid cron expression falls back to default (08:30 mon-fri)."""
    from src.bot.scheduler import start_scheduler
    import src.bot.scheduler as sched_mod

    sched_mod._scheduler = None

    settings = Settings(target_chat_id="oc_test", report_cron="invalid")
    db_conn = MagicMock()
    feishu_client = MagicMock()

    with patch("apscheduler.schedulers.asyncio.AsyncIOScheduler") as mock_cls:
        mock_scheduler = MagicMock()
        mock_cls.return_value = mock_scheduler

        start_scheduler(settings, db_conn, feishu_client)

        mock_scheduler.add_job.assert_called_once()

    sched_mod._scheduler = None


# ── APScheduler fire-time integration test ────────────────────────────────


def test_cron_trigger_fires_monday_friday_0830():
    """Verify that the parsed '30 8 * * 1-5' trigger fires Mon-Fri at 08:30 only."""
    from apscheduler.triggers.cron import CronTrigger
    from src.bot.scheduler import _convert_cron_dow

    CST = timezone(timedelta(hours=8))
    cron_parts = "30 8 * * 1-5".split()
    dow = _convert_cron_dow(cron_parts[4])
    trigger = CronTrigger(
        minute=cron_parts[0],
        hour=cron_parts[1],
        day=cron_parts[2],
        month=cron_parts[3],
        day_of_week=dow,
    )

    # Start from a Sunday, iterate 20 fire times
    now = datetime(2025, 4, 13, 0, 0, tzinfo=CST)  # Sunday
    for _ in range(20):
        fire = trigger.get_next_fire_time(None, now)
        assert fire is not None, "Ran out of fire times"
        dt = fire.astimezone(CST)
        assert dt.weekday() < 5, f"Fire on weekend: {dt.strftime('%A %Y-%m-%d')}"
        assert dt.hour == 8, f"Wrong hour: {dt.hour}"
        assert dt.minute == 30, f"Wrong minute: {dt.minute}"
        now = fire + timedelta(seconds=1)
