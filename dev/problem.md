# Recurring Problems

## ArXistant Development

### arXiv API date filter causes HTTP 500
- The arXiv API does not reliably support `submittedDate:[date TO NOW]` query syntax.
- **Fix**: Use simple `cat:` queries sorted by submission date, and filter dates client-side.
- First encountered: 2026-04-21 (initial pipeline run)

### LLM scores may be inflated
- GLM-4-flash tends to give generous scores. 18/28 papers scored >= 3 in the initial MVP.
- **Fix (Phase 1.5)**: Stricter system prompt with expected distribution, cumulative decision criteria, self-check if >40% scored >= 3. Raised threshold to 4.
- **Status**: Mitigated but still worth monitoring with the new two-axis analysis in Phase 2.

### conda env `llm` was empty
- The `llm` conda env existed but had no Python installed.
- **Fix**: Install Python 3.12 explicitly: `conda install -n llm python=3.12 -y`
- **Watch for**: Always verify `which python` points to the expected env before running.

### `get_links_for_paper` and `get_pending_candidates` return dicts, not dataclasses
- JOIN queries add extra columns (e.g., `node_name`, `parent_name`) that don't fit the `PaperTreeLink` / `CandidateNode` dataclass fields.
- **Fix**: Changed return types to `list[dict]` to include the extra columns. All callers use dict access (`link["relevance_score"]`) instead of attribute access.
- First encountered: 2026-04-21 (Phase 2 test development)
- **Watch for**: Any new JOIN queries in storage.py should return dicts, not dataclass instances.

### LLM analysis prompt is untested against real data
- The `analyze_papers()` module has been tested with mocked LLM responses but not with real GLM/Claude API calls.
- **Risk**: The actual JSON format may differ from expectations, parsing could fail.
- **Mitigation**: `_parse_analysis_response()` has three fallback strategies (direct JSON, code fence, brace extraction), same proven pattern as `filter.py`.
- **Status**: Tested end-to-end. Batch 2/7 consistently fails to parse. See next item.

### GLM-4-flash returns malformed JSON for ~14% of batches
- Batch 2/7 fails to parse in both end-to-end runs (reproducible). 6 papers lost per run.
- **Likely cause**: The response may be truncated (too long for the model's output limit), or may contain non-JSON preamble/postamble that the three fallback strategies don't catch.
- **Impact**: ~14% of papers are silently dropped. They remain in the DB as `quality_score IS NULL` and will be re-analyzed on the next run — but only if we change the query to also include previously-failed papers.
- **Fix needed**: (1) Log the raw LLM response when parsing fails for debugging. (2) Consider reducing batch size or truncating abstracts. (3) Add a retry mechanism for failed batches.

### No candidate nodes proposed by the LLM
- Across 36 analyzed papers (quality >= 3 for many), the LLM never proposed a new tree node.
- **Possible cause**: The system prompt says "Only propose at most ONE candidate node per paper. Set to null if no proposal warranted." The LLM may be interpreting this too conservatively. Alternatively, the existing tree nodes may already cover the concepts well enough.
- **Status**: Low priority — the feature works but needs tuning after more data.

### Paper deduplication is per-node only in the report
- A paper linked to 3 nodes (e.g., Galactic Dynamics, Bar Formation, Bar-driven Secular Evolution) appears 3 times in the report — once under each node. This is correct behavior but makes the report long.
- **Future improvement**: Add a "see also" cross-reference instead of repeating the full entry.

### arXiv HTML not available for all papers
- Some arXiv papers (especially older ones or non-LaTeX submissions) do not have an HTML version at `https://arxiv.org/html/{id}`, returning 404.
- **Handling**: `PaperHtmlUnavailableError` is raised by `fetch_arxiv_html()`. Callers (e.g., `read_paper`) should catch this and inform the user that the paper cannot be read in full-text mode.
- **First encountered**: 2026-04-21 (Phase 5 HTML parser development)

### Section heading numbers concatenate with titles in LaTeXML output
- arXiv HTML uses `<span class="ltx_tag">1</span>` inside headings, which causes `get_text()` to produce "1Introduction" instead of "1 Introduction".
- **Fix**: Decompose the number span before extracting heading text in `_extract_sections()`.
- **First encountered**: 2026-04-21 (Phase 5 HTML parser test development)

### `reading_notes` table intentionally has no FK to `papers`
- The original plan had `REFERENCES papers(arxiv_id)` but this would prevent storing reading notes for papers that haven't been collected by the pipeline. Removed the FK constraint.
- **Watch for**: If we ever need to enforce referential integrity, add a deferred FK or a cleanup step.
