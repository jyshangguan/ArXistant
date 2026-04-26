"""Understanding Verifier: verify genuine understanding of scientific papers."""

from __future__ import annotations

import json
import logging
import time
from typing import Callable

from openai import APIConnectionError, InternalServerError, RateLimitError

from ..config import Settings
from ..llm_client import create_client, chat_completion
from .json_utils import parse_llm_json
from .understanding_prompts import (
    EXTRACT_POINTS_PROMPT,
    BUILD_LOGIC_CHAIN_PROMPT,
    CRITIQUE_LOGIC_CHAIN_PROMPT,
    FEYNMAN_TEST_PROMPT,
    CRITIQUE_FEYNMAN_PROMPT,
    IDENTIFY_GAPS_PROMPT,
    RESOLVE_GAPS_WITH_CONTEXT_PROMPT,
)
from .understanding_types import (
    EvidenceItem,
    ScientificPoint,
    LogicChain,
    FeynmanTestResult,
    LogicReviewResult,
    VerificationGap,
    UnderstandingCertificate,
    VerificationProgress,
    UnderstandingLevel,
    determine_understanding_level,
)

logger = logging.getLogger(__name__)


def _call_llm(
    settings: Settings,
    system_prompt: str,
    user_prompt: str,
    *,
    max_retries: int = 3,
    base_delay: float = 15.0,
) -> str:
    """Call LLM with retry logic (same pattern as scan_paper/read_paper)."""
    client = create_client(settings)
    for attempt in range(max_retries + 1):
        try:
            return chat_completion(
                client=client,
                model=settings.llm_model,
                system_prompt=system_prompt,
                user_prompt=user_prompt,
                temperature=settings.llm_temperature,
            )
        except (RateLimitError, InternalServerError, APIConnectionError) as e:
            if attempt < max_retries:
                delay = base_delay * (2 ** attempt)
                logger.warning(
                    "Verifier LLM error, retrying in %ds (attempt %d/%d): %s",
                    delay, attempt + 1, max_retries, type(e).__name__,
                )
                time.sleep(delay)
            else:
                raise


def _truncate(text: str, max_chars: int) -> str:
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + "\n\n[... truncated ...]"


# ── Stage 1: Extract Points ──────────────────────────────────────────────


def extract_scientific_points(
    paper_context: str,
    settings: Settings,
    max_points: int = 5,
) -> list[ScientificPoint]:
    """Extract important scientific points from paper text.

    Makes 1 LLM call.
    """
    user_prompt = (
        f"## Paper Content\n\n{_truncate(paper_context, getattr(settings, 'verifier_max_context_chars', 20000))}\n\n"
        f"Extract up to {max_points} important scientific points."
    )
    response_text = _call_llm(settings, EXTRACT_POINTS_PROMPT, user_prompt)
    parsed = parse_llm_json(response_text, expected_root="points")

    points: list[ScientificPoint] = []
    if not isinstance(parsed, list):
        return points

    for i, p in enumerate(parsed):
        if not isinstance(p, dict):
            continue
        importance = max(1, min(5, int(p.get("importance", 3))))
        point_id = p.get("point_id", f"P{i+1}")
        points.append(ScientificPoint(
            point_id=point_id,
            point_type=p.get("point_type", "main_result"),
            original_text=p.get("original_text", ""),
            normalized_question=p.get("normalized_question", ""),
            importance=importance,
            reason_for_importance=p.get("reason_for_importance", ""),
        ))

    # Sort by importance descending
    points.sort(key=lambda x: x.importance, reverse=True)
    return points[:max_points]


# ── Stage 4: Build Logic Chain ───────────────────────────────────────────


def build_logic_chain(
    point: ScientificPoint,
    paper_context: str,
    settings: Settings,
    max_context_chars: int = 20000,
) -> LogicChain:
    """Build claim-evidence-reasoning chain for one point.

    Makes 1 LLM call.
    """
    user_prompt = (
        f"## Question\n\n{point.normalized_question}\n\n"
        f"## Original Point\n\n{point.original_text}\n\n"
        f"## Paper Content\n\n{_truncate(paper_context, max_context_chars)}\n\n"
        f"Reconstruct the claim-evidence-reasoning chain for this question."
    )
    response_text = _call_llm(settings, BUILD_LOGIC_CHAIN_PROMPT, user_prompt)
    parsed = parse_llm_json(response_text)
    if not parsed:
        return LogicChain(question=point.normalized_question, claim="")

    # Parse evidence items
    evidence: list[EvidenceItem] = []
    for e in parsed.get("evidence", []):
        if not isinstance(e, dict):
            continue
        evidence.append(EvidenceItem(
            source_type=e.get("source_type", "unknown"),
            source_id=e.get("source_id", ""),
            quote_or_summary=e.get("quote_or_summary", ""),
            relevance=e.get("relevance", ""),
            supports_claim=e.get("supports_claim", True),
            confidence=float(e.get("confidence", 0.5)),
        ))

    return LogicChain(
        question=parsed.get("question", point.normalized_question),
        claim=parsed.get("claim", ""),
        evidence=evidence,
        reasoning_chain=parsed.get("reasoning_chain", []),
        assumptions=parsed.get("assumptions", []),
        caveats=parsed.get("caveats", []),
        alternative_explanations=parsed.get("alternative_explanations", []),
        conclusion=parsed.get("conclusion", ""),
    )


# ── Stage 5: Critique Logic Chain ───────────────────────────────────────


def critique_logic_chain(
    point: ScientificPoint,
    chain: LogicChain,
    settings: Settings,
) -> LogicReviewResult:
    """Critique a logic chain.

    Makes 1 LLM call.
    """
    chain_json = json.dumps({
        "question": chain.question,
        "claim": chain.claim,
        "evidence": [{"source_type": e.source_type, "source_id": e.source_id,
                       "quote_or_summary": e.quote_or_summary, "relevance": e.relevance,
                       "supports_claim": e.supports_claim}
                      for e in chain.evidence],
        "reasoning_chain": chain.reasoning_chain,
        "assumptions": chain.assumptions,
        "caveats": chain.caveats,
        "conclusion": chain.conclusion,
    }, ensure_ascii=False, indent=2)

    user_prompt = (
        f"## Question\n\n{point.normalized_question}\n\n"
        f"## Logic Chain\n\n```json\n{chain_json}\n```"
    )
    response_text = _call_llm(settings, CRITIQUE_LOGIC_CHAIN_PROMPT, user_prompt)
    parsed = parse_llm_json(response_text)
    if not parsed:
        return LogicReviewResult()

    return LogicReviewResult(
        score=max(0, min(10, int(parsed.get("logic_score", 0)))),
        strengths=parsed.get("strengths", []),
        weaknesses=parsed.get("weaknesses", []),
        missing_evidence=parsed.get("missing_evidence", []),
        unsupported_claims=parsed.get("unsupported_claims", []),
        hidden_assumptions=parsed.get("hidden_assumptions", []),
        alternative_explanations=parsed.get("alternative_explanations", []),
    )


# ── Stage 6: Run Feynman Test ───────────────────────────────────────────


def run_feynman_test(
    point: ScientificPoint,
    chain: LogicChain,
    settings: Settings,
) -> FeynmanTestResult:
    """Run Feynman-style explanation test.

    Makes 1 LLM call.
    """
    chain_json = json.dumps({
        "question": chain.question,
        "claim": chain.claim,
        "evidence": [{"quote_or_summary": e.quote_or_summary} for e in chain.evidence[:3]],
        "reasoning_chain": chain.reasoning_chain,
        "conclusion": chain.conclusion,
    }, ensure_ascii=False, indent=2)

    user_prompt = (
        f"## Question\n\n{point.normalized_question}\n\n"
        f"## Logic Chain (summary)\n\n```json\n{chain_json}\n```"
    )
    response_text = _call_llm(settings, FEYNMAN_TEST_PROMPT, user_prompt)
    parsed = parse_llm_json(response_text)
    if not parsed:
        return FeynmanTestResult(simple_explanation="", first_principles_explanation="")

    return FeynmanTestResult(
        simple_explanation=parsed.get("simple_explanation", ""),
        first_principles_explanation=parsed.get("first_principles_explanation", ""),
        jargon_terms=parsed.get("jargon_terms", {}),
        transfer_test=parsed.get("transfer_test", ""),
    )


# ── Stage 7: Critique Feynman Test ──────────────────────────────────────


def critique_feynman_test(
    point: ScientificPoint,
    chain: LogicChain,
    feynman: FeynmanTestResult,
    settings: Settings,
) -> dict:
    """Critique the Feynman test.

    Makes 1 LLM call. Returns a dict with score, passed, critic_comments, etc.
    """
    feynman_json = json.dumps({
        "simple_explanation": feynman.simple_explanation,
        "first_principles_explanation": feynman.first_principles_explanation,
        "jargon_terms": feynman.jargon_terms,
        "transfer_test": feynman.transfer_test,
    }, ensure_ascii=False, indent=2)

    chain_summary = json.dumps({
        "question": chain.question,
        "claim": chain.claim,
        "conclusion": chain.conclusion,
    }, ensure_ascii=False)

    user_prompt = (
        f"## Question\n\n{point.normalized_question}\n\n"
        f"## Logic Chain\n\n```json\n{chain_summary}\n```\n\n"
        f"## Feynman Explanation\n\n```json\n{feynman_json}\n```"
    )
    response_text = _call_llm(settings, CRITIQUE_FEYNMAN_PROMPT, user_prompt)
    parsed = parse_llm_json(response_text)
    if not parsed:
        return {"feynman_score": 0, "passed": False, "critic_comments": ["Failed to critique"],
                "missing_mechanisms": [], "unsupported_additions": [],
                "transfer_test_quality": "poor"}
    return parsed


# ── Stage 8: Identify Gaps ──────────────────────────────────────────────


def identify_gaps(
    logic_review: LogicReviewResult,
    feynman_review: dict,
    point: ScientificPoint,
    settings: Settings,
) -> list[VerificationGap]:
    """Identify understanding gaps after logic and Feynman review.

    Makes 1 LLM call.
    """
    context = json.dumps({
        "question": point.normalized_question,
        "logic_score": logic_review.score,
        "logic_weaknesses": logic_review.weaknesses,
        "logic_missing_evidence": logic_review.missing_evidence,
        "feynman_score": feynman_review.get("feynman_score", 0),
        "feynman_comments": feynman_review.get("critic_comments", []),
        "missing_mechanisms": feynman_review.get("missing_mechanisms", []),
    }, ensure_ascii=False, indent=2)

    user_prompt = (
        f"## Review Summary\n\n```json\n{context}\n```"
    )
    response_text = _call_llm(settings, IDENTIFY_GAPS_PROMPT, user_prompt)
    parsed = parse_llm_json(response_text)
    if not parsed:
        return []

    gaps: list[VerificationGap] = []
    for g in parsed.get("gap_details", []):
        if not isinstance(g, dict):
            continue
        gaps.append(VerificationGap(
            gap_description=g.get("gap_description", ""),
            gap_type=g.get("gap_type", "evidence_missing"),
            severity=g.get("severity", "moderate"),
            point_id=point.point_id,
        ))

    return gaps


# ── Single-Point Verification ───────────────────────────────────────────


def verify_single_point(
    arxiv_id: str,
    point: ScientificPoint,
    paper_context: str,
    settings: Settings,
    max_iterations: int = 1,
    progress_callback: Callable[[str, str], None] | None = None,
) -> UnderstandingCertificate:
    """Verify understanding of a single scientific point.

    Pipeline per iteration: build_logic_chain → critique → feynman → critique_feynman → identify_gaps.
    Optional iteration: resolve_gaps → rebuild → re-critique (up to max_iterations).

    Makes 5 LLM calls per iteration.

    Args:
        arxiv_id: Paper identifier.
        point: The scientific point to verify.
        paper_context: Full paper text sections.
        settings: Application settings.
        max_iterations: Max gap-resolution iterations (default 1).
        progress_callback: Called with (stage_name, detail) after each stage.

    Returns:
        UnderstandingCertificate for this point.
    """
    max_context = getattr(settings, "verifier_max_context_chars", 20000)

    def _report(stage: str, detail: str = "") -> None:
        if progress_callback:
            progress_callback(stage, detail)

    try:
        # Stage 4: Build logic chain
        _report("logic_chain", point.point_id)
        chain = build_logic_chain(point, paper_context, settings, max_context)

        # Stage 5: Critique logic chain
        _report("logic_review", point.point_id)
        logic_review = critique_logic_chain(point, chain, settings)

        # Stage 6: Run Feynman test
        run_feynman = getattr(settings, "verifier_run_feynman", True)
        feynman_threshold = getattr(settings, "verifier_feynman_importance_threshold", 4)
        should_run_feynman = run_feynman and point.importance >= feynman_threshold

        if should_run_feynman:
            _report("feynman_test", point.point_id)
            feynman = run_feynman_test(point, chain, settings)

            _report("feynman_review", point.point_id)
            feynman_review = critique_feynman_test(point, chain, feynman, settings)
            feynman_score = max(0, min(10, int(feynman_review.get("feynman_score", 0))))
            feynman.critic_comments = feynman_review.get("critic_comments", [])
            feynman.score = feynman_score
        else:
            feynman = FeynmanTestResult(simple_explanation="", first_principles_explanation="")
            feynman_review = {"feynman_score": 0, "critic_comments": []}
            feynman_score = 0

        # Stage 8: Identify gaps
        _report("gaps", point.point_id)
        gaps = identify_gaps(logic_review, feynman_review, point, settings)

        # Stage 9: Iteration if needed
        for iteration in range(max_iterations):
            critical_gaps = [g for g in gaps if g.severity == "critical"]
            if not critical_gaps:
                break

            logger.info(
                "Point %s has %d critical gaps, iterating (round %d/%d)",
                point.point_id, len(critical_gaps), iteration + 1, max_iterations,
            )

            # Find context sections relevant to the gaps
            gap_descriptions = [g.gap_description for g in critical_gaps]
            additional_context = _find_relevant_sections(gap_descriptions, paper_context)

            if not additional_context:
                break

            # Rebuild with additional context
            _report("iterating", point.point_id)
            enriched_context = paper_context + "\n\n## Additional Context\n\n" + additional_context
            chain = build_logic_chain(point, enriched_context, settings, max_context)
            logic_review = critique_logic_chain(point, chain, settings)

            if should_run_feynman:
                feynman = run_feynman_test(point, chain, settings)
                feynman_review = critique_feynman_test(point, chain, feynman, settings)
                feynman_score = max(0, min(10, int(feynman_review.get("feynman_score", 0))))
                feynman.critic_comments = feynman_review.get("critic_comments", [])
                feynman.score = feynman_score

            gaps = identify_gaps(logic_review, feynman_review, point, settings)

        # Stage 10: Produce certificate
        _report("certificate", point.point_id)
        remaining_gaps = [g.gap_description for g in gaps if g.severity in ("critical", "moderate")]
        recommended = [g.gap_description for g in gaps if g.gap_type == "user_judgment_needed"]

        level = determine_understanding_level(
            logic_review.score, feynman_score, remaining_gaps,
        )
        overall_score = logic_review.score + feynman_score

        return UnderstandingCertificate(
            arxiv_id=arxiv_id,
            point=point,
            logic_chain=chain,
            logic_review=logic_review,
            feynman_test=feynman,
            understanding_level=level,
            overall_score=overall_score,
            remaining_gaps=remaining_gaps,
            recommended_followup=recommended,
            verified=True,
        )

    except Exception as e:
        logger.error("Verification failed for point %s: %s", point.point_id, e, exc_info=True)
        return UnderstandingCertificate(
            arxiv_id=arxiv_id,
            point=point,
            logic_chain=LogicChain(question=point.normalized_question, claim=""),
            logic_review=LogicReviewResult(),
            feynman_test=FeynmanTestResult(simple_explanation="", first_principles_explanation=""),
            understanding_level="not_understood",
            remaining_gaps=[f"Verification failed: {e}"],
            verified=False,
        )


def _find_relevant_sections(gap_descriptions: list[str], paper_context: str) -> str:
    """Find sections in paper_context relevant to the gap descriptions."""
    sections = paper_context.split("\n## ")
    relevant_parts: list[str] = []

    for section in sections:
        if not section.strip():
            continue
        for gap in gap_descriptions:
            # Simple keyword matching: check if gap keywords appear in section
            keywords = [w.lower() for w in gap.split() if len(w) > 4]
            section_lower = section.lower()
            matches = sum(1 for kw in keywords if kw in section_lower)
            if matches >= 2:
                relevant_parts.append("## " + section[:3000])
                break

    return "\n\n".join(relevant_parts[:5])


# ── Paper-Level Verification ────────────────────────────────────────────


def verify_paper_understanding(
    arxiv_id: str,
    title: str,
    paper_context: str,
    settings: Settings,
    max_points: int = 5,
    max_iterations: int = 1,
    progress_callback: Callable[[VerificationProgress], None] | None = None,
) -> list[UnderstandingCertificate]:
    """Verify understanding of all important points in a paper.

    Makes 1 LLM call for extraction + 5 calls per point per iteration.
    For 5 points with 1 iteration each: ~26 LLM calls total.

    Args:
        arxiv_id: Paper identifier.
        title: Paper title.
        paper_context: Full paper text sections.
        settings: Application settings.
        max_points: Max points to verify.
        max_iterations: Max gap-resolution iterations per point.
        progress_callback: Called with VerificationProgress after each stage.
    """
    max_points = min(max_points, getattr(settings, "verifier_max_points", 5))
    max_iterations = min(max_iterations, getattr(settings, "verifier_max_iterations", 1))

    progress = VerificationProgress(
        arxiv_id=arxiv_id,
        total_points=0,
        current_point_index=0,
        start_time=time.strftime("%H:%M:%S UTC"),
    )

    def _stage_report(stage: str, detail: str = "") -> None:
        progress.current_stage = stage
        progress.llm_calls_made += 1
        if progress_callback:
            progress_callback(progress)

    # Stage 1: Extract points
    _stage_report("extracting")
    points = extract_scientific_points(paper_context, settings, max_points)
    progress.total_points = len(points)

    if not points:
        logger.warning("No scientific points extracted for %s", arxiv_id)
        return []

    logger.info(
        "Extracted %d points for %s: %s",
        len(points), arxiv_id,
        ", ".join(f"{p.point_id}({p.point_type})" for p in points),
    )

    certificates: list[UnderstandingCertificate] = []

    for i, point in enumerate(points):
        progress.current_point_index = i + 1
        progress.current_point = point

        cert = verify_single_point(
            arxiv_id=arxiv_id,
            point=point,
            paper_context=paper_context,
            settings=settings,
            max_iterations=max_iterations,
            progress_callback=_stage_report,
        )
        certificates.append(cert)
        progress.completed_certificates.append(cert)

        if not cert.verified:
            progress.failed_points.append(point.point_id)

        logger.info(
            "Point %s verified: logic=%d feynman=%d level=%s",
            point.point_id, cert.logic_review.score, cert.feynman_test.score,
            cert.understanding_level,
        )

    progress.current_point = None
    progress.current_stage = "done"
    if progress_callback:
        progress_callback(progress)

    return certificates
