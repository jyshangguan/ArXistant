Run tests for a specific module.

Module: $ARGUMENTS

Map the module name to its test file and run it:

| Module | Test file |
|--------|-----------|
| config | tests/test_config.py |
| collector | tests/test_collector.py |
| llm_client | tests/test_llm_client.py |
| filter | tests/test_filter.py |
| analyze | tests/test_analyze.py |
| storage | tests/test_storage.py |
| report | tests/test_report.py |
| tree | tests/test_tree.py |
| main | tests/test_main.py |
| bot_server | tests/test_bot_server.py |
| command_router | tests/test_command_router.py |
| card_builder | tests/test_card_builder.py |
| conversation | tests/test_conversation.py |
| session_store | tests/test_session_store.py |
| preference_store | tests/test_preference_store.py |
| scheduler | tests/test_scheduler.py |
| debug | tests/test_debug.py |
| scan_paper | tests/test_scan_paper.py |
| read_paper | tests/test_read_paper.py |
| html_parser | tests/test_html_parser.py |

If the name doesn't match exactly, try fuzzy matching against the table above. If no match, try `tests/test_{name}.py`.

Run with:
```
conda run -n llm python -m pytest <test_file> -v --tb=short
```

Report pass/fail counts and any failures.
