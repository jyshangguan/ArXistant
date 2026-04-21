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
- **Next step**: Run end-to-end with real data in Phase 3.
