"""Data models for the Understanding Verifier."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Literal

UnderstandingLevel = Literal[
    "not_understood",
    "partially_understood",
    "argument_understood",
    "mechanism_understood",
    "critically_understood",
]


@dataclass
class EvidenceItem:
    source_type: str  # abstract, section, figure, table, equation, reference, user_note
    source_id: str = ""
    quote_or_summary: str = ""
    relevance: str = ""
    supports_claim: bool = True
    confidence: float = 0.5


@dataclass
class ScientificPoint:
    point_id: str
    point_type: str  # main_result, method, interpretation, caveat, novelty, comparison, implication
    original_text: str
    normalized_question: str
    importance: int = 3  # 1-5
    reason_for_importance: str = ""


@dataclass
class LogicChain:
    question: str
    claim: str
    evidence: list[EvidenceItem] = field(default_factory=list)
    reasoning_chain: list[str] = field(default_factory=list)
    assumptions: list[str] = field(default_factory=list)
    caveats: list[str] = field(default_factory=list)
    alternative_explanations: list[str] = field(default_factory=list)
    conclusion: str = ""


@dataclass
class FeynmanTestResult:
    simple_explanation: str
    first_principles_explanation: str
    jargon_terms: dict[str, str] = field(default_factory=dict)
    transfer_test: str = ""
    critic_comments: list[str] = field(default_factory=list)
    score: int = 0  # 0-10


@dataclass
class LogicReviewResult:
    score: int = 0  # 0-10
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    hidden_assumptions: list[str] = field(default_factory=list)
    alternative_explanations: list[str] = field(default_factory=list)


@dataclass
class VerificationGap:
    gap_description: str
    gap_type: str  # evidence_missing, method_unclear, user_judgment_needed, reference_needed
    severity: str  # critical, moderate, minor
    point_id: str = ""


@dataclass
class UnderstandingCertificate:
    arxiv_id: str
    point: ScientificPoint
    logic_chain: LogicChain
    logic_review: LogicReviewResult
    feynman_test: FeynmanTestResult
    understanding_level: UnderstandingLevel
    overall_score: int = 0  # 0-20
    remaining_gaps: list[str] = field(default_factory=list)
    recommended_followup: list[str] = field(default_factory=list)
    verified: bool = False


@dataclass
class VerificationProgress:
    """Progress state for a running verification, used to build Feishu progress cards."""
    arxiv_id: str
    total_points: int
    current_point_index: int
    current_point: ScientificPoint | None = None
    current_stage: str = ""  # extracting, logic_chain, logic_review, feynman_test, feynman_review, gaps, certificate
    completed_certificates: list[UnderstandingCertificate] = field(default_factory=list)
    failed_points: list[str] = field(default_factory=list)
    start_time: str = ""
    llm_calls_made: int = 0


@dataclass
class UserQuestion:
    """A question sent to the user during verification, with an awaitable response."""
    question_id: str
    point_id: str
    question_text: str
    options: list[str] | None = None
    user_response: str | None = None
    answered: bool = False


def determine_understanding_level(
    logic_score: int,
    feynman_score: int,
    gaps: list[str],
) -> UnderstandingLevel:
    """Map scores and gaps to understanding level."""
    if logic_score >= 8 and feynman_score >= 8 and not _has_critical_gap(gaps):
        return "critically_understood"
    if logic_score >= 8 and feynman_score < 8:
        return "argument_understood"
    if logic_score < 8 and feynman_score >= 8:
        return "mechanism_understood"
    if logic_score + feynman_score >= 6:
        return "partially_understood"
    return "not_understood"


def _has_critical_gap(gaps: list[str]) -> bool:
    """Check if any gap description contains critical keywords."""
    critical_keywords = [
        "no evidence",
        "cannot locate",
        "missing main figure",
        "unsupported",
        "contradiction",
    ]
    text = " ".join(gaps).lower()
    return any(k in text for k in critical_keywords)
