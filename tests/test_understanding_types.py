"""Tests for understanding_types module."""

from src.tools.understanding_types import (
    ScientificPoint,
    EvidenceItem,
    LogicChain,
    LogicReviewResult,
    FeynmanTestResult,
    VerificationGap,
    UnderstandingCertificate,
    VerificationProgress,
    determine_understanding_level,
    UnderstandingLevel,
)


class TestDetermineUnderstandingLevel:
    def test_critically_understood(self):
        level = determine_understanding_level(9, 9, [])
        assert level == "critically_understood"

    def test_critically_understood_threshold(self):
        level = determine_understanding_level(8, 8, [])
        assert level == "critically_understood"

    def test_critically_understood_blocked_by_critical_gap(self):
        level = determine_understanding_level(9, 9, ["no evidence for this claim"])
        assert level == "partially_understood"

    def test_argument_understood(self):
        level = determine_understanding_level(8, 5, [])
        assert level == "argument_understood"

    def test_argument_understood_low_feynman(self):
        level = determine_understanding_level(9, 2, [])
        assert level == "argument_understood"

    def test_mechanism_understood(self):
        level = determine_understanding_level(5, 9, [])
        assert level == "mechanism_understood"

    def test_mechanism_understood_low_logic(self):
        level = determine_understanding_level(2, 9, [])
        assert level == "mechanism_understood"

    def test_partially_understood(self):
        level = determine_understanding_level(6, 6, [])
        assert level == "partially_understood"

    def test_not_understood_low_total(self):
        level = determine_understanding_level(2, 2, [])
        assert level == "not_understood"

    def test_not_understood_zero(self):
        level = determine_understanding_level(0, 0, [])
        assert level == "not_understood"

    def test_critical_gap_cannot_locate(self):
        level = determine_understanding_level(9, 9, ["cannot locate the relevant figure"])
        assert level != "critically_understood"

    def test_critical_gap_contradiction(self):
        level = determine_understanding_level(9, 9, ["contradiction in the data"])
        assert level != "critically_understood"

    def test_non_critical_gap(self):
        level = determine_understanding_level(9, 9, ["minor caveat about sample size"])
        assert level == "critically_understood"


class TestDataclasses:
    def test_scientific_point(self):
        p = ScientificPoint(
            point_id="P1",
            point_type="main_result",
            original_text="The Universe is expanding",
            normalized_question="Is the Universe expanding?",
            importance=5,
        )
        assert p.point_id == "P1"
        assert p.importance == 5

    def test_evidence_item(self):
        e = EvidenceItem(
            source_type="section",
            source_id="Results",
            quote_or_summary="The data shows...",
            supports_claim=True,
            confidence=0.9,
        )
        assert e.source_type == "section"
        assert e.supports_claim is True

    def test_logic_chain(self):
        chain = LogicChain(
            question="Q?",
            claim="C",
            evidence=[EvidenceItem(source_type="abstract", quote_or_summary="ev")],
            reasoning_chain=["step1", "step2"],
            assumptions=["a1"],
            caveats=["c1"],
        )
        assert len(chain.evidence) == 1
        assert len(chain.reasoning_chain) == 2

    def test_feynman_test_result(self):
        f = FeynmanTestResult(
            simple_explanation="It works like this...",
            first_principles_explanation="From basic physics...",
            jargon_terms={"AGN": "Active Galactic Nucleus"},
            transfer_test="If we apply this to...",
        )
        assert f.jargon_terms["AGN"] == "Active Galactic Nucleus"

    def test_logic_review_result(self):
        r = LogicReviewResult(
            score=8,
            strengths=["well supported"],
            weaknesses=["small sample"],
            missing_evidence=["no error bars"],
        )
        assert r.score == 8

    def test_verification_gap(self):
        g = VerificationGap(
            gap_description="Missing figure analysis",
            gap_type="figure_analysis_needed",
            severity="moderate",
            point_id="P1",
        )
        assert g.severity == "moderate"

    def test_understanding_certificate(self):
        point = ScientificPoint("P1", "main_result", "text", "Q?", 5)
        chain = LogicChain(question="Q?", claim="C")
        review = LogicReviewResult(score=8)
        feynman = FeynmanTestResult(simple_explanation="...", first_principles_explanation="...", score=9)
        cert = UnderstandingCertificate(
            arxiv_id="2604.12345",
            point=point,
            logic_chain=chain,
            logic_review=review,
            feynman_test=feynman,
            understanding_level="critically_understood",
            overall_score=17,
            verified=True,
        )
        assert cert.overall_score == 17
        assert cert.verified is True

    def test_verification_progress(self):
        progress = VerificationProgress(
            arxiv_id="2604.12345",
            total_points=3,
            current_point_index=1,
            current_stage="feynman_test",
        )
        assert progress.total_points == 3
        assert progress.current_stage == "feynman_test"

    def test_certificate_serialization(self):
        import dataclasses
        cert = UnderstandingCertificate(
            arxiv_id="2604.12345",
            point=ScientificPoint("P1", "main_result", "text", "Q?", 5),
            logic_chain=LogicChain(question="Q?", claim="C"),
            logic_review=LogicReviewResult(),
            feynman_test=FeynmanTestResult(simple_explanation="", first_principles_explanation=""),
            understanding_level="not_understood",
        )
        # Should be serializable via dataclasses.asdict
        d = dataclasses.asdict(cert)
        assert d["arxiv_id"] == "2604.12345"
        assert isinstance(d["logic_chain"]["evidence"], list)
