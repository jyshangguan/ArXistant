Run the ArXistant CLI pipeline as an end-to-end test.

Steps:
1. Run the pipeline:
```
conda run -n llm python -m src.main
```
2. Check the output for:
   - Number of papers collected
   - Number of new papers stored (deduplicated by arxiv_id)
   - Number of papers analyzed
   - Report file path
3. If there are errors, analyze them and suggest fixes.
4. If successful, show a brief summary.

Context:
- Uses tree-aware mode (SQLite at data/arxistant.db).
- Already-stored papers are skipped.
- Already-analyzed papers (quality_score IS NOT NULL) are not re-analyzed.
- API key is in .env (GLM_API_KEY).
- Use conda environment `llm`.
