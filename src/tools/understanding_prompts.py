"""LLM prompts for the Understanding Verifier."""

EXTRACT_POINTS_PROMPT = """\
You are an expert astronomy paper reader. Your task is to extract the most important scientific points from a paper.

A scientific point may be a main result, method, interpretation, caveat, novelty claim, comparison with previous work, or implication.

Do not extract every detail. Focus on the points that deserve verification.

Return ONLY JSON:

{
  "points": [
    {
      "point_id": "P1",
      "point_type": "main_result | method | interpretation | caveat | novelty | comparison | implication",
      "original_text": "short statement of the point",
      "normalized_question": "the point rewritten as a precise scientific question",
      "importance": 1,
      "reason_for_importance": "why this point matters"
    }
  ]
}

Rules:
- Extract 3-7 points unless the paper is very simple.
- Always include the main scientific result if identifiable.
- Prefer questions that can be checked using evidence.
- Do not invent claims not present in the provided text.
"""

BUILD_LOGIC_CHAIN_PROMPT = """\
You are verifying whether a scientific point in an astronomy paper is understood.

Given a normalized question and paper context, reconstruct the claim-evidence-reasoning chain.

Return ONLY JSON:

{
  "question": "...",
  "claim": "...",
  "evidence": [
    {
      "source_type": "abstract | section | figure | table | equation | reference | unknown",
      "source_id": "section title, figure number, table number, or other locator",
      "quote_or_summary": "short quote or faithful summary",
      "relevance": "why this evidence matters",
      "supports_claim": true,
      "confidence": 0.0
    }
  ],
  "reasoning_chain": ["step 1", "step 2", "step 3"],
  "assumptions": ["..."],
  "caveats": ["..."],
  "alternative_explanations": ["..."],
  "conclusion": "..."
}

Rules:
- Distinguish what is observed from what is inferred.
- Do not claim that the paper proves something unless the evidence supports it.
- Include missing or weak evidence as caveats.
- Prefer specific evidence locations over general statements.
"""

CRITIQUE_LOGIC_CHAIN_PROMPT = """\
You are a skeptical referee evaluating a claim-evidence-reasoning chain.

Your job is to decide whether the evidence supports the conclusion.

Look for:
- missing evidence;
- unsupported claims;
- hidden assumptions;
- alternative explanations;
- overgeneralization;
- observation-interpretation confusion;
- method blindness;
- figure blindness;
- literature isolation.

Return ONLY JSON:

{
  "logic_score": 0,
  "strengths": ["..."],
  "weaknesses": ["..."],
  "missing_evidence": ["..."],
  "unsupported_claims": ["..."],
  "hidden_assumptions": ["..."],
  "alternative_explanations": ["..."]
}

Scoring:
- 0-2: not supported or incoherent
- 3-5: partially supported but major gaps
- 6-7: mostly supported but important caveats remain
- 8-9: well supported with minor caveats
- 10: exceptionally clear and robust
"""

FEYNMAN_TEST_PROMPT = """\
You are testing whether a scientific point is genuinely understood, not merely summarized.

Using the provided logic chain, perform a Feynman-style explanation test.

Return ONLY JSON:

{
  "simple_explanation": "Explain the point for a first-year astronomy graduate student without unexplained jargon.",
  "first_principles_explanation": "Explain the physical or methodological mechanism from basic principles to the conclusion.",
  "jargon_terms": {
    "term": "definition and why it matters here"
  },
  "transfer_test": "Apply the idea to a new but related case and explain what should be checked."
}

Rules:
- Do not quote the paper.
- Do not merely repeat the claim.
- Do not introduce unsupported details.
- Preserve scientific accuracy.
- If the mechanism is not clear from the context, say so explicitly.
"""

CRITIQUE_FEYNMAN_PROMPT = """\
You are grading a Feynman-style explanation of a scientific point.

Check whether the explanation demonstrates real understanding.

Look for:
- paper parroting;
- vague explanation;
- missing mechanism;
- false simplicity;
- unsupported additions;
- incorrect physics;
- confusion between observation and interpretation;
- failure to define key terms;
- failure to transfer the idea;
- overconfidence.

Return ONLY JSON:

{
  "feynman_score": 0,
  "passed": false,
  "critic_comments": ["..."],
  "missing_mechanisms": ["..."],
  "unsupported_additions": ["..."],
  "transfer_test_quality": "poor | partial | good | excellent"
}

Scoring:
- 0-2: does not demonstrate understanding
- 3-5: partial explanation with major gaps
- 6-7: mostly correct but incomplete
- 8-9: strong understanding
- 10: excellent, accurate, transferable understanding
"""

IDENTIFY_GAPS_PROMPT = """\
You are identifying remaining gaps after a logic review and Feynman review of a scientific point.

Given the logic review findings and Feynman review findings, identify what prevents full understanding.

Return ONLY JSON:

{
  "remaining_gaps": ["description of each gap"],
  "recommended_followup": ["suggested action for each gap"],
  "gap_details": [
    {
      "gap_description": "...",
      "gap_type": "evidence_missing | method_unclear | user_judgment_needed | reference_needed | figure_analysis_needed",
      "severity": "critical | moderate | minor"
    }
  ]
}

Common gap types:
- evidence_missing: the paper does not provide sufficient evidence
- method_unclear: methodology details are insufficient
- user_judgment_needed: domain expertise or user judgment is required
- reference_needed: a cited reference should be consulted
- figure_analysis_needed: a figure or table needs visual inspection

Only list gaps that are real barriers to understanding, not minor stylistic issues.
"""

RESOLVE_GAPS_WITH_CONTEXT_PROMPT = """\
You are resolving understanding gaps identified by a critic.

Given:
- The original question
- The existing logic chain
- The identified gaps
- Additional context from the user or from re-examining the paper

Revise the logic chain to address the gaps. Be explicit about what was resolved and what remains uncertain.

Return ONLY JSON:

{
  "revised_logic_chain": {
    "question": "...",
    "claim": "...",
    "evidence": [...],
    "reasoning_chain": [...],
    "assumptions": [...],
    "caveats": [...],
    "alternative_explanations": [...],
    "conclusion": "..."
  },
  "resolved_gaps": ["descriptions of what was resolved"],
  "remaining_gaps": ["descriptions of what could not be resolved"]
}
"""
