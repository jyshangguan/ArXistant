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

A minor point, I also want you to use GLM-5 or the equivalent newer model. I think it will be much faster.


# Refine the reading tool

I want to have two tools (or functions) to read the paper. One simple reading, only to understand if the paper is relevant or not by reading the title, authors, and abstract. This tool helps to catagorize the paper and decide whether the detailed reading tool read it. Then, the detailed reading function will 