# Recurring Problems

## ArXistant Development

### arXiv API date filter causes HTTP 500
- The arXiv API does not reliably support `submittedDate:[date TO NOW]` query syntax.
- **Fix**: Use simple `cat:` queries sorted by submission date, and filter dates client-side.
- First encountered: 2026-04-21 (initial pipeline run)

### LLM scores may be inflated
- GLM-4-flash tends to give generous scores. 18/28 papers scored >= 3, which is higher than expected.
- **Watch for**: Consider tuning the system prompt or raising the relevance threshold if too many false positives appear.

### conda env `llm` was empty
- The `llm` conda env existed but had no Python installed.
- **Fix**: Install Python 3.12 explicitly: `conda install -n llm python=3.12 -y`
- **Watch for**: Always verify `which python` points to the expected env before running.
