Check the status of the ArXistant Feishu bot service.

Steps:
1. Check if the bot process is running:
```bash
ps aux | grep "src.bot.server" | grep -v grep
```

2. If running, show PID and check recent log entries:
```bash
tail -30 data/logs/bot.log
```

3. If not running, report that and offer to start it with:
```
conda run -n llm uvicorn src.bot.server:app --host 0.0.0.0 --port 8000
```

4. Check for recent errors:
```bash
grep -i "error\|failed\|traceback" data/logs/bot.log | tail -20
```

5. Summarize: is the bot running? Any recent errors? Last activity timestamp?
