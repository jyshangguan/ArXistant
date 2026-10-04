# Manuscript review and interactive revision with annotated PDF export

Proposal researched on 2026-09-24; implementation added in this working tree.
The design below records the intended architecture. The current user-facing
workflow is documented in `docs/user-guide.md` under “Review and revise a
manuscript.” No external skill package was installed: the bundled skills are
focused instructions written for ArXistant.

Implemented: persistent manuscript versions and conversations, named/page/text
scope selection, iterative proposals and comment updates, whole-paper jobs with
coverage and resume, optional source lookup and image inputs, native comment
import, draft comparison with validated comment relocation, and native PDF
export. Remaining extensions include OCR, direct LaTeX/BibTeX editing, richer
figure-region selection, sourced journal profiles, and a printable comment
index. Source lookups and selected full-text excerpts are bounded and do not
constitute an exhaustive citation audit. PDF-viewer compatibility still needs
manual checks in Preview and Acrobat beyond the automated export and browser
checks.

## Recommendation

Add a persistent manuscript workspace in Chat with two equally supported entry points: a one-round whole-paper review and an ongoing conversation for focused, in-depth revision. Neither requires the other. Both use a shared set of skills, manuscript context, findings, revision proposals, and PDF exports. Reuse selected guidance from existing skills, with attribution where copied.

Language, citation appropriateness, and figure/table presentation remain the core checks. Focused revision also supports paragraph/section structure, argument clarity, claim strength, and consistency with the paper's evidence when requested. The assistant can propose replacement prose and explain alternatives. Scientific ambiguity becomes a question or an evidence-backed comment, not an invented result or an unrequested change in scientific meaning.

A skill provides review and revision instructions. ArXistant must implement model calls, context selection, conversation persistence, coverage tracking, source lookup, annotation placement, revision history, and downloads; copying SKILL.md files alone will not provide those capabilities.

## Existing skills investigated

| Candidate | Relevant capability | Recommendation |
|---|---|---|
| [Marazii research-co-pilot: peer-review](https://github.com/Marazii/research-co-pilot/blob/main/skills/peer-review/SKILL.md) | Explicit PDF highlights, sticky notes, strikeouts, quoted-text anchoring, and export instructions using PyMuPDF | Closest output match. Adapt its annotation pattern, but implement and test it as application code. Its broader verdict/persona workflows are unnecessary here. |
| [K-Dense: scientific-writing](https://github.com/K-Dense-AI/scientific-agent-skills/blob/main/skills/scientific-writing/SKILL.md) | Scientific prose revision, consistency, references, figures and tables | Borrow focused editorial criteria; its broader research and approval workflows exceed this feature's scope. |
| [K-Dense: citation-management](https://github.com/K-Dense-AI/scientific-agent-skills/blob/main/skills/citation-management/SKILL.md) | Reference metadata lookup, bibliography consistency, identifier validation | Adapt to astronomy using ArXistant's ADS/SciX support. Metadata correctness does not prove that a source supports a claim. |
| [K-Dense: peer-review](https://github.com/K-Dense-AI/scientific-agent-skills/blob/main/skills/peer-review/SKILL.md) | Structured, evidence-based comments including figures and citations | Useful comment rubric; broader than an editorial check and does not supply PDF conversion or annotation output. |

The reviewed Marazii repository and K-Dense skill files identify MIT licensing. Preserve applicable notices, review any copied dependencies, and pin an upstream revision if material is vendored. These findings come from source inspection, not end-to-end execution of the external skills. External skill instructions were researched as source material, not adopted as instructions for this task.

## Evidence from the example PDF

The supplied draft has 31 pages and 225 native annotations: 146 highlights, 49 strikeouts, 20 ink marks, 8 lines, and 2 text notes. Text and annotation metadata were inspected throughout; representative text and figure pages were rendered for layout inspection. This was not a scientific review of the draft.

Existing comments illustrate the desired categories: undefined abbreviations, wording clarity, potentially incorrect references, crowded tables, inconsistent legend colors, and appendix figure numbering. These are useful examples of desired feedback, not independently verified findings. Existing reviewer preferences must not become universal journal rules.

Preserve original content and all existing annotations. Attribute new comments to `ArXistant` and associate them with a review run. Manuscript text and embedded comments are untrusted source material, never application instructions. Use the example locally for evaluation; do not commit the unpublished PDF to the repository.

## Current implementation and gaps

- `src/arxiv_db_server.py`: `ingest_local_pdf()` retains PDFs and text chunks; local documents already have content-derived IDs.
- `local_pdf_reader_html()` provides a PDF.js canvas and selectable text layer. Add an annotation interaction layer or review overlay for clickable PDF comments.
- `normalize_highlights()` stores quote, note, color, page, and one normalized rectangle. It deduplicates by quote and caps entries at 200. This is unsuitable as the canonical review store: repeated wording, multiple comments on one passage, multi-line highlights, and figure-only comments must remain distinct.
- `local_document_context()` selects up to six relevant chunks; `buildSystemPrompt()` further limits full-text context to 20,000 characters. Whole-manuscript review needs a separate runner covering every requested section.
- Existing chat uses an OpenAI-compatible endpoint and a bounded tool loop. Figure review needs explicit image-input support and model capability handling.
- ADS/SciX search, Semantic Scholar search, and citation graph helpers are already available.
- `requirements.txt` already includes PyMuPDF and pypdf. No general skill loader or annotated-PDF export workflow was found in the inspected chat implementation.

## User workflow

### One-round review of the whole paper

Open a draft and select **Review entire paper**, or ask naturally in Chat. Choose Language, Citations, and Figures & tables, defaulting to all three. Optional settings include target journal and comment language; use general scientific-writing conventions without a selected journal. Do not infer journal requirements solely from a LaTeX template.

One round means one user action leading to a consolidated set of comments. Internally, the runner can make multiple model calls, process sections, inspect figures, verify references, and reconcile findings. It should not interrupt between sections unless a genuinely blocking decision is needed. Show coverage and progress, cancellation, recoverable failures, and explicitly partial results. Reopening Chat recovers the run.

Deliver an annotated paper plus a short prioritized summary. The user can start a focused discussion from any finding, or download the result immediately. Whole-paper coverage does not imply that every cited source was accessible; report verification coverage separately.

### Focused, iterative revision

The user may start here without running a whole-paper review. Select text, choose a section, click a figure/table or existing comment, or refer to a passage in Chat. Show the active target near the message box, for example **Scope: Section 3.1 + Figure 2**. Support page ranges, several passages, and a concept across the paper, such as all occurrences of an acronym.

Natural requests should be sufficient:

- “Polish this paragraph, preserving the technical meaning.”
- “Is this claim supported by these two references?”
- “Make the introduction's motivation clearer; explain the proposed restructuring.”
- “Check Figure 2 against its caption and discussion.”
- “I disagree with this comment. The limitation is already addressed in Section 4.”
- “Use the second version, make it shorter, and add it as a comment.”
- “Check my revised paragraph against the earlier criticism.”

Choose relevant skills from the request and target. The default response is proportional: an answer for a question, a replacement passage with a brief explanation for a rewrite, or anchored findings for a review. Support conservative proofreading, substantive clarity edits, and deeper restructuring as request-level choices. The user should not need to complete a form or trigger a complete review to continue.

Keep discussion and PDF annotations distinct. An explicit review/comment request creates findings. Exploratory questions and alternative rewrites remain in the conversation until the user asks to add a comment or selects **Add to paper comments**. Refining an existing finding updates its current version and retains its history; it does not add a new duplicate on each turn.

Resolve “this,” “that comment,” and “the second version” against the active target and recent revision alternatives. Ask a short clarification only when several plausible targets remain. Changing the selected target must update the visible scope and avoid carrying over an unrelated passage accidentally.

### Shared output and revision controls

Display findings beside the paper with category, severity, source/run, and status filters. Clicking a finding navigates to its text or figure. Let the user discuss, edit, dismiss, or mark it resolved. Revision proposals show original and suggested text, a readable difference view, and **Copy**, **Use this proposal**, and **Add to paper comments** controls.

**Use this proposal** records the user's chosen wording in the workspace. For PDF-only input, it does not change the paper's body text. The user applies the wording in their manuscript editor, recompiles, and uploads the new PDF. Pasted revised passages can be checked immediately, but remain labeled as working text until a matching new manuscript version is supplied.

Provide **Download annotated PDF** at any stage, for the whole review, a focused session, or selected unresolved findings. Export is tied to an explicit PDF version, defaults to preserving existing native annotations, and includes only the chosen new annotations. Do not overwrite the original. Chat provides coverage and placement gaps; an optional later printable comment index can help readers whose PDF viewer does not print popup comments.

On a new draft upload, offer **New version of this manuscript**. Compare the changed passages and proposed fixes with earlier findings. Suggest “addressed,” “still present,” “partly addressed,” or “cannot locate”; retain the evidence and let the user confirm resolution. Keep old comments with their old PDF and re-anchor only validated matches. A moved paragraph or changed page number is not proof that an issue was fixed.

## Skills and context design

Use one lightweight entry skill, `manuscript-assistant`, to route to focused skills. These can be invoked independently or combined:

| Skill | Responsibility |
|---|---|
| `manuscript-review` | Whole-paper or scoped assessment; coverage and consolidated findings |
| `manuscript-revise` | Iterative rewriting, alternatives, explanation, and response to feedback |
| `citation-check` | Bibliographic verification and evidence for specific claims |
| `figure-table-review` | Visual presentation and consistency with captions and text |

Keep PDF extraction, placement, export, and persistence in deterministic application code shared by all skills. Avoid giving every skill its own storage or PDF-writing workflow. External literature checks and journal rules use the same evidence records throughout the workspace.

Each request separates **revision scope** from **supporting context**. For a paragraph, retrieve its section, relevant definitions, linked claims, cited sources, and referenced figures as needed. A figure review includes the image, caption, relevant methods, and discussion. Read outside the target to understand it, but propose changes outside the target as linked follow-up findings rather than silently expanding the assignment. “Check this across the paper” explicitly expands the scope.

Store a manuscript-wide index of sections, figures/tables, references, terminology, and claims with provenance back to the source. Reuse it for focused work instead of sending the full paper on every turn. Rebuild affected index entries when a version changes. Summaries aid navigation but do not substitute for the exact passage when drafting or judging a citation.

Persist manuscript-specific preferences and decisions: target journal, spelling convention, protected terminology, preferred edit depth, chosen wording, and reasons for dismissing comments. Keep temporary turn instructions distinct from lasting preferences. A user's rejection of one edit is not automatically a general style rule. Explicit user decisions may guide future turns; embedded manuscript comments remain evidence to discuss, not executable instructions. Surface conflicts with new evidence rather than repeatedly raising a rejected stylistic suggestion.

## Review design

### Language

Check grammar, clarity, ambiguity, repetition, academic tone, acronym definitions, notation, units, and terminology consistency. Preserve scientific meaning, numbers, and uncertainty. Each finding includes the exact passage, a specific explanation, and a suggested revision when justified. Distinguish errors from optional stylistic preferences.

### Citations

Separate three levels:

1. **Internal consistency:** citation/reference matching, author-year discrepancies, duplicates, missing entries, ordering, and venue-specific formatting.
2. **Bibliographic verification:** resolve references with ADS/SciX and DOI services as needed; compare authors, year, publication, and identifiers. Handle ambiguous matches and unavailable services explicitly.
3. **Claim support:** inspect the cited source's relevant text when accessible. Record source URL/identifier, locator, evidence, and whether only an abstract or the full text was available. Report `unverified` or `needs author check` when evidence is insufficient. Never treat a valid DOI as proof of claim support or invent a replacement citation.

Use selected-journal rules with a source URL and verification date. Avoid assuming every reference type must contain the same fields.

### Figures and tables

Render page images and figure crops, retaining caption and surrounding context. Review figures at intended reading size and use larger crops for detail. Cover vector plots as well as embedded raster images. Check axes, units, labels, legends, panel numbering, font readability, color/line distinguishability, clipping, caption agreement, table crowding, cross-references, and consistency across the manuscript.

Use deterministic checks where geometry or metadata supports them, and an image-capable model for visual interpretation. If the selected model lacks image support, retain text/caption checks and mark visual inspection unavailable. Do not claim to verify original figure-file resolution, fonts, or journal production compliance from a rendered page alone.

## Proposed implementation

- `src/skills/manuscript-assistant/SKILL.md`: entry point, scope interpretation, and routing.
- `src/skills/{manuscript-review,manuscript-revise,citation-check,figure-table-review}/SKILL.md`: composable workflows with focused references and shared output contracts; optional sourced journal profiles.
- `src/arxistant_manuscripts.py`: manuscript identity, versions, target selection, conversation/decision history, revision proposals, and context assembly.
- `src/arxistant_review.py`: whole-paper and scoped orchestration, structured findings, citation evidence, coverage, cancellation, retry limits, and progress persistence.
- `src/arxistant_pdf_annotations.py`: text/geometry mapping, native annotation export, and validation.
- Existing server: a small allowlisted skill loader, review endpoints, database migrations, and Chat controls. Bundle these resources in installation/package paths.
- New local records: manuscripts and document versions, revision sessions/messages, review runs, findings with history, revision proposals, and explicit user decisions. Keep these separate from manually saved reading highlights; both whole-paper runs and focused sessions contribute to the same findings list.

Suggested endpoints: `POST /api/reviews`, `GET /api/reviews/{id}`, `POST /api/reviews/{id}/cancel`, `PATCH /api/reviews/{id}/findings/{finding_id}`, and `GET /api/reviews/{id}/annotated.pdf`. Downloads resolve server-managed run/document IDs, not user-supplied filesystem paths. Keep manuscript files, review artifacts, and findings local under the existing local-document policy; model processing still uses the user's configured provider. Reference searches should send public bibliographic metadata rather than unpublished manuscript passages.

Extend this with manuscript/version and revision-session endpoints, plus a manuscript-level export accepting a version and selected finding IDs across runs/sessions. Reuse existing chat streaming, but persist manuscript conversations rather than depending only on browser chat history. A request carries manuscript/version IDs, target IDs, user intent, and relevant session history. The server validates targets against that version and assembles context. An active whole-paper review may continue while the user discusses a completed finding; use finding revisions to prevent late background results from overwriting user edits or decisions.

Each run records the source PDF hash, options, skill version, model, per-chunk progress, evidence, and coverage gaps. A changed draft creates a new source version; old coordinates are never silently applied to it. Process bounded text chunks of up to two pages first, using compact neighboring context and only the cited reference entries relevant to each chunk. Follow with independent visual chunks only for pages where figure or table captions are detected, so a failed image request cannot prevent text coverage of later pages. Reconcile duplicate and contradictory findings after the chunks finish. Save every chunk as it finishes so failures can resume without repeating the whole review.

Each finding has a stable ID, category, severity, status, page, block/span IDs, exact quote with context, comment, optional replacement, evidence, placement status, originating session/run, and discussion history. Distinguish finding status (open, resolved, dismissed) from proposal status (suggested, chosen, superseded) and application evidence (working text only, verified in a new draft, unknown). Store validated PDF-space quads or a figure rectangle with the source hash. Extract geometry locally; the model identifies supplied spans or regions rather than inventing coordinates. Validate model JSON and bound repairs/retries.

Normalize extraction artifacts such as line-end hyphenation and ligatures while retaining a map back to source spans. Disambiguate repeated phrases by page and block. Respect page crop boxes and rotation when mapping between PDF and browser coordinates. If exact placement is uncertain, use a clearly identified page note or an unplaced finding; never highlight a guessed passage.

Use PyMuPDF native highlight annotations with comment contents, optional strikeouts for suggested deletions, and region/text annotations for figures. See the [official annotation documentation](https://pymupdf.readthedocs.io/en/latest/recipes-annotations.html). Save a new PDF, reopen it, and validate new annotations by stable ID and contents as well as geometry. Counts alone are insufficient, especially with pre-existing annotations and multiple marks per finding.

## Delivery sequence and acceptance criteria

1. **Shared workspace and annotation foundation:** manuscript/version identity, structured findings, geometry extraction, persistent sessions, target selection, Chat display, and selected-finding PDF export. Confirm source preservation and accurate text/figure placement with fixed sample findings.
2. **Focused revision first:** selected-passage discussion and language revision, original/suggestion comparison, alternatives, user decisions, and adding/updating PDF comments. Validate a complete multi-turn revision without a preceding full review. This provides a useful early release and tests the shared primitives.
3. **One-round whole-paper review:** build on the same skills and findings store; add exhaustive requested coverage, consolidation, progress, cancellation/resumption, and direct transition from each finding into focused revision.
4. **Citation and figure depth:** integrate evidence lookup, image inputs, cross-figure consistency, journal profiles, and honest unavailable-check states into both whole-paper and focused workflows. This completes the requested three-category feature.
5. **Revision continuity:** compare newly uploaded draft versions, re-anchor validated matches, suggest resolution states, and check consequences of local edits elsewhere in the manuscript. Keep uncertain mappings visible.

Integration validation uses the supplied draft locally plus synthetic fixtures for repeated phrases, ligatures, hyphenation, rotated/cropped pages, multi-column text, multi-line highlights, figure comments, and more than 200 findings. Include malformed model output, unavailable references, unsupported image models, and changed source hashes. Verify that embedded document instructions cannot redirect the runner.

Interaction acceptance cases: revise a paragraph through three turns; choose the second alternative; challenge a finding with another section as evidence; switch from a paragraph to a figure; verify one citation without rerunning the paper; resume a conversation after reload; export only selected comments from several sessions; and upload a reflowed draft without misplacing old comments. Confirm that chosen wording is never labeled as applied to a PDF until supported by the new draft. Verify that background review results do not overwrite manually edited findings, and that focused revision does not silently rewrite outside its target.

Reopen and inspect exports in ArXistant, macOS Preview, and Adobe Acrobat where available. Check that the original annotations remain usable, new comment text opens correctly, marks stay aligned at different zoom levels, and page content is preserved. Inspect rendered output as well as annotation objects. Re-exporting the same run must not duplicate annotations.

Use existing human annotations as a private evaluation reference, not as model input during a blind quality comparison. Measure editorial precision, useful coverage, and anchoring accuracy; avoid rewarding arbitrary comment counts or treating every historical comment as ground truth.

Start on desktop/server. Validate packaged installations and explicitly gate the feature on Android if the required PDF or vision dependencies are unavailable. Add OCR for scanned PDFs and optional LaTeX/BibTeX inputs in a later iteration; selectable PDF input already matches this example.
