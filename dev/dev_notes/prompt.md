# Start

Project: AI Research Assistant for Literature Monitoring and Analysis

Goal:
Build a personal AI research assistant that monitors arXiv regularly, filters papers by my research interests, generates research-oriented summaries, and gradually learns my preferences through feedback. The project name is ArXistant.

Current MVP goal:
1. Read topic configuration from local files
2. Fetch recent arXiv papers
3. Filter papers by my interests
4. Generate a local Markdown daily report

What the assistant should eventually do:
- Regularly monitor arXiv, and later possibly other databases
- Detect papers relevant to user-defined research topics
- Produce a short report first, with optional detailed follow-up analysis
- For each relevant paper, analyze:
  - what problem it addresses
  - what is genuinely new
  - how it compares with prior work
  - possible weaknesses or open questions
  - how relevant it is to my interests
- Later send reports to Feishu
- Later ingest my feedback from chat and store it into structured long-term notes
- Gradually build topic-specific knowledge and adapt to my judgment style

Important product direction:
- This is not just a summary bot
- It should act like a research assistant that performs first-pass scientific judgment
- It should avoid dumping everything into one mixed notebook
- Long-term memory should be structured into:
  1. paper-level notes
  2. topic-level notes
  3. user preference notes

Architecture preferences:
- Keep the system simple, modular, and maintainable by a single developer
- Separate modules such as:
  - config
  - collectors
  - filters
  - analyzers
  - reports
  - channels
  - memory
  - storage
- Do not over-engineer
- Prefer minimal dependencies
- Prefer SQLite + Markdown in early stages
- Keep model providers replaceable

Model provider requirement:
- I want to use Claude Code to develop the project
- But runtime model APIs should remain replaceable
- Design for future support of providers like Anthropic, Zhipu, and Qwen
- Do not hard-code the whole system around one model provider

Current scope:
Please focus only on the first local MVP:
- topic config
- arXiv fetch
- basic relevance filtering
- local Markdown report

Not in scope yet unless explicitly requested:
- Feishu bot
- WeChat integration
- vector database
- multi-user support
- production deployment

Development style:
- Prefer small, incremental, testable changes
- Explain things simply; I know Python but I am not a professional software engineer
- Use practical solutions and avoid unnecessary complexity

Required workflow after each coding step:
Always do these three things:
1. Explain which files were created or modified and why
2. Give the exact local test commands
3. Update README.md if setup, usage, config, structure, or workflow changed

Local environment:
- Use the conda environment `llm` for development, debugging, and local runs
- Assume `conda activate llm` before Python commands unless I say otherwise

Workspace rule:
- Treat the current project root as the main workspace
- Do not write files outside the project directory unless I explicitly request it

What I want you to do first:
Help me build the smallest working prototype:
1. define a simple project structure
2. create config format for monitored topics
3. implement recent arXiv fetching
4. implement rough paper filtering
5. generate a Markdown daily report
6. keep the code simple and easy to understand

You can find the development notes in `/home/shangguan/Softwares/my_modules/ArXistant/dev/dev_notes`, where the user will save his notes and prompts. Read the Claude_启动提示词_科研助手.md to better understand the project overall plan. You can use the `/home/shangguan/Softwares/my_modules/ArXistant/dev` folder to work on specific feature developments in the future. Write down this rule in the Claude.local.md as my personal habbit.

I have added the Claude.md and Claude.local.md. Please read them carefully and suggest if revision is needed. Please also add the README file for this project.


# Next step

The MVP seems works. I want you to add the unit tests so that we can check if things work more easily. Then, work on the score inflation problem.

For future tests, switch the catagory to astro-ph.GA and astro-ph.HE.

## Next major step

OK, I want you to plan the next major step.

The general plan looks okay. However, I think we do not need a "score" in the code. A papers cannot be just labeled by a score. Instead, we need a knowledge tree that connect to each paper. One paper can be connected by different subjects/concepts/questions. We do not have a knowledge tree from the beginning. I would suggest that we build one gradually during reading the paper. This is similar to a student's learning. I guess the config/topics.yaml can serve for this knowledge tree. Please think carefully and give me a plan.

To fix the GLM problem, I want you to use GLM-5 or the equivalent newer model. I think it will be much faster and hopefully also solve the problem. Please investigate which model you should use in plan mode first.

It takes too long to test the full paper sets. Let's limit the paper list in the unit test. Let's just use 2 batches which should be enough to test all the features in the future.


# Refine the reading tool

I want to have three tools (or functions) to read the paper. One simple reading, only to understand if the paper is relevant or not by reading the title, authors, and abstract. This tool helps to catagorize the paper and decide whether the detailed reading tool read it. Then, the detailed reading function will read the whole text of the paper including the figure caption. However, this function does not need to read the figures (so that we do not need a visual model). Then, only for detailed study, we will need the third function to understand the figure given some context. I also want a function to be able to search the internet and collect useful references to help understand the main paper further. Do we need another function or incorporate it into one of the above function? Think carefully and give me a plan.

Oh, I forgot to mention that the web search and figure recognition functions can be scheduled now but implemented later.

## Close this part

Evaluate if the phase 3 items are still relevant or not.


# Implement the Feishu interaction capability

I want you to implement that the code can be launched as a service in the server. Then, it can talk to me on Feishu. Provide me an implementation plan. Note that I need to be able to talk to it and we may have complicated discussion (at least in the future). For the routine report of paper brief summary, I want you to design a good template so that we see standardized output. While the code shall provide a comprehensive list for a sub-catagory (e.g. astroph.GA and astroph.HE), I want to tell it my preference (or it can learn my preference), and order the list according to it.


# Refine the work flow

I want you to provide the summary of all the sub-area in astroph after scan over the papers. Then, when I ask for more details in a particular paper. Read in detail and make a more comprehensive summary.


# Feishu Bot Implementation (Completed)

Implemented the full Feishu bot service as planned. Here is a summary of what was done.

## What was built

A new `src/bot/` package that turns ArXistant into a persistent Feishu bot service.

### New files created

| File | Purpose |
|------|---------|
| `src/bot/__init__.py` | Package init |
| `src/bot/server.py` | FastAPI app with webhook endpoint and lifespan management |
| `src/bot/feishu_client.py` | Feishu API: auth token, send message/card, signature verification |
| `src/bot/command_router.py` | Regex-based command parsing (/scan, /read, /report, /tree, /help, /prefs, /reset) |
| `src/bot/command_handler.py` | Command execution: all 7 commands + natural language fallback |
| `src/bot/conversation.py` | Session-based conversation engine with LLM, tool-use detection |
| `src/bot/card_builder.py` | Feishu interactive card JSON templates (scan, read, report, tree, prefs, help) |
| `src/bot/session_store.py` | SQLite-backed session message history (rolling last N messages) |
| `src/bot/preference_store.py` | SQLite-backed user preference CRUD + learning (weight boost on interactions) |
| `src/bot/scheduler.py` | APScheduler for daily cron report push |
| `src/bot/prompts.py` | Bot-specific system prompt for conversation engine |
| `tests/test_command_router.py` | Tests for command parsing |
| `tests/test_card_builder.py` | Tests for card building |
| `tests/test_session_store.py` | Tests for session store |
| `tests/test_preference_store.py` | Tests for preference store |
| `tests/test_conversation.py` | Tests for conversation tool extraction |
| `tests/test_bot_server.py` | Tests for bot server endpoints |

### Existing files modified

| File | Changes |
|------|---------|
| `src/config.py` | Added feishu/bot settings fields (app_id, app_secret, bot_name, webhook_path, etc.) |
| `src/storage.py` | Schema v3: added chat_sessions, session_messages, user_preferences tables |
| `src/llm_client.py` | Added `chat_completion_messages()` for multi-turn conversation |
| `config/settings.yaml` | Added `feishu:` and `bot:` configuration sections |
| `.env.example` | Added FEISHU_APP_ID, FEISHU_APP_SECRET env vars |
| `requirements.txt` | Added fastapi, uvicorn, httpx, apscheduler |
| `tests/conftest.py` | Added `bot_settings` fixture |
| `tests/test_storage.py` | Updated schema version assertion (v2 → v3) |

### Key design decisions

- **Raw httpx** for Feishu API (no lark-oapi SDK dependency)
- **asyncio.create_task()** for webhook handler (Feishu requires 3s response, LLM takes 10-30s)
- **run_in_executor()** to call sync scan_paper/read_paper from async handler
- **SQLite** for sessions and preferences (single-user, no Redis needed)
- **Preference learning**: scan with relevance >= 3 → weight +1, read → weight +2, button click → weight +2
- **Report ordering**: `sort_key = quality_score + sum(pref.weight * link.relevance_score)`

### How to run

```bash
# Install dependencies
pip install -r requirements.txt

# Configure Feishu app credentials in .env
# FEISHU_APP_ID=your_app_id
# FEISHU_APP_SECRET=your_app_secret
# FEISHU_VERIFICATION_TOKEN=your_token

# Start the bot service
uvicorn src.bot.server:app --host 0.0.0.0 --port 8000
```

### Feishu app setup steps

1. Go to [Feishu Open Platform](https://open.feishu.cn/) → Create an app
2. Enable the bot capability (机器人能力)
3. Get App ID and App Secret from Credentials page
4. Set up Event Subscriptions:
   - Request URL: `https://your-server:8000/feishu/webhook`
   - Subscribe to: `im.message.receive_v1`
   - Set verification token and encrypt key
5. Set bot permissions: `im:message`, `im:message:send_as_bot`
6. Add the bot to a group chat and note the chat_id
7. Configure `target_chat_id` in settings.yaml for scheduled reports

### Commands

| Command | Description |
|---------|-------------|
| `/scan <arxiv_id>` | Quick relevance scan |
| `/read <arxiv_id>` | Full-text reading with notes |
| `/report [GA\|HE\|all]` | Daily report as interactive card |
| `/tree` | Display knowledge tree |
| `/prefs` | Show preference weights |
| `/reset` | Clear conversation session |
| `/help` | List commands |
| *any other text* | Natural language conversation |

### Next steps to consider

- Web search and figure recognition tools (scheduled but not yet implemented)
- Multi-user support if needed
- Production deployment with proper reverse proxy and TLS
- Rate limiting for Feishu API (~10 msg/s)

## Update and re-schedule

Now, ArXistant can be run on Feishu. We need to reschedule our plan and see what is the most urgent to be developed further. I find that the code does not search for the latest arXiv paper for now when I use `/report`. Also the GA does not mean `galaxy dynamics`. I think the root name of the tree should be revised. Both `galaxy dynamics` and `High-Energy Astrophysical Transients` are subsubt topics. We should have a strategy to first go through a relatively large paper list and build up the knowledge tree; and we also should search for some established catagory methods. Think carefully and give me a plan.


### Debug the bot usage

There are also bugs when I interact with the bot. What is the best way to do debugging? Are you able to find the errors in the log when I run the bot?


## Bot Debugging System (Completed)

Implemented a systematic debugging strategy for the detached bot. Now every command, card callback, and scheduler job is traceable via unique request IDs.

### New files

| File | Purpose |
|------|---------|
| `src/bot/debug.py` | Error ring buffer, per-chat verbose toggle, request ID generation |
| `tests/test_debug.py` | Tests for ring buffer, verbose toggle, request ID format |

### Modified files

| File | Changes |
|------|---------|
| `src/bot/server.py` | `_setup_logging()` with RotatingFileHandler to `data/logs/bot.log`; `RequestIdFilter`; `req_id` generated per message/callback and threaded through async wrappers |
| `src/bot/command_handler.py` | `req_id` parameter on `handle_command`/`handle_card_callback`; errors recorded to ring buffer with `_build_debug_error_card`; added `_handle_debug()` handler |
| `src/bot/card_builder.py` | `build_debug_card()` for listing recent errors; updated `build_help_card()` with `/debug` |
| `src/bot/command_router.py` | Added `/debug [on|off]` pattern |
| `src/bot/feishu_client.py` | `FeishuAPIError` exception and `_check_response()` for contextual error messages on HTTP failures |
| `src/bot/scheduler.py` | `record_error()` in `_push_daily_report` with request ID |

### Key features

- **Request IDs**: Every command and card callback gets a 6-char hex ID (e.g. `a3f2b1`) that appears in file logs and error cards
- **Error ring buffer**: Last 50 errors stored in memory, viewable via `/debug` command in Feishu
- **Verbose mode**: `/debug on` enables full tracebacks in error cards; `/debug off` disables them
- **File logging**: Rotating log file at `data/logs/bot.log` (5 MB, 3 backups) with DEBUG level; console stays at INFO
- **Feishu API errors**: All API calls now raise `FeishuAPIError` with operation name, status code, and response body

### Usage

| Command | Description |
|---------|-------------|
| `/debug` | Show last 10 errors |
| `/debug on` | Enable verbose mode (full tracebacks in error cards) |
| `/debug off` | Disable verbose mode |

### Log file format

```
2026-04-22 13:25:01,234 [ERROR] [a3f2b1] src.bot.command_handler: Command handler failed [a3f2b1]: scan
```


## Debug the /fetch error

It seems that the filtering does not find the really relevant papers. Please check if there is any bugs first. I wonder where the 