Run the ArXistant test suite.

```
conda run -n llm python -m pytest tests/ -v --tb=short $ARGUMENTS
```

Summarize: X passed, Y failed, Z skipped. If any tests failed, show the failure output and suggest likely causes. If all pass, confirm it.
