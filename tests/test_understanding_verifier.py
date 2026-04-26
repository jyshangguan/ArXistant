"""Tests for understanding_verifier module."""

import json
from unittest.mock import MagicMock, patch

import pytest

from src.tools.understanding_types import (
    ScientificPoint,
    EvidenceItem,
    LogicChain,
    LogicReviewResult,
    FeynmanTestResult,
    UnderstandingCertificate,
    VerificationProgress,
)
from src.tools.understanding_verifier import (
    extract_scientific_points,
    build_logic_chain,
    critique_logic_chain,
    run_feynman_test,
    critique_feynman_test,
    identify_gaps,
    verify_single_point,
    verify_paper_understanding,
    _call_llm,
    _find_relevant_sections,
)


def _make_settings(**overrides):
    """Create a mock Settings object."""
    from src.config import Settings
    defaults = dict(
        llm_model="test-model",
        llm_base_url="https://api.test.com",
        llm_api_key="test-key",
        llm_temperature=0.1,
        verifier_max_points=5,
        verifier_max_iterations=1,
        verifier_run_feynman=True,
        verifier_feynman_importance_threshold=4,
        verifier_max_context_chars=20000,
    )
    defaults.update(overrides)
    return Settings(**defaults)


SAMPLE_POINTS_RESPONSE = json.dumps({
    "points": [
        {
            "point_id": "P1",
            "point_type": "main_result",
            "original_text": "The star formation rate is 5 Msun/yr",
            "normalized_question": "What is the star formation rate?",
            "importance": 5,
            "reason_for_importance": "Main result of the paper",
        },
        {
            "point_id": "P2",
            "point_type": "method",
            "original_text": "We used SED fitting",
            "normalized_question": "How was the SED fitting done?",
            "importance": 3,
            "reason_for_importance": "Key methodology",
        },
    ]
})


SAMPLE_LOGIC_CHAIN_RESPONSE = json.dumps({
    "question": "What is the star formation rate?",
    "claim": "The SFR is 5 Msun/yr",
    "evidence": [
        {
            "source_type": "section",
            "source_id": "Results",
            "quote_or_summary": "We find SFR = 5 +/- 1 Msun/yr",
            "relevance": "Direct measurement",
            "supports_claim": True,
            "confidence": 0.9,
        }
    ],
    "reasoning_chain": ["Measure UV luminosity", "Convert to SFR using Kennicutt relation"],
    "assumptions": ["Calibration is appropriate for this galaxy type"],
    "caveats": ["Dust attenuation uncertainty"],
    "alternative_explanations": ["AGN contribution could inflate UV"],
    "conclusion": "The SFR of 5 Msun/yr is consistent with the measured UV luminosity",
})


SAMPLE_LOGIC_REVIEW_RESPONSE = json.dumps({
    "logic_score": 8,
    "strengths": ["Direct measurement cited", "Calibration discussed"],
    "weaknesses": ["Dust correction uncertain"],
    "missing_evidence": ["No error propagation shown"],
    "unsupported_claims": [],
    "hidden_assumptions": ["Assumes no AGN contribution"],
    "alternative_explanations": ["AGN could contribute to UV flux"],
})


SAMPLE_FEYNMAN_RESPONSE = json.dumps({
    "simple_explanation": "The galaxy is making 5 suns worth of stars per year",
    "first_principles_explanation": "Young massive stars emit UV light. By measuring UV luminosity and converting via a calibration, we estimate how many stars are forming.",
    "jargon_terms": {
        "SFR": "Star Formation Rate - how fast a galaxy converts gas into stars",
        "SED": "Spectral Energy Distribution - how brightness varies with wavelength",
    },
    "transfer_test": "If we applied this method to a metal-poor galaxy, we would need to adjust the UV-to-SFR calibration since metallicity affects the UV output per unit SFR.",
})


SAMPLE_FEYNMAN_CRITIQUE_RESPONSE = json.dumps({
    "feynman_score": 7,
    "passed": False,
    "critic_comments": ["Simple explanation is good", "Transfer test is reasonable"],
    "missing_mechanisms": ["Kennicutt relation details not explained"],
    "unsupported_additions": [],
    "transfer_test_quality": "good",
})


SAMPLE_GAPS_RESPONSE = json.dumps({
    "remaining_gaps": ["Dust correction method not fully specified"],
    "recommended_followup": ["Check the dust attenuation section"],
    "gap_details": [
        {
            "gap_description": "Dust correction method not fully specified",
            "gap_type": "method_unclear",
            "severity": "moderate",
        }
    ],
})


@patch("src.tools.understanding_verifier.create_client")
@patch("src.tools.understanding_verifier.chat_completion")
class TestExtractScientificPoints:
    def test_extracts_points(self, mock_chat, mock_client):
        mock_client.return_value = MagicMock()
        mock_chat.return_value = SAMPLE_POINTS_RESPONSE

        settings = _make_settings()
        points = extract_scientific_points("Paper about galaxies...", settings, max_points=5)

        assert len(points) == 2
        assert points[0].point_id == "P1"
        assert points[0].importance == 5
        # Should be sorted by importance descending
        assert points[0].importance >= points[1].importance

    def test_respects_max_points(self, mock_chat, mock_client):
        mock_client.return_value = MagicMock()
        mock_chat.return_value = SAMPLE_POINTS_RESPONSE

        settings = _make_settings()
        points = extract_scientific_points("Paper...", settings, max_points=1)
        assert len(points) <= 1

    def test_handles_empty_response(self, mock_chat, mock_client):
        mock_client.return_value = MagicMock()
        mock_chat.return_value = "not json"

        settings = _make_settings()
        points = extract_scientific_points("Paper...", settings)
        assert points == []

    def test_handles_missing_points_key(self, mock_chat, mock_client):
        mock_client.return_value = MagicMock()
        mock_chat.return_value = json.dumps({"data": []})

        settings = _make_settings()
        points = extract_scientific_points("Paper...", settings)
        assert points == []


@patch("src.tools.understanding_verifier.create_client")
@patch("src.tools.understanding_verifier.chat_completion")
class TestBuildLogicChain:
    def test_builds_chain(self, mock_chat, mock_client):
        mock_client.return_value = MagicMock()
        mock_chat.return_value = SAMPLE_LOGIC_CHAIN_RESPONSE

        point = ScientificPoint("P1", "main_result", "SFR=5", "What is SFR?", 5)
        settings = _make_settings()
        chain = build_logic_chain(point, "Paper text...", settings)

        assert chain.question == "What is the star formation rate?"
        assert chain.claim == "The SFR is 5 Msun/yr"
        assert len(chain.evidence) == 1
        assert chain.evidence[0].source_type == "section"

    def test_handles_malformed_response(self, mock_chat, mock_client):
        mock_client.return_value = MagicMock()
        mock_chat.return_value = "broken"

        point = ScientificPoint("P1", "main_result", "text", "Q?", 5)
        settings = _make_settings()
        chain = build_logic_chain(point, "Paper...", settings)

        assert chain.question == "Q?"
        assert chain.claim == ""


@patch("src.tools.understanding_verifier.create_client")
@patch("src.tools.understanding_verifier.chat_completion")
class TestCritiqueLogicChain:
    def test_critiques_chain(self, mock_chat, mock_client):
        mock_client.return_value = MagicMock()
        mock_chat.return_value = SAMPLE_LOGIC_REVIEW_RESPONSE

        point = ScientificPoint("P1", "main_result", "text", "Q?", 5)
        chain = LogicChain(question="Q?", claim="C", evidence=[EvidenceItem(source_type="abstract")])
        settings = _make_settings()
        review = critique_logic_chain(point, chain, settings)

        assert review.score == 8
        assert len(review.strengths) == 2
        assert len(review.weaknesses) == 1


@patch("src.tools.understanding_verifier.create_client")
@patch("src.tools.understanding_verifier.chat_completion")
class TestRunFeynmanTest:
    def test_runs_feynman(self, mock_chat, mock_client):
        mock_client.return_value = MagicMock()
        mock_chat.return_value = SAMPLE_FEYNMAN_RESPONSE

        point = ScientificPoint("P1", "main_result", "text", "Q?", 5)
        chain = LogicChain(question="Q?", claim="C")
        settings = _make_settings()
        result = run_feynman_test(point, chain, settings)

        assert "sun" in result.simple_explanation.lower() or "star" in result.simple_explanation.lower()
        assert "SFR" in result.jargon_terms
        assert result.transfer_test != ""


@patch("src.tools.understanding_verifier.create_client")
@patch("src.tools.understanding_verifier.chat_completion")
class TestCritiqueFeynmanTest:
    def test_critiques_feynman(self, mock_chat, mock_client):
        mock_client.return_value = MagicMock()
        mock_chat.return_value = SAMPLE_FEYNMAN_CRITIQUE_RESPONSE

        point = ScientificPoint("P1", "main_result", "text", "Q?", 5)
        chain = LogicChain(question="Q?", claim="C")
        feynman = FeynmanTestResult(simple_explanation="test", first_principles_explanation="test")
        settings = _make_settings()
        result = critique_feynman_test(point, chain, feynman, settings)

        assert result["feynman_score"] == 7
        assert result["transfer_test_quality"] == "good"


@patch("src.tools.understanding_verifier.create_client")
@patch("src.tools.understanding_verifier.chat_completion")
class TestIdentifyGaps:
    def test_identifies_gaps(self, mock_chat, mock_client):
        mock_client.return_value = MagicMock()
        mock_chat.return_value = SAMPLE_GAPS_RESPONSE

        point = ScientificPoint("P1", "main_result", "text", "Q?", 5)
        review = LogicReviewResult(score=8, weaknesses=["dust uncertain"])
        feynman_review = {"feynman_score": 7, "critic_comments": [], "missing_mechanisms": []}
        settings = _make_settings()
        gaps = identify_gaps(review, feynman_review, point, settings)

        assert len(gaps) == 1
        assert gaps[0].gap_type == "method_unclear"
        assert gaps[0].severity == "moderate"


class TestFindRelevantSections:
    def test_finds_matching_sections(self):
        paper = "## Abstract\nSFR is 5.\n\n## Methods\nDust correction using Calzetti law.\n\n## Results\nSFR measured."
        result = _find_relevant_sections(["dust correction method"], paper)
        assert "Methods" in result or "dust" in result.lower()

    def test_no_match(self):
        paper = "## Abstract\nSome text about stars."
        result = _find_relevant_sections(["quantum gravity"], paper)
        assert result == ""


class TestVerifySinglePoint:
    @patch("src.tools.understanding_verifier.create_client")
    @patch("src.tools.understanding_verifier.chat_completion")
    def test_produces_certificate(self, mock_chat, mock_client):
        mock_client.return_value = MagicMock()
        # Return different responses for each LLM call
        mock_chat.side_effect = [
            SAMPLE_LOGIC_CHAIN_RESPONSE,
            SAMPLE_LOGIC_REVIEW_RESPONSE,
            SAMPLE_FEYNMAN_RESPONSE,
            SAMPLE_FEYNMAN_CRITIQUE_RESPONSE,
            SAMPLE_GAPS_RESPONSE,
        ]

        point = ScientificPoint("P1", "main_result", "SFR=5", "What is SFR?", 5)
        settings = _make_settings()
        cert = verify_single_point("2604.12345", point, "Paper text...", settings)

        assert isinstance(cert, UnderstandingCertificate)
        assert cert.arxiv_id == "2604.12345"
        assert cert.logic_review.score == 8
        assert cert.feynman_test.score == 7
        assert cert.verified is True

    @patch("src.tools.understanding_verifier.create_client")
    @patch("src.tools.understanding_verifier.chat_completion")
    def test_handles_llm_failure_gracefully(self, mock_chat, mock_client):
        from openai import InternalServerError
        mock_client.return_value = MagicMock()
        mock_chat.side_effect = InternalServerError(
            message="Server error", response=MagicMock(status_code=500), body=None,
        )

        point = ScientificPoint("P1", "main_result", "text", "Q?", 5)
        settings = _make_settings()
        cert = verify_single_point("2604.12345", point, "Paper...", settings)

        assert isinstance(cert, UnderstandingCertificate)
        assert cert.verified is False
        assert cert.understanding_level == "not_understood"

    def test_progress_callback_called(self):
        calls = []
        from unittest.mock import patch

        with patch("src.tools.understanding_verifier.create_client") as mock_cc, \
             patch("src.tools.understanding_verifier.chat_completion") as mock_chat:
            mock_cc.return_value = MagicMock()
            mock_chat.side_effect = [
                SAMPLE_LOGIC_CHAIN_RESPONSE,
                SAMPLE_LOGIC_REVIEW_RESPONSE,
                SAMPLE_FEYNMAN_RESPONSE,
                SAMPLE_FEYNMAN_CRITIQUE_RESPONSE,
                SAMPLE_GAPS_RESPONSE,
            ]

            point = ScientificPoint("P1", "main_result", "text", "Q?", 5)
            settings = _make_settings()
            verify_single_point("2604.12345", point, "Paper...", settings, progress_callback=lambda s, d: calls.append(s))

        # Should have been called at least 5 times (one per stage)
        assert len(calls) >= 5


class TestVerifyPaperUnderstanding:
    @patch("src.tools.understanding_verifier.create_client")
    @patch("src.tools.understanding_verifier.chat_completion")
    def test_verifies_multiple_points(self, mock_chat, mock_client):
        mock_client.return_value = MagicMock()
        # 1 extraction call + 5 calls per point × 2 points = 11 calls
        responses = [SAMPLE_POINTS_RESPONSE]
        for _ in range(2):
            responses.extend([
                SAMPLE_LOGIC_CHAIN_RESPONSE,
                SAMPLE_LOGIC_REVIEW_RESPONSE,
                SAMPLE_FEYNMAN_RESPONSE,
                SAMPLE_FEYNMAN_CRITIQUE_RESPONSE,
                SAMPLE_GAPS_RESPONSE,
            ])
        mock_chat.side_effect = responses

        settings = _make_settings()
        certs = verify_paper_understanding("2604.12345", "Test Paper", "Paper text...", settings, max_points=5)

        assert len(certs) == 2
        assert all(isinstance(c, UnderstandingCertificate) for c in certs)

    @patch("src.tools.understanding_verifier.create_client")
    @patch("src.tools.understanding_verifier.chat_completion")
    def test_empty_extraction_returns_empty(self, mock_chat, mock_client):
        mock_client.return_value = MagicMock()
        mock_chat.return_value = json.dumps({"points": []})

        settings = _make_settings()
        certs = verify_paper_understanding("2604.12345", "Test", "Paper...", settings)
        assert certs == []

    @patch("src.tools.understanding_verifier.create_client")
    @patch("src.tools.understanding_verifier.chat_completion")
    def test_progress_callback(self, mock_chat, mock_client):
        mock_client.return_value = MagicMock()
        responses = [SAMPLE_POINTS_RESPONSE] + [
            SAMPLE_LOGIC_CHAIN_RESPONSE,
            SAMPLE_LOGIC_REVIEW_RESPONSE,
            SAMPLE_FEYNMAN_RESPONSE,
            SAMPLE_FEYNMAN_CRITIQUE_RESPONSE,
            SAMPLE_GAPS_RESPONSE,
        ]
        mock_chat.side_effect = responses

        progress_updates = []
        settings = _make_settings()
        verify_paper_understanding(
            "2604.12345", "Test", "Paper...", settings,
            max_points=1,
            progress_callback=progress_updates.append,
        )

        assert len(progress_updates) > 0
        first = progress_updates[0]
        assert isinstance(first, VerificationProgress)
