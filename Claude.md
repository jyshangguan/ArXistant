# CLAUDE.md

## Project

This project is an AI research assistant for scientific literature monitoring and analysis.

Current MVP goal:
- read topic configuration
- fetch recent arXiv papers
- filter papers by user interests
- generate a local Markdown daily report

Not in scope yet unless explicitly requested:
- Feishu bot implementation
- WeChat integration
- complex database design
- vector database
- multi-user support
- production deployment

---

## Developer habits

Read `Claude.local.md` for local environment setup, API configuration, and personal workflow notes specific to this developer.

---

## General principles

- Prefer small, incremental, testable changes.
- Keep the system simple and modular.
- Avoid unnecessary frameworks and dependencies.
- Do not change unrelated files unless necessary.
- Prefer practical solutions that a single developer can maintain.
- Explain engineering concepts in simple language.

---

## Architecture preferences

Keep source collection, filtering, analysis, reporting, messaging, and memory loosely coupled.

Prefer modules such as:
- config
- collectors
- filters
- analyzers
- reports
- channels
- memory
- storage

Do not over-engineer for future features too early.

---

## Model provider rule

Model providers must be replaceable.

Design the code so it can support multiple APIs later, such as:
- Zhipu
- Qwen
- Anthropic
- OpenAI

Do not hard-code the whole project around one model provider.

---

## Memory rule

Do not store everything in one mixed notebook.

Prefer structured memory:
- paper-level notes
- topic-level notes
- user preference notes

Avoid dumping raw chat history directly into long-term memory.

---

## Communication style

The user knows Python but is not a professional software engineer.

When explaining things:
- use simple language
- avoid unnecessary jargon
- give practical next steps
- clearly separate “do now” from “do later”

---

## Required workflow after each coding step

After any implementation step or code change, always do these three things:

1. Explain which files were created or modified and why.
2. Give the exact local test commands.
3. Update README.md if setup, usage, configuration, structure, or workflow changed.

Do not skip this unless the user explicitly says so.

---

## Dependency and config rules

- Prefer minimal dependencies.
- Explain why any new dependency is needed.
- Use environment variables for secrets.
- Keep user-editable settings in clear config files such as YAML or TOML.
- Do not hide important behavior in hard-coded constants.

---

## Language rule

Use English by default for this project.

This applies to:
- code
- filenames
- function/class/module names
- configuration files
- prompts
- reports
- notes
- memory records
- README and documentation

Do not use Chinese unless the user explicitly requests it.

If the user provides input in Chinese, you may discuss it with the user in Chinese when appropriate, but stored records, structured notes, and project artifacts should still remain in English unless the user asks otherwise.

Preserve original paper titles, quoted text, and proper nouns in their original form when needed.

---

## Current priority

Focus on the smallest working prototype first:

1. load topic configuration
2. fetch recent arXiv papers
3. filter papers roughly by relevance
4. output a local Markdown report

Do not jump to later phases unless requested.