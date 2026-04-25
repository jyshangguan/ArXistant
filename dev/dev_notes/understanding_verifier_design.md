# ArXistant Understanding Verifier Design

## Purpose

This document describes the proposed **Understanding Verifier** for ArXistant. The verifier is a core component for using LLMs to read, understand, and critically evaluate scientific papers from arXiv, especially astronomy papers.

The verifier should not behave like a simple paper summarizer. Its purpose is to determine whether the system has **really understood** a scientific question, claim, result, method, or important point in a paper.

In this project, "understanding" is operationally defined as the ability to:

1. state the question or point precisely;
2. identify the relevant claims;
3. collect the necessary evidence from the paper and, when needed, related references;
4. reconstruct the reasoning chain from evidence to conclusion;
5. identify assumptions, caveats, and alternative explanations;
6. explain the mechanism in simple and technical language;
7. answer skeptical follow-up questions;
8. transfer the idea to a related case.

The verifier should become an important part of `/read`, and `/scan` should eventually take over the current roles of both the existing `/scan` and the existing lightweight `/read`.

---

## Current Project Context

The current ArXistant prototype has two related tools:

- `src/tools/scan_paper.py`
  - Uses arXiv metadata and abstract.
  - Produces a quick quality score, relevance links to the knowledge tree, and a reading recommendation.

- `src/tools/read_paper.py`
  - Fetches and parses arXiv HTML.
  - Selects abstract, introduction, conclusion/summary, results, and discussion-like sections.
  - Produces a concise executive reading note: background, key findings, evaluation, and knowledge-tree connections.

The proposed future behavior is:

- `/scan <arxiv_id>` should become a **rich but still quick paper triage command**.
  - It should combine the current `/scan` and current lightweight `/read` functions.
  - It should use metadata, abstract, and selected paper sections when available.
  - It should decide whether the paper deserves deep reading.
  - It should provide a compact summary, relevance score, novelty estimate, and reading recommendation.

- `/read <arxiv_id>` should become a **deep understanding command**.
  - It should use the Understanding Verifier extensively.
  - It should identify the main scientific results and other important points.
  - For each important point, it should verify whether the system has understood the point.
  - It should produce a structured reading report, not only a summary.

---

## High-Level Concept

The verifier has two complementary tasks.

### 1. Logic-loop verification

This checks whether the claim is supported by the paper.

It asks:

```text
Does the evidence in the paper support the conclusion?
```

The verifier reconstructs:

```text
Question / point
→ claim
→ evidence
→ method
→ assumptions
→ reasoning
→ conclusion
→ caveats
```

This is similar to how a referee checks a paper.

### 2. Understanding verification

This checks whether the LLM understands the scientific meaning behind the claim.

It asks:

```text
Can the system explain, defend, simplify, and transfer the idea without merely repeating the paper?
```

This includes a Feynman-style explanation test, jargon unpacking, first-principles mechanism explanation, adversarial questioning, and transfer to related cases.

Both parts are necessary.

A system may summarize a paper correctly but fail to explain the physical mechanism. Conversely, a system may know the background physics but fail to verify whether the specific paper actually proves the claim. The verifier should distinguish these cases.

---

## Core Principle

The verifier should not allow the LLM to declare that it understands a point unless it passes explicit tests.

The basic loop is:

```text
Read
→ extract important points
→ build claim-evidence-reasoning chains
→ critique the chains
→ run Feynman tests
→ identify gaps
→ retrieve more context
→ revise
→ score
→ output an understanding certificate
```

---

## Important Definitions

### Scientific point

A scientific point is any important statement worth understanding or verifying. It may be:

- a main result;
- a physical interpretation;
- a methodological claim;
- a sample-selection claim;
- a data-quality claim;
- a novelty claim;
- a caveat;
- a comparison with previous work;
- a prediction or implication.

Examples:

```text
The paper argues that Little Red Dots show Balmer absorption caused by dense gas.
```

```text
The authors claim that their JWST/NIRSpec spectrum reveals a broad-line AGN component.
```

```text
The paper claims that the measured host-galaxy morphology is robust against PSF mismatch.
```

### Question

A question is a normalized form of a point.

Example:

```text
Original point:
The spectrum shows Balmer absorption, indicating dense gas.

Normalized question:
Why does Balmer absorption imply unusual dense or optically thick gas, and does the paper provide enough evidence to support that interpretation?
```

### Understanding certificate

An understanding certificate is the final structured output for one question or point. It records:

- the question;
- the final answer;
- the evidence used;
- the logic chain;
- the assumptions;
- the caveats;
- the Feynman explanation;
- the transfer test;
- the score;
- remaining gaps.

---

## Proposed File Structure

Suggested new files:

```text
src/tools/understanding_verifier.py
src/tools/understanding_prompts.py
src/tools/understanding_types.py
src/tools/evidence_collector.py
src/tools/figure_reader.py              # optional later
src/tools/reference_reader.py           # optional later
```

Minimal first implementation can use only:

```text
src/tools/understanding_verifier.py
src/tools/understanding_prompts.py
src/tools/understanding_types.py
```

Later, evidence collection, figure reading, and reference reading can be separated into dedicated modules.

---

## Data Model

Add the following dataclasses in `src/tools/understanding_types.py` or integrate them into the existing `src/tools/types.py`.

```python
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
    """One piece of evidence used to support or challenge a scientific point."""

    source_type: str  # abstract, section, figure, table, equation, reference, user_note
    source_id: str = ""  # e.g. section title, figure number, reference key
    quote_or_summary: str = ""
    relevance: str = ""
    supports_claim: bool = True
    confidence: float = 0.5


@dataclass
class ScientificPoint:
    """An important point extracted from a paper."""

    point_id: str
    point_type: str  # result, method, interpretation, caveat, novelty, comparison, implication
    original_text: str
    normalized_question: str
    importance: int = 3  # 1-5
    reason_for_importance: str = ""


@dataclass
class LogicChain:
    """Claim-evidence-reasoning reconstruction for one point."""

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
    """Outputs of the Feynman-style understanding tests."""

    simple_explanation: str
    first_principles_explanation: str
    jargon_terms: dict[str, str] = field(default_factory=dict)
    transfer_test: str = ""
    critic_comments: list[str] = field(default_factory=list)
    score: int = 0  # 0-10


@dataclass
class LogicReviewResult:
    """Critique of whether the claim follows from the evidence."""

    score: int = 0  # 0-10
    strengths: list[str] = field(default_factory=list)
    weaknesses: list[str] = field(default_factory=list)
    missing_evidence: list[str] = field(default_factory=list)
    unsupported_claims: list[str] = field(default_factory=list)
    hidden_assumptions: list[str] = field(default_factory=list)
    alternative_explanations: list[str] = field(default_factory=list)


@dataclass
class UnderstandingCertificate:
    """Final verification report for one scientific point."""

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
```

---

## Understanding Levels

Use explicit understanding levels instead of a single confidence number.

### `not_understood`

The system cannot state the question, locate evidence, or explain the result.

### `partially_understood`

The system can summarize the point, but the logic chain or mechanism is incomplete.

### `argument_understood`

The system can reconstruct the paper's claim-evidence-reasoning chain, but the physical or methodological mechanism remains weak.

Example:

```text
The system knows that the authors claim X based on Figure 3, but cannot explain why Figure 3 physically implies X.
```

### `mechanism_understood`

The system can explain the underlying physical or methodological mechanism, but the critical evaluation of the specific paper remains incomplete.

Example:

```text
The system understands Balmer absorption physics, but has not checked whether the paper excludes stellar absorption or line-fitting artifacts.
```

### `critically_understood`

The system can:

- reconstruct the evidence;
- explain the mechanism;
- identify assumptions;
- consider alternatives;
- answer skeptical questions;
- transfer the idea to a related case.

This is the desired target for important scientific results.

---

## Scoring

Use two separate scores: `logic_score` and `feynman_score`.

### Logic-loop score: 0-10

```text
1. Question normalized clearly:       0-2
2. Claim identified:                  0-2
3. Evidence located and relevant:      0-2
4. Reasoning chain complete:           0-2
5. Assumptions/caveats considered:     0-2
```

### Feynman score: 0-10

```text
1. Simple explanation correct:         0-2
2. First-principles mechanism correct: 0-2
3. Key jargon unpacked:                0-2
4. Transfer test successful:           0-2
5. No unsupported hallucination:       0-2
```

### Overall status

```text
critically_understood:
    logic_score >= 8 and feynman_score >= 8

argument_understood:
    logic_score >= 8 and feynman_score < 8

mechanism_understood:
    logic_score < 8 and feynman_score >= 8

partially_understood:
    6 <= total_score < 16, or one important component is weak

not_understood:
    total_score < 6, or evidence cannot be located
```

The exact thresholds can be configured later.

---

## Pipeline Overview

The verifier should run the following stages.

```text
1. Extract important points
2. Normalize each point into a scientific question
3. Collect evidence from the paper
4. Build a claim-evidence-reasoning chain
5. Critique the logic chain
6. Run Feynman-style tests
7. Identify understanding gaps
8. Retrieve more context if available
9. Revise and re-score
10. Produce an understanding certificate
```

---

## Stage 1: Extract Important Points

Input:

- paper title;
- abstract;
- selected sections;
- figure captions if available;
- user knowledge tree;
- optional user-specified interests.

Output:

A list of `ScientificPoint` objects.

Prompt behavior:

- Extract the main scientific results first.
- Then extract important methodological, interpretive, or caveat points.
- Do not extract every sentence.
- Prefer 3-7 points for normal `/read`.
- For `/scan`, extract only 1-3 high-level points.

Example JSON output:

```json
{
  "points": [
    {
      "point_id": "P1",
      "point_type": "main_result",
      "original_text": "The authors report broad Balmer absorption in a population of Little Red Dots.",
      "normalized_question": "What evidence shows that the Little Red Dots have Balmer absorption, and how robust is that identification?",
      "importance": 5,
      "reason_for_importance": "This is the central observational result of the paper."
    },
    {
      "point_id": "P2",
      "point_type": "interpretation",
      "original_text": "The authors interpret the absorption as evidence for dense gas near the AGN.",
      "normalized_question": "Why would Balmer absorption imply dense or optically thick gas, and does the paper support this interpretation?",
      "importance": 5,
      "reason_for_importance": "This connects the observation to the physical interpretation."
    }
  ]
}
```

---

## Stage 2: Normalize the Question

For each point, rewrite it as a precise scientific question.

The normalized question should include:

- the phenomenon;
- the claimed interpretation;
- the required evidence;
- the expected answer type.

Bad:

```text
What about Balmer absorption?
```

Good:

```text
What observational evidence supports the identification of Balmer absorption in this spectrum, and what checks are needed to distinguish intrinsic gas absorption from stellar absorption, line blending, or continuum-fitting artifacts?
```

---

## Stage 3: Evidence Collection

Evidence should be collected from:

1. abstract;
2. introduction;
3. data/sample section;
4. method section;
5. results section;
6. discussion/conclusion;
7. figure captions;
8. tables;
9. equations;
10. related references, when available.

For the first implementation, use only available arXiv HTML text and figure captions. Later implementations can add visual figure analysis, PDF parsing, and reference retrieval.

Each evidence item should record:

```text
source_type
source_id
quote_or_summary
relevance
supports_claim
confidence
```

Important: evidence should not be only copied text. The verifier should also explain why the evidence matters.

---

## Stage 4: Build the Logic Chain

For each point, build a `LogicChain`.

The output should contain:

```text
Question
Claim
Evidence
Reasoning chain
Assumptions
Caveats
Alternative explanations
Conclusion
```

Example:

```text
Question:
Why does Balmer absorption imply dense or optically thick gas?

Claim:
The absorption indicates a gas component with a significant hydrogen n=2 population, likely dense and/or optically thick.

Evidence:
- Multiple Balmer absorption troughs are reported.
- The troughs are aligned in velocity.
- The authors model the absorption multiplicatively on top of continuum and emission components.

Reasoning:
1. Balmer absorption requires hydrogen atoms in the n=2 level.
2. In ordinary neutral gas, most hydrogen is in n=1.
3. A significant n=2 population requires special excitation or radiative-transfer conditions.
4. Dense or optically thick gas can maintain such a population.
5. Therefore, if the absorption is intrinsic, it constrains dense/optically thick gas near the source.

Assumptions:
- The absorption is not dominated by stellar Balmer absorption.
- The continuum and broad emission-line model is reliable.
- The line identification is correct.

Caveats:
- Line blending and low spectral resolution can mimic broad absorption.
- Stellar continuum subtraction may affect the result.
- The absorption model may be degenerate with emission-line decomposition.
```

---

## Stage 5: Critique the Logic Chain

Use a critic prompt to evaluate whether the logic chain is sufficient.

The critic should look for:

- missing evidence;
- unsupported claims;
- hidden assumptions;
- alternative explanations;
- overgeneralization;
- observation-interpretation confusion;
- method blindness;
- figure blindness;
- literature isolation.

The critic should output JSON:

```json
{
  "logic_score": 8,
  "strengths": [
    "The evidence identifies multiple Balmer lines, not only one feature."
  ],
  "weaknesses": [
    "The current evidence does not fully show how stellar absorption was excluded."
  ],
  "missing_evidence": [
    "Need the section describing continuum and stellar-population subtraction."
  ],
  "unsupported_claims": [],
  "hidden_assumptions": [
    "The absorption troughs are assumed to be intrinsic to the source."
  ],
  "alternative_explanations": [
    "stellar Balmer absorption", "line blending", "sky residual", "template mismatch"
  ]
}
```

---

## Stage 6: Feynman Test

The Feynman test is required for important points. It checks whether the system can explain the idea from first principles rather than merely repeating the paper.

The Feynman test should contain four subtests.

### 6.1 Simple explanation

Prompt:

```text
Explain the answer to the question in simple language for a first-year astronomy graduate student. Do not quote the paper. Do not use unexplained jargon. Keep the explanation scientifically correct.
```

### 6.2 First-principles mechanism

Prompt:

```text
Explain the physical or methodological mechanism from first principles. Start from the most basic relevant concepts. Show the causal or logical chain from observation to conclusion.
```

Example structure:

```text
Observed feature
→ measurement
→ physical transition or diagnostic
→ required conditions
→ interpretation
→ limitations
```

### 6.3 Jargon unpacking

Prompt:

```text
List the key technical terms in your explanation. For each term, define it in one sentence and explain why it matters here.
```

### 6.4 Transfer test

Prompt:

```text
Apply the same idea to a new but related case. Suppose we observe another object with similar features. What should we check to decide whether the same conclusion applies?
```

The transfer test is important because it checks whether the system has generalized the idea.

---

## Stage 7: Critique the Feynman Test

The Feynman explanation should be graded by a critic prompt.

The critic should detect:

1. paper parroting;
2. vague explanation;
3. missing mechanism;
4. false simplicity;
5. unsupported additions;
6. incorrect physics;
7. confusion between observation and interpretation;
8. failure to define key terms;
9. failure to transfer the idea;
10. overconfidence.

Output JSON:

```json
{
  "feynman_score": 8,
  "passed": true,
  "critic_comments": [
    "The explanation correctly connects Balmer absorption to the n=2 level population.",
    "The answer should more explicitly mention that dense gas is not the only possible route; radiative transfer and Ly-alpha trapping may also matter."
  ],
  "missing_mechanisms": [],
  "unsupported_additions": [],
  "transfer_test_quality": "good"
}
```

---

## Stage 8: Gap Identification

After logic review and Feynman review, identify remaining gaps.

A gap is something that prevents `critically_understood` status.

Common gaps:

```text
Need exact figure/table evidence.
Need method details.
Need sample-selection information.
Need uncertainty or error propagation.
Need alternative explanation check.
Need physical background reference.
Need prior literature comparison.
Need visual inspection of a figure.
Need user expertise or domain-specific judgment.
```

Example:

```json
{
  "remaining_gaps": [
    "The paper's treatment of stellar Balmer absorption has not been checked.",
    "The relevant figure caption was read, but the figure image itself has not yet been analyzed.",
    "The cited reference for Balmer absorption physics has not been retrieved."
  ],
  "recommended_followup": [
    "Read the spectral decomposition method section.",
    "Analyze the figure showing the Balmer-line fits.",
    "Retrieve and summarize the cited Balmer absorption reference."
  ]
}
```

---

## Stage 9: Iteration

The verifier should support multiple iterations.

Pseudo-code:

```python
def verify_understanding(
    arxiv_id: str,
    question: str,
    paper_context: str,
    figures: list | None = None,
    references: list | None = None,
    max_iterations: int = 2,
) -> UnderstandingCertificate:
    point = normalize_question(question, paper_context)

    evidence = collect_evidence(point, paper_context, figures=figures, references=references)
    logic_chain = build_logic_chain(point, evidence)
    logic_review = critique_logic_chain(point, logic_chain)
    feynman_test = run_feynman_test(point, logic_chain)
    feynman_review = critique_feynman_test(point, feynman_test, logic_chain)

    gaps = identify_gaps(logic_review, feynman_review)

    iteration = 0
    while gaps and iteration < max_iterations:
        extra_context = retrieve_for_gaps(gaps, paper_context, figures, references)
        evidence = update_evidence(evidence, extra_context)
        logic_chain = build_logic_chain(point, evidence)
        logic_review = critique_logic_chain(point, logic_chain)
        feynman_test = run_feynman_test(point, logic_chain)
        feynman_review = critique_feynman_test(point, feynman_test, logic_chain)
        gaps = identify_gaps(logic_review, feynman_review)
        iteration += 1

    return build_certificate(
        arxiv_id=arxiv_id,
        point=point,
        logic_chain=logic_chain,
        logic_review=logic_review,
        feynman_test=feynman_test,
        feynman_review=feynman_review,
        gaps=gaps,
    )
```

For the first implementation, `retrieve_for_gaps` can simply search within already parsed paper sections. Later it can retrieve cited references, figures, PDF pages, or external literature.

---

## LLM Prompt Design

Put prompts in `src/tools/understanding_prompts.py`.

Recommended prompt constants:

```python
EXTRACT_POINTS_PROMPT
BUILD_LOGIC_CHAIN_PROMPT
CRITIQUE_LOGIC_CHAIN_PROMPT
FEYNMAN_TEST_PROMPT
CRITIQUE_FEYNMAN_PROMPT
IDENTIFY_GAPS_PROMPT
SYNTHESIZE_CERTIFICATE_PROMPT
```

All prompts should require JSON output. The parser should be robust to fenced code blocks and invalid LaTeX escapes, following the style already used in `scan_paper.py` and `read_paper.py`.

---

## JSON Output Schemas

### Extract points schema

```json
{
  "points": [
    {
      "point_id": "P1",
      "point_type": "main_result",
      "original_text": "...",
      "normalized_question": "...",
      "importance": 5,
      "reason_for_importance": "..."
    }
  ]
}
```

### Logic chain schema

```json
{
  "question": "...",
  "claim": "...",
  "evidence": [
    {
      "source_type": "section",
      "source_id": "Results",
      "quote_or_summary": "...",
      "relevance": "...",
      "supports_claim": true,
      "confidence": 0.8
    }
  ],
  "reasoning_chain": ["...", "..."],
  "assumptions": ["..."],
  "caveats": ["..."],
  "alternative_explanations": ["..."],
  "conclusion": "..."
}
```

### Logic critique schema

```json
{
  "logic_score": 8,
  "strengths": ["..."],
  "weaknesses": ["..."],
  "missing_evidence": ["..."],
  "unsupported_claims": ["..."],
  "hidden_assumptions": ["..."],
  "alternative_explanations": ["..."]
}
```

### Feynman test schema

```json
{
  "simple_explanation": "...",
  "first_principles_explanation": "...",
  "jargon_terms": {
    "term": "definition and relevance"
  },
  "transfer_test": "..."
}
```

### Feynman critique schema

```json
{
  "feynman_score": 8,
  "passed": true,
  "critic_comments": ["..."],
  "missing_mechanisms": ["..."],
  "unsupported_additions": ["..."],
  "transfer_test_quality": "good"
}
```

### Certificate schema

```json
{
  "understanding_level": "critically_understood",
  "overall_score": 17,
  "verified": true,
  "remaining_gaps": ["..."],
  "recommended_followup": ["..."]
}
```

---

## Integration with `/read`

The future `/read <arxiv_id>` command should do the following:

```text
1. Fetch and parse the arXiv HTML paper.
2. Select useful sections, but avoid losing methods/results details.
3. Extract 3-7 important scientific points.
4. For each high-importance point, run the Understanding Verifier.
5. Produce a deep reading report.
6. Store the reading report and certificates in SQLite.
7. Display a concise Feishu card with expandable details or links to a Markdown report.
```

Recommended `/read` output sections:

```markdown
# Deep Reading Report

## Paper
Title, authors, arXiv ID, categories

## One-paragraph overview

## Main scientific points

## Verified understanding certificates
For each point:
- question
- answer
- logic score
- Feynman score
- understanding level
- key evidence
- caveats
- remaining gaps

## Most important figures/tables

## Referee-style critique

## Relevance to my knowledge tree

## Follow-up ideas
```

The `/read` command should distinguish between:

```text
The paper claims X.
The verifier considers X well supported.
The verifier considers X plausible but not fully demonstrated.
The verifier cannot verify X from the available text.
```

This distinction is crucial.

---

## Integration with `/scan`

The future `/scan <arxiv_id>` should take over the current functions of both `/scan` and the current lightweight `/read`.

It should remain fast and concise, but should use more than the abstract when arXiv HTML is available.

Recommended behavior:

```text
1. Fetch arXiv metadata and abstract.
2. Try to fetch arXiv HTML.
3. If HTML is available, select abstract, introduction, conclusion, and key result sections.
4. Produce:
   - quality score;
   - relevance to knowledge tree;
   - short background;
   - 1-3 key findings;
   - novelty estimate;
   - reliability warning if obvious;
   - recommendation: ignore / skim / read deeply.
5. Do not run the full Understanding Verifier by default.
6. Optionally run a mini-verifier on only the single most important point when the paper is very relevant.
```

Suggested scan recommendation levels:

```text
ignore
skim
read
deep_read
```

Mapping:

```text
ignore: low relevance or routine work
skim: maybe useful background
read: relevant and scientifically interesting
deep_read: important paper; run `/read` with full verifier
```

---

## Database Storage

The current database has a `reading_notes` table. The verifier will likely need new storage.

Minimal option:

- Store certificates as JSON in `reading_notes.raw_notes`.

Better option:

Add a new table:

```sql
CREATE TABLE IF NOT EXISTS understanding_certificates (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    arxiv_id TEXT NOT NULL,
    point_id TEXT NOT NULL,
    point_type TEXT NOT NULL DEFAULT '',
    question TEXT NOT NULL DEFAULT '',
    claim TEXT NOT NULL DEFAULT '',
    logic_score INTEGER NOT NULL DEFAULT 0,
    feynman_score INTEGER NOT NULL DEFAULT 0,
    overall_score INTEGER NOT NULL DEFAULT 0,
    understanding_level TEXT NOT NULL DEFAULT '',
    verified INTEGER NOT NULL DEFAULT 0,
    certificate_json TEXT NOT NULL DEFAULT '',
    full_text_hash TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL DEFAULT (datetime('now')),
    updated_at TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE(arxiv_id, point_id, full_text_hash)
);

CREATE INDEX IF NOT EXISTS idx_understanding_certificates_arxiv
ON understanding_certificates(arxiv_id);
```

Also consider updating `SCHEMA_VERSION` and migrations in `src/storage.py`.

---

## Configuration

Add settings to `config/settings.yaml` and the `Settings` dataclass if useful.

Example:

```yaml
understanding_verifier:
  enabled: true
  max_points_per_read: 5
  max_iterations: 2
  run_feynman_test: true
  require_feynman_for_importance: 4
  logic_pass_threshold: 8
  feynman_pass_threshold: 8
  max_context_chars_per_point: 20000
  store_certificates: true
```

For the MVP, it is acceptable to hard-code reasonable defaults and add config later.

---

## Minimal MVP Implementation Plan

### MVP goal

Implement a verifier that can run on selected text from arXiv HTML and produce understanding certificates for 1-3 important points.

### Step 1: Add types

Create `src/tools/understanding_types.py` with the dataclasses above.

### Step 2: Add prompts

Create `src/tools/understanding_prompts.py` with JSON-output prompts.

### Step 3: Add robust JSON parsing helper

Reuse or factor out the JSON parsing logic currently duplicated in `scan_paper.py` and `read_paper.py`.

Possible file:

```text
src/tools/json_utils.py
```

### Step 4: Implement point extraction

Function:

```python
def extract_scientific_points(paper_context: str, settings: Settings, max_points: int = 5) -> list[ScientificPoint]:
    ...
```

### Step 5: Implement single-point verification

Function:

```python
def verify_point(
    arxiv_id: str,
    point: ScientificPoint,
    paper_context: str,
    settings: Settings,
    max_iterations: int = 1,
) -> UnderstandingCertificate:
    ...
```

### Step 6: Implement paper-level verification

Function:

```python
def verify_paper_understanding(
    arxiv_id: str,
    title: str,
    paper_context: str,
    settings: Settings,
    max_points: int = 5,
) -> list[UnderstandingCertificate]:
    ...
```

### Step 7: Integrate with `read_paper`

For initial integration:

- Keep the existing `ReadingNote` output.
- Add optional verifier execution after the current executive summary.
- Store or return certificates separately.
- Do not break existing tests.

### Step 8: Add tests

Add tests:

```text
tests/test_understanding_verifier.py
```

Test cases should mock LLM responses and verify:

- JSON parsing;
- point extraction;
- score calculation;
- understanding level assignment;
- certificate construction;
- graceful behavior when LLM output is malformed.

---

## Suggested Function Interfaces

```python
def determine_understanding_level(logic_score: int, feynman_score: int, gaps: list[str]) -> str:
    """Return understanding level from scores and gaps."""
    if logic_score >= 8 and feynman_score >= 8 and not _has_critical_gap(gaps):
        return "critically_understood"
    if logic_score >= 8 and feynman_score < 8:
        return "argument_understood"
    if logic_score < 8 and feynman_score >= 8:
        return "mechanism_understood"
    if logic_score + feynman_score >= 6:
        return "partially_understood"
    return "not_understood"
```

```python
def _has_critical_gap(gaps: list[str]) -> bool:
    critical_keywords = [
        "no evidence",
        "cannot locate",
        "missing main figure",
        "unsupported",
        "contradiction",
    ]
    text = " ".join(gaps).lower()
    return any(k in text for k in critical_keywords)
```

---

## Prompt: Extract Important Points

```python
EXTRACT_POINTS_PROMPT = """
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
```

---

## Prompt: Build Logic Chain

```python
BUILD_LOGIC_CHAIN_PROMPT = """
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
```

---

## Prompt: Critique Logic Chain

```python
CRITIQUE_LOGIC_CHAIN_PROMPT = """
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
```

---

## Prompt: Feynman Test

```python
FEYNMAN_TEST_PROMPT = """
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
```

---

## Prompt: Critique Feynman Test

```python
CRITIQUE_FEYNMAN_PROMPT = """
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
```

---

## Astronomy-Specific Checks

The verifier should eventually include astronomy-specific critical checks.

For observational astronomy papers:

```text
- Is the sample selection clearly defined?
- Are selection effects considered?
- Are completeness limits discussed?
- Are redshift uncertainties important?
- Are flux calibration and telluric correction relevant?
- Are PSF, beam, or aperture effects important?
- Are spatial-resolution limits or beam-smearing effects important?
- Are line identifications robust?
- Are emission-line blends handled correctly?
- Are stellar-continuum and AGN-continuum decompositions reliable?
- Are uncertainties propagated?
- Are upper limits treated correctly?
- Are non-detections interpreted carefully?
```

For AGN/galaxy papers:

```text
- Is AGN contamination separated from host-galaxy light?
- Are broad and narrow emission-line components separated?
- Are outflows distinguished from rotation or turbulence?
- Are SED-fitting assumptions stated?
- Are dust law, IMF, metallicity, and SFH assumptions relevant?
- Are virial black-hole mass assumptions relevant?
- Are orientation effects considered?
```

For high-redshift/JWST papers:

```text
- Is the redshift spectroscopic or photometric?
- Are line identifications unique?
- Are lensing magnification or selection effects relevant?
- Are stellar population and AGN interpretations degenerate?
- Are nebular continuum and dust attenuation considered?
```

For interferometry or high-resolution imaging:

```text
- Is the source resolved?
- Is the PSF or beam model reliable?
- Are uv-coverage limitations important?
- Are calibration systematics discussed?
- Are model degeneracies explored?
```

These can be added as optional domain checklists later.

---

## Example User-Facing Output for `/read`

```markdown
## Understanding Verifier Summary

The verifier checked 4 important scientific points.

| Point | Type | Logic | Feynman | Level | Status |
|---|---:|---:|---:|---|---|
| Evidence for Balmer absorption | main result | 8/10 | 8/10 | critically understood | verified |
| Dense-gas interpretation | interpretation | 7/10 | 9/10 | mechanism understood | needs paper evidence |
| Comparison with low-z analogs | comparison | 6/10 | 7/10 | partially understood | needs references |
| Implication for LRD nature | implication | 6/10 | 6/10 | partially understood | speculative |

### Most robust result
The presence of multiple velocity-consistent Balmer absorption features appears to be the most directly supported result.

### Most important caveat
The dense-gas interpretation is physically plausible, but its strength depends on how well the paper excludes stellar absorption, continuum-subtraction artifacts, and line blending.

### Recommended follow-up
Read the spectral decomposition section and the cited reference on Balmer absorption physics before treating the dense-gas interpretation as fully established.
```

---

## Testing Strategy

Use mocked LLM responses. Do not call real APIs in unit tests.

Test files:

```text
tests/test_understanding_verifier.py
tests/test_understanding_types.py
tests/test_understanding_prompts.py
```

Tests should cover:

1. parsing valid JSON;
2. parsing JSON inside code fences;
3. handling malformed JSON gracefully;
4. converting parsed JSON to dataclasses;
5. determining understanding level from scores;
6. detecting critical gaps;
7. verifying that `/read` can call verifier without breaking existing output;
8. verifying that certificates can be serialized to JSON.

Example test:

```python
def test_determine_understanding_level_critically_understood():
    level = determine_understanding_level(
        logic_score=8,
        feynman_score=9,
        gaps=[],
    )
    assert level == "critically_understood"
```

---

## Development Priorities

### Priority 1: MVP verifier

- Works on text only.
- Extracts 1-5 important points.
- Builds logic chain.
- Runs Feynman test.
- Scores understanding.
- Produces certificates.

### Priority 2: `/read` integration

- `/read` uses verifier for main scientific points.
- Keeps concise output card.
- Saves detailed report to Markdown or SQLite.

### Priority 3: richer `/scan`

- `/scan` combines current scan and lightweight read.
- Uses abstract plus selected sections.
- Provides triage recommendation.

### Priority 4: figures

- Parse figure captions.
- Associate figures with points.
- Later add visual figure analysis.

### Priority 5: references

- Identify key cited references.
- Retrieve and summarize background references.
- Use references to close mechanism gaps.

---

## Important Implementation Notes

1. Keep the verifier modular.
   - It should be callable from `/read`, reports, or future scheduled workflows.

2. Keep outputs structured.
   - Use JSON internally and Markdown for user-facing reports.

3. Do not over-trust the LLM.
   - The critic prompts are not optional decoration; they are part of the verifier.

4. Separate paper evidence from background knowledge.
   - A claim may be physically plausible but not demonstrated by the paper.

5. Preserve uncertainty.
   - The verifier should say when something is not fully verified.

6. Make partial completion useful.
   - Even if the verifier cannot reach `critically_understood`, it should return gaps and recommended follow-up.

7. Avoid excessive cost in `/scan`.
   - Full verification belongs in `/read`.

8. Make the system astronomy-aware but not hard-coded to one subfield.
   - Use domain checklists as guidance, not rigid rules.

---

## Summary

The Understanding Verifier is the component that turns ArXistant from a summarization bot into a scientific reading assistant.

Its central task is to verify whether an LLM has genuinely understood a scientific point by checking both:

```text
1. whether the paper's evidence supports the conclusion;
2. whether the system can explain, defend, simplify, and transfer the idea.
```

The verifier should output explicit understanding certificates, including logic scores, Feynman scores, evidence, assumptions, caveats, and remaining gaps.

The future role of commands should be:

```text
/scan  → rich triage: current scan + current lightweight read
/read  → deep scientific understanding using the verifier
```

The most important design principle is:

```text
Do not let the LLM claim understanding merely because it produced a fluent summary.
Require evidence, reasoning, critique, Feynman explanation, and transfer.
```
