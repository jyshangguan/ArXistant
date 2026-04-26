"""Async orchestration layer for the Understanding Verifier.

Bridges the sync verifier (run_in_executor) with the async Feishu bot.
Manages progress reporting, user interaction, and concurrency.
"""

from __future__ import annotations

import asyncio
import json
import logging
import sqlite3
import time
from datetime import datetime, timezone

logger = logging.getLogger(__name__)

# Module-level state for active verifications
_active_verifications: dict[str, asyncio.Task] = {}  # chat_id -> Task
_user_responses: dict[str, asyncio.Future] = {}  # question_id -> Future


async def start_verification(
    arxiv_id: str,
    title: str,
    paper_context: str,
    settings,
    db: sqlite3.Connection,
    feishu,
    chat_id: str,
) -> list:
    """Start a background verification task.

    1. Send "Verification started" message
    2. Run point extraction (1 LLM call)
    3. Show verification plan card
    4. Run verification in background
    5. Send progress updates at stage transitions
    6. Send final result card
    7. Store certificates in DB

    Returns list of UnderstandingCertificate.
    """
    # Guard: prevent duplicate verification
    if chat_id in _active_verifications:
        task = _active_verifications[chat_id]
        if not task.done():
            await feishu.send_text(chat_id, "A verification is already running. Please wait.")
            return []

    await feishu.send_text(chat_id, f"Starting verification for {arxiv_id}...\nExtracting scientific points...")

    loop = asyncio.get_event_loop()
    from ..tools.understanding_verifier import extract_scientific_points

    max_points = getattr(settings, "verifier_max_points", 5)
    points = await loop.run_in_executor(
        None,
        lambda: extract_scientific_points(paper_context, settings, max_points),
    )

    if not points:
        await feishu.send_text(chat_id, f"Could not extract verifiable points from {arxiv_id}.")
        return []

    # Show verification plan
    from .card_builder import build_verification_plan_card
    plan_card = build_verification_plan_card(arxiv_id, title, points)
    await feishu.send_card(chat_id, plan_card)

    # Start background verification
    task = asyncio.create_task(
        _run_verification_bg(
            arxiv_id, title, paper_context, points, settings, db, feishu, chat_id,
        )
    )
    _active_verifications[chat_id] = task

    try:
        certificates = await task
        return certificates
    finally:
        _active_verifications.pop(chat_id, None)


async def _run_verification_bg(
    arxiv_id: str,
    title: str,
    paper_context: str,
    points: list,
    settings,
    db: sqlite3.Connection,
    feishu,
    chat_id: str,
) -> list:
    """Background task: run verification, send progress, handle user questions."""

    from ..tools.understanding_types import VerificationProgress
    from ..tools.understanding_verifier import verify_paper_understanding
    from .card_builder import build_verification_progress_card, build_verification_result_card

    progress = VerificationProgress(
        arxiv_id=arxiv_id,
        total_points=len(points),
        current_point_index=0,
        start_time=datetime.now(timezone.utc).strftime("%H:%M:%S UTC"),
    )

    last_progress_time = 0.0
    progress_interval = getattr(settings, "verifier_progress_interval", 30)
    loop = asyncio.get_running_loop()

    def on_progress(p: VerificationProgress) -> None:
        """Callback from sync verifier -> schedule async Feishu update."""
        nonlocal last_progress_time
        now = time.time()
        # Throttle: only send update if enough time has passed or we're at a milestone
        is_milestone = p.current_stage in ("certificate", "done", "extracting")
        if is_milestone or (now - last_progress_time) >= progress_interval:
            last_progress_time = now
            try:
                loop.call_soon_threadsafe(_schedule_progress, feishu, chat_id, p)
            except RuntimeError:
                pass  # Event loop closed (bot shutting down)

    certificates = await loop.run_in_executor(
        None,
        lambda: verify_paper_understanding(
            arxiv_id, title, paper_context, settings,
            max_points=len(points),
            max_iterations=getattr(settings, "verifier_max_iterations", 1),
            progress_callback=on_progress,
        ),
    )

    # Send final result card
    if certificates:
        result_card = build_verification_result_card(arxiv_id, certificates)
        await feishu.send_card(chat_id, result_card)

        # Store certificates in DB
        if getattr(settings, "verifier_store_certificates", True):
            from ..tools.html_parser import fetch_and_parse
            try:
                parsed = await loop.run_in_executor(
                    None,
                    lambda: fetch_and_parse(arxiv_id, timeout=getattr(settings, "html_timeout", 30)),
                )
                text_hash = parsed.full_text_hash
            except Exception:
                text_hash = ""

            for cert in certificates:
                _store_certificate(db, cert, text_hash)

    return certificates


def _schedule_progress(feishu, chat_id: str, progress) -> None:
    """Schedule a progress card send (called via call_soon_threadsafe)."""
    asyncio.create_task(_send_progress_card(feishu, chat_id, progress))


async def _send_progress_card(feishu, chat_id: str, progress) -> None:
    """Send a progress update card to Feishu."""
    from .card_builder import build_verification_progress_card
    card = build_verification_progress_card(progress)
    try:
        await feishu.send_card(chat_id, card)
    except Exception:
        logger.exception("Failed to send progress card")


async def ask_user_question(
    question_id: str,
    point_id: str,
    question_text: str,
    feishu,
    chat_id: str,
    options: list[str] | None = None,
    timeout: float = 300.0,
) -> str | None:
    """Ask the user a question and wait for response.

    Sends a question card, creates an asyncio.Future, and waits for either:
    - A card callback (button click) to resolve the Future
    - A text message to resolve the Future
    - Timeout (default 5 minutes)

    Returns user's response text, or None on timeout.
    """
    from .card_builder import build_verifier_question_card

    future: asyncio.Future = asyncio.get_event_loop().create_future()
    _user_responses[question_id] = future

    card = build_verifier_question_card(question_id, point_id, question_text, options)
    await feishu.send_card(chat_id, card)

    try:
        result = await asyncio.wait_for(future, timeout=timeout)
        return result
    except asyncio.TimeoutError:
        logger.warning("User question timed out: %s", question_id)
        return None
    finally:
        _user_responses.pop(question_id, None)


def resolve_user_response(question_id: str, response: str) -> bool:
    """Resolve a pending user question with the user's response.

    Called from the message handler when the user replies.
    Returns True if the question was found and resolved.
    """
    future = _user_responses.get(question_id)
    if future is None or future.done():
        return False
    future.set_result(response)
    return True


def has_pending_questions(chat_id: str = "") -> bool:
    """Check if there are any pending user questions."""
    return len(_user_responses) > 0


def _store_certificate(db: sqlite3.Connection, cert, text_hash: str) -> None:
    """Store a certificate in the database."""
    from ..storage import upsert_understanding_certificate
    cert_json = json.dumps({
        "point_id": cert.point.point_id,
        "point_type": cert.point.point_type,
        "original_text": cert.point.original_text,
        "normalized_question": cert.point.normalized_question,
        "importance": cert.point.importance,
        "logic_chain": {
            "claim": cert.logic_chain.claim,
            "conclusion": cert.logic_chain.conclusion,
            "evidence": [{"source_type": e.source_type, "source_id": e.source_id,
                          "quote_or_summary": e.quote_or_summary} for e in cert.logic_chain.evidence],
            "reasoning_chain": cert.logic_chain.reasoning_chain,
            "assumptions": cert.logic_chain.assumptions,
            "caveats": cert.logic_chain.caveats,
        },
        "logic_review": {
            "score": cert.logic_review.score,
            "strengths": cert.logic_review.strengths[:3],
            "weaknesses": cert.logic_review.weaknesses[:3],
        },
        "feynman_test": {
            "simple_explanation": cert.feynman_test.simple_explanation[:500],
            "first_principles_explanation": cert.feynman_test.first_principles_explanation[:500],
            "score": cert.feynman_test.score,
        },
        "overall_score": cert.overall_score,
        "understanding_level": cert.understanding_level,
        "remaining_gaps": cert.remaining_gaps,
        "verified": cert.verified,
    }, ensure_ascii=False, default=str)

    upsert_understanding_certificate(
        db,
        arxiv_id=cert.arxiv_id,
        point_id=cert.point.point_id,
        point_type=cert.point.point_type,
        question=cert.point.normalized_question,
        claim=cert.logic_chain.claim,
        logic_score=cert.logic_review.score,
        feynman_score=cert.feynman_test.score,
        overall_score=cert.overall_score,
        understanding_level=cert.understanding_level,
        verified=cert.verified,
        certificate_json=cert_json,
        full_text_hash=text_hash,
    )
