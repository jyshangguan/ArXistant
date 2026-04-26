"""End-to-end mock tests for the Understanding Verifier in Feishu bot context.

These tests mock the LLM and Feishu client to verify the full /read → verification
flow works correctly, including progress cards and result cards.
"""

import asyncio
import json
from unittest.mock import MagicMock, patch, AsyncMock

import pytest


# ── Fixtures ─────────────────────────────────────────────────────────────


@pytest.fixture
def mock_settings():
    from src.config import Settings
    return Settings(
        feishu_app_id="test",
        feishu_app_secret="test",
        feishu_bot_name="ArXistant",
        target_chat_id="oc_test",
        db_path="data/arxistant.db",
        llm_api_key="test-key",
        llm_model="test-model",
        verifier_enabled=True,
        verifier_max_points=2,
        verifier_max_iterations=1,
        verifier_run_feynman=True,
        verifier_feynman_importance_threshold=1,
        verifier_max_context_chars=5000,
        verifier_store_certificates=False,
    )


@pytest.fixture
def db_conn():
    from src.storage import init_db
    conn = init_db(":memory:")
    yield conn
    conn.close()


# ── LLM responses for the verifier pipeline ──────────────────────────────


SAMPLE_EXTRACT_RESPONSE = json.dumps({
    "points": [
        {
            "point_id": "P1",
            "point_type": "main_result",
            "original_text": "SFR is 5 Msun/yr",
            "normalized_question": "What is the star formation rate?",
            "importance": 5,
            "reason_for_importance": "Main result",
        },
    ]
})

SAMPLE_CHAIN_RESPONSE = json.dumps({
    "question": "What is the star formation rate?",
    "claim": "SFR = 5 Msun/yr",
    "evidence": [{"source_type": "abstract", "source_id": "", "quote_or_summary": "SFR = 5",
                  "relevance": "direct", "supports_claim": True, "confidence": 0.9}],
    "reasoning_chain": ["Measure UV", "Convert to SFR"],
    "assumptions": ["Calibration valid"],
    "caveats": ["Dust uncertain"],
    "alternative_explanations": [],
    "conclusion": "SFR is 5 Msun/yr",
})

SAMPLE_REVIEW_RESPONSE = json.dumps({
    "logic_score": 8,
    "strengths": ["Direct evidence"],
    "weaknesses": ["Dust correction uncertain"],
    "missing_evidence": [],
    "unsupported_claims": [],
    "hidden_assumptions": [],
    "alternative_explanations": [],
})

SAMPLE_FEYNMAN_RESPONSE = json.dumps({
    "simple_explanation": "The galaxy forms 5 solar masses of stars per year.",
    "first_principles_explanation": "Young stars emit UV. UV luminosity -> SFR via calibration.",
    "jargon_terms": {"SFR": "Star Formation Rate"},
    "transfer_test": "For a metal-poor galaxy, adjust calibration.",
})

SAMPLE_FEYNMAN_CRITIQUE_RESPONSE = json.dumps({
    "feynman_score": 7,
    "passed": False,
    "critic_comments": ["Good simple explanation"],
    "missing_mechanisms": [],
    "unsupported_additions": [],
    "transfer_test_quality": "good",
})

SAMPLE_GAPS_RESPONSE = json.dumps({
    "remaining_gaps": [],
    "recommended_followup": [],
    "gap_details": [],
})


# ── Tests ─────────────────────────────────────────────────────────────────


class TestVerifierCommandFlow:
    """Test the /read → verification flow with mocked LLM and Feishu."""

    def test_read_triggers_verification(self, mock_settings, db_conn):
        """Verify that /read command triggers verification after executive summary."""
        from src.bot.command_handler import handle_command
        from src.bot.command_router import parse_command
        from src.tools.types import ReadingNote
        from src.tools.html_parser import ParsedPaper
        from src.tools.understanding_types import ScientificPoint, UnderstandingCertificate
        from src.tools.understanding_verifier import VerificationProgress

        # Pre-built responses to avoid threading mock issues
        test_point = ScientificPoint("P1", "main_result", "SFR=5", "What is SFR?", 5, "Main")
        test_cert = UnderstandingCertificate(
            arxiv_id="2604.12345",
            point=test_point,
            logic_chain=MagicMock(),
            logic_review=MagicMock(score=8),
            feynman_test=MagicMock(score=7),
            understanding_level="argument_understood",
            verified=True,
        )
        test_progress = VerificationProgress(
            arxiv_id="2604.12345", total_points=1, current_point_index=1,
            current_stage="done", completed_certificates=[test_cert],
        )

        async def _run():
            mock_feishu = AsyncMock()
            mock_feishu.reply_text = AsyncMock()
            mock_feishu.reply_card = AsyncMock()
            mock_feishu.send_text = AsyncMock()
            mock_feishu.send_card = AsyncMock()

            mock_note = ReadingNote(
                arxiv_id="2604.12345", title="Test Paper", authors="Author et al.",
                background="Background", key_findings=["Finding 1"], evaluation="Good",
                tree_connections=[],
            )
            mock_parsed = ParsedPaper(
                arxiv_id="2604.12345", title="Test Paper", abstract="Abstract",
                sections=[{"number": "1", "title": "Intro", "text": "Intro text"}],
                figures=[], full_text_markdown="## Abstract\nAbstract\n\n## Intro\nIntro\n\n",
                full_text_hash="test_hash",
            )

            cmd = parse_command("/read 2604.12345")

            # Pre-execute the verification simulation
            async def mock_start_verification_inner():
                await mock_feishu.send_text("oc_test", "Starting verification for 2604.12345...\nExtracting scientific points...")
                from src.bot.card_builder import build_verification_plan_card
                plan_card = build_verification_plan_card("2604.12345", "Test Paper", [test_point])
                await mock_feishu.send_card("oc_test", plan_card)
                from src.bot.card_builder import build_verification_progress_card, build_verification_result_card
                progress_card = build_verification_progress_card(test_progress)
                await mock_feishu.send_card("oc_test", progress_card)
                result_card = build_verification_result_card("2604.12345", [test_cert])
                await mock_feishu.send_card("oc_test", result_card)

            # Create a coroutine that runs the simulation
            async def mock_start_verification(*a, **kw):
                await mock_start_verification_inner()
                return [test_cert]

            with patch("src.tools.read_paper.read_paper", return_value=mock_note), \
                 patch("src.tools.html_parser.fetch_and_parse", return_value=mock_parsed), \
                 patch("src.tools.scan_paper.scan_paper"), \
                 patch("src.bot.command_handler._ensure_paper_scanned", new_callable=AsyncMock), \
                 patch("src.tools.read_paper.format_tree_for_prompt", return_value="Tree"), \
                 patch("src.bot.command_handler._start_background_verification", mock_start_verification):

                await handle_command(
                    cmd, "oc_test", "msg_001", "/read 2604.12345",
                    feishu=mock_feishu, db=db_conn, settings=mock_settings, req_id="test",
                )
                # Give background verification task a chance to run
                await asyncio.sleep(0.1)
                await handle_command(
                    cmd, "oc_test", "msg_001", "/read 2604.12345",
                    feishu=mock_feishu, db=db_conn, settings=mock_settings, req_id="test",
                )
                await asyncio.sleep(0.1)

            # Verify Feishu interactions
            assert mock_feishu.reply_text.called, "Should send 'Processing...' text"
            assert mock_feishu.reply_card.called, "Should send reading note card"

            verification_texts = [c[0][1] for c in mock_feishu.send_text.call_args_list]
            has_start = any("Starting verification" in t for t in verification_texts)
            assert has_start, f"Should send 'Starting verification...' text. Got: {verification_texts}"

            card_calls = [c[0][1] for c in mock_feishu.send_card.call_args_list]
            def _title(c):
                t = c.get("header", {}).get("title", "")
                return t.get("content", "") if isinstance(t, dict) else str(t)

            plan_cards = [c for c in card_calls if isinstance(c, dict)
                          and "Verification Plan" in _title(c)]
            assert len(plan_cards) > 0, f"Should send verification plan card. Got {len(card_calls)} card calls: {card_calls}"

            result_cards = [c for c in card_calls if isinstance(c, dict)
                           and "Verification Result" in _title(c)]
            assert len(result_cards) > 0, "Should send verification result card"

        asyncio.run(_run())


class TestVerifierCardCallbacks:
    """Test verifier-specific card callbacks."""

    def test_verifier_answer_callback(self, mock_settings, db_conn):
        from src.bot.command_handler import handle_card_callback
        from src.bot.verifier_runner import _user_responses

        async def _run():
            mock_feishu = AsyncMock()
            mock_feishu.send_text = AsyncMock()

            loop = asyncio.get_event_loop()
            future = loop.create_future()
            _user_responses["test_q1"] = future

            await handle_card_callback(
                callback_type="verifier_answer", arxiv_id="", chat_id="oc_test",
                feishu=mock_feishu, db=db_conn, settings=mock_settings,
                question_id="test_q1", response="I know about this topic",
            )

            assert future.done(), "Future should be resolved"
            assert future.result() == "I know about this topic"
            mock_feishu.send_text.assert_called()

        asyncio.run(_run())

    def test_verifier_skip_callback(self, mock_settings, db_conn):
        from src.bot.command_handler import handle_card_callback
        from src.bot.verifier_runner import _user_responses

        async def _run():
            mock_feishu = AsyncMock()
            loop = asyncio.get_event_loop()
            future = loop.create_future()
            _user_responses["test_q2"] = future

            await handle_card_callback(
                callback_type="verifier_skip_point", arxiv_id="", chat_id="oc_test",
                feishu=mock_feishu, db=db_conn, settings=mock_settings,
                question_id="test_q2",
            )

            assert future.done()
            assert future.result() == "__SKIP__"

        asyncio.run(_run())

    def test_verifier_abort_callback(self, mock_settings, db_conn):
        from src.bot.command_handler import handle_card_callback
        from src.bot.verifier_runner import _active_verifications

        async def _run():
            mock_feishu = AsyncMock()
            mock_task = MagicMock()
            mock_task.done.return_value = False
            _active_verifications["oc_test"] = mock_task

            await handle_card_callback(
                callback_type="verifier_abort", arxiv_id="", chat_id="oc_test",
                feishu=mock_feishu, db=db_conn, settings=mock_settings,
                question_id="test_q3",
            )

            mock_task.cancel.assert_called_once()
            mock_feishu.send_text.assert_called()
            _active_verifications.pop("oc_test", None)

        asyncio.run(_run())


class TestCardBuilders:
    """Test that card builders produce valid Feishu card structures."""

    def test_verification_plan_card_structure(self):
        from src.bot.card_builder import build_verification_plan_card
        from src.tools.understanding_types import ScientificPoint

        points = [
            ScientificPoint("P1", "main_result", "SFR=5", "What is SFR?", 5, "Main"),
            ScientificPoint("P2", "method", "Used SED fitting", "How?", 3, "Method"),
        ]
        card = build_verification_plan_card("2604.12345", "Test Paper", points)

        assert card["config"]["wide_screen_mode"] is True
        assert "Verification Plan" in card["header"]["title"]["content"]
        assert len(card["elements"]) >= 3

    def test_verification_progress_card_structure(self):
        from src.bot.card_builder import build_verification_progress_card
        from src.tools.understanding_types import (
            VerificationProgress, ScientificPoint, UnderstandingCertificate,
            LogicChain, LogicReviewResult, FeynmanTestResult,
        )

        cert = UnderstandingCertificate(
            arxiv_id="2604.12345",
            point=ScientificPoint("P1", "main_result", "text", "Q?", 5),
            logic_chain=LogicChain(question="Q?", claim="C"),
            logic_review=LogicReviewResult(score=8),
            feynman_test=FeynmanTestResult(simple_explanation="", first_principles_explanation="", score=7),
            understanding_level="argument_understood",
        )
        progress = VerificationProgress(
            arxiv_id="2604.12345", total_points=3, current_point_index=2,
            current_point=ScientificPoint("P2", "method", "text", "Q2?", 4),
            current_stage="feynman_test", completed_certificates=[cert],
        )
        card = build_verification_progress_card(progress)

        assert "Verification Progress" in card["header"]["title"]["content"]
        elements_text = json.dumps(card["elements"])
        assert "Q?" in elements_text
        assert "Logic 8" in elements_text

    def test_verification_result_card_structure(self):
        from src.bot.card_builder import build_verification_result_card
        from src.tools.understanding_types import (
            UnderstandingCertificate, ScientificPoint, LogicChain,
            LogicReviewResult, FeynmanTestResult,
        )

        cert = UnderstandingCertificate(
            arxiv_id="2604.12345",
            point=ScientificPoint("P1", "main_result", "text", "Q?", 5),
            logic_chain=LogicChain(question="Q?", claim="C"),
            logic_review=LogicReviewResult(score=9),
            feynman_test=FeynmanTestResult(simple_explanation="", first_principles_explanation="", score=9),
            understanding_level="critically_understood", overall_score=18, verified=True,
        )
        card = build_verification_result_card("2604.12345", [cert])

        assert "Verification Result" in card["header"]["title"]["content"]
        assert card["header"]["template"] == "green"
        actions = [e for e in card["elements"] if e.get("tag") == "action"]
        assert len(actions) > 0

    def test_verifier_question_card_structure(self):
        from src.bot.card_builder import build_verifier_question_card

        card = build_verifier_question_card(
            question_id="q1", point_id="P1",
            question_text="Cannot determine if stellar absorption was excluded.",
            options=["I know about this", "Skip this point"],
        )

        assert "Needs Your Input" in card["header"]["title"]["content"]
        assert card["header"]["template"] == "orange"

        actions = [e for e in card["elements"] if e.get("tag") == "action"]
        assert len(actions) > 0
        buttons = actions[0].get("actions", [])
        button_values = [b.get("value", {}).get("type") for b in buttons]
        assert "verifier_answer" in button_values
        assert "verifier_skip_point" in button_values
        assert "verifier_abort" in button_values

    def test_verification_summary_inline(self):
        from src.bot.card_builder import build_verification_summary_inline

        certs = [
            {"understanding_level": "critically_understood"},
            {"understanding_level": "partially_understood"},
        ]
        summary = build_verification_summary_inline(certs)
        assert "2 points checked" in summary
        assert "critically understood" in summary
        assert "partially understood" in summary

    def test_verification_summary_empty(self):
        from src.bot.card_builder import build_verification_summary_inline
        assert build_verification_summary_inline([]) == ""
