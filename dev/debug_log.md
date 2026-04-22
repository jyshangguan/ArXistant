# Debug Log

A record of bugs found, diagnosed, and fixed.

---

## 2026-04-22: `/fetch` fails with SQLite thread safety error

**Request IDs:** `03bfdd`, `4b9e11`

**Symptom:** `/fetch` command always fails. Error card shows:
```
SQLite objects created in a thread can only be used in that same thread.
The object was created in thread id 137563591092032 and this is thread id 137563373303360.
```

**Root cause:** `init_db()` in `src/storage.py` creates the SQLite connection in the main thread. `/fetch` calls `run_collect_and_analyze()` via `loop.run_in_executor()`, which runs in a thread pool thread. SQLite rejects cross-thread connection access by default.

**Traceback:**
```
File "src/bot/command_handler.py", line 281, in _handle_fetch
    stats = await loop.run_in_executor(...)
File "src/main.py", line 73, in run_collect_and_analyze
    topics = derive_topics_from_tree(conn)
File "src/tree.py", line 148, in derive_topics_from_tree
    roots = get_tree_children(conn, parent_id=None)
File "src/storage.py", line 283, in get_tree_children
    rows = conn.execute(...)
sqlite3.ProgrammingError: SQLite objects created in a thread can only be used in that same thread.
```

**Fix:** Changed `src/storage.py` line 200 from:
```python
conn = sqlite3.connect(str(db_path))
```
to:
```python
conn = sqlite3.connect(str(db_path), check_same_thread=False)
```

**Why this is safe:** The bot is single-user. The connection is not used concurrently within a single operation (default thread pool runs one call at a time). The async event loop serializes command handling, so no two `/fetch` calls overlap on the same connection.

**Also fixed during this session:** The debug system had a gap — `_handle_fetch`, `_handle_chat`, and `_handle_build_accept` each had internal `try/except` blocks that swallowed errors before reaching the debug-aware outer handler. These were updated to use `record_error()` and `_build_debug_error_card()` with `req_id`.

---

## 2026-04-22: `/fetch` redesign — slow batch LLM → fast keyword filter

**Symptom:** `/fetch` was painfully slow (~10 min) due to batch LLM analysis of all unanalyzed papers. With ~57 unanalyzed papers requiring ~10 LLM batches, GLM rate limits caused most batches to fail. Users rarely benefit from analyzing all papers at once.

**Root cause:** The `/fetch` pipeline ran `run_collect_and_analyze()`, which collected papers from arXiv AND then LLM-analyzed ALL unanalyzed papers in batches. This was designed for the CLI pipeline where you want everything analyzed at once, but in the bot context it blocks the user for too long.

**Fix (Phase 6):** Split the pipeline into two paths:
1. **`/fetch`** (fast): `collect_and_store()` + `keyword_pre_filter()` → list card with [Scan]/[Read] buttons. No LLM calls. Completes in ~10 seconds.
2. **`/scan` and `/read`** (on-demand): LLM analysis runs per-paper when the user explicitly requests it.
3. **Scheduler** (overnight): Still uses `run_collect_and_analyze()` with full batch LLM analysis — this is fine since it runs unattended.

**Files changed:** `src/filter.py`, `src/storage.py`, `src/main.py`, `src/bot/command_handler.py`, `src/bot/card_builder.py`, `src/analyze.py`, `src/config.py`.

**Also during this session:** Reverted aggressive rate-limit workarounds in `analyze.py` (max_retries 5→3, base_delay 30→15) since batch analysis is no longer on the critical path. Reverted `batch_delay` default to 5 in `src/config.py`. Added `pre_filter_max: int = 30` setting.
