Search the ArXistant bot logs for a pattern or show recent entries.

Query: $ARGUMENTS

Log files:
- data/logs/bot.log (current, rotating 5MB x 3)
- data/logs/bot.log.1
- data/logs/bot.log.2

If no argument provided:
```bash
tail -50 data/logs/bot.log
```
Highlight any lines containing ERROR, FAILED, or Traceback.

If argument provided:
```bash
grep -i "$ARGUMENTS" data/logs/bot.log data/logs/bot.log.1 data/logs/bot.log.2 2>/dev/null | tail -50
```

Smart analysis for common patterns:
- Error/exception/traceback → show surrounding context (5 lines before/after)
- Rate limit/retry → count occurrences, show timing spread
- A request ID (6 hex chars like `a3f2b1`) → find all log lines with that ID
- An arxiv_id (like `2604.12345v1`) → find all operations for that paper
- A datetime → filter logs around that time

Summarize findings concisely.
