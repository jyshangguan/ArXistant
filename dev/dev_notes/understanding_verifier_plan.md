# Understanding Verifier — Implementation Plan

## Context

ArXistant currently summarizes papers via `/scan` (relevance + quality score) and `/read` (executive summary). This plan upgrades it into a scientific reading assistant that verifies genuine understanding of each paper's key claims.

Design specification: `dev/dev_notes/understanding_verifier_design.md` (1605 lines).

## Architecture

```
/read arxiv_id
  │
  ├─ [existing] scan → read (executive summary) → card
  │
  └─ [new] start_verification() (background asyncio task)
         │
         ├─ extract_scientific_points() → 1 LLM call
         │     → [Feishu] Verification Plan Card
         │
         └─ For each point (up to 5):
               ├─ build_logic_chain()     → 1 LLM call
               ├─ critique_logic_chain()   → 1 LLM call
               ├─ run_feynman_test()       → 1 LLM call
               ├─ critique_feynman_test()  → 1 LLM call
               ├─ identify_gaps()          → 1 LLM call
               │     → [Feishu] Progress Card + Question Card (if needed)
               └─ produce certificate → store in DB
                     → [Feishu] Final Result Card
```

Cost: ~26-36 LLM calls per paper. Caching by `full_text_hash` avoids re-verification.

## Phases

### Phase 1: Foundation (types, prompts, json_utils)

**New files:**
- `src/tools/understanding_types.py`
- `src/tools/understanding_prompts.py`
- `src/tools/json_utils.py`

**Modify:** `src/tools/scan_paper.py`, `src/tools/read_paper.py` (use shared json_utils)

### Phase 2: Core Verifier Pipeline

**New file:** `src/tools/understanding_verifier.py`

Functions:
- `extract_scientific_points(paper_context, settings, max_points) -> list[ScientificPoint]`
- `build_logic_chain(point, paper_context, settings) -> LogicChain`
- `critique_logic_chain(point, chain, settings) -> LogicReviewResult`
- `run_feynman_test(point, chain, settings) -> FeynmanTestResult`
- `critique_feynman_test(point, chain, feynman, settings) -> dict`
- `identify_gaps(logic_review, feynman_review, point, settings) -> list[VerificationGap]`
- `determine_understanding_level(logic_score, feynman_score, gaps) -> UnderstandingLevel`
- `verify_single_point(arxiv_id, point, paper_context, settings, ...) -> UnderstandingCertificate`
- `verify_paper_understanding(arxiv_id, title, paper_context, settings, ...) -> list[UnderstandingCertificate]`

### Phase 3: Database

- Schema v5 migration: `understanding_certificates` table
- CRUD: `upsert_understanding_certificate()`, `get_certificates_for_paper()`, `has_certificates_for_paper()`

### Phase 4: Configuration

- Settings: `verifier_enabled`, `verifier_max_points`, `verifier_max_iterations`, etc.
- `config/settings.yaml`: `verifier:` section

### Phase 5: Feishu Cards

- `build_verification_plan_card()`, `build_verification_progress_card()`, `build_verification_result_card()`, `build_verifier_question_card()`, `build_verification_summary_inline()`

### Phase 6: Async Runner

- `src/bot/verifier_runner.py`: `start_verification()`, `ask_user_question()`, `resolve_user_response()`

### Phase 7: /read Integration

- After executive summary, start background verification task

### Phase 8: User Interaction Callbacks

- Card callbacks: `verifier_answer`, `verifier_skip_point`, `verifier_abort`
- Text reply interception for user questions

### Phase 9: Tests

- `test_understanding_types.py`, `test_understanding_verifier.py`, `test_json_utils.py`

## File Summary

| Action | File | Phase |
|--------|------|-------|
| CREATE | `src/tools/understanding_types.py` | 1 |
| CREATE | `src/tools/understanding_prompts.py` | 1 |
| CREATE | `src/tools/json_utils.py` | 1 |
| MODIFY | `src/tools/scan_paper.py` | 1 |
| MODIFY | `src/tools/read_paper.py` | 1 |
| CREATE | `src/tools/understanding_verifier.py` | 2 |
| MODIFY | `src/storage.py` | 3 |
| MODIFY | `src/config.py` | 4 |
| MODIFY | `config/settings.yaml` | 4 |
| MODIFY | `src/bot/card_builder.py` | 5 |
| CREATE | `src/bot/verifier_runner.py` | 6 |
| MODIFY | `src/bot/command_handler.py` | 7, 8 |
| MODIFY | `src/bot/server.py` | 8 |
| CREATE | `tests/test_understanding_types.py` | 9 |
| CREATE | `tests/test_understanding_verifier.py` | 9 |
| CREATE | `tests/test_json_utils.py` | 9 |
