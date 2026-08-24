---
layout: default
title: User guide
description: Learn the daily ArXistant workflow, reminders, search, publications, and ranking controls.
nav_order: 2
---

# ArXistant user guide

## First launch

1. Make sure the local server is running.
2. Click the red ArXistant icon in Chrome.
3. Choose **Open Daily Papers**.
4. Open **Settings** to choose reminder times and retraining behavior.

The server health endpoint is
[http://localhost:8765/api/health](http://localhost:8765/api/health).

## Main pages

| Page | Address | Purpose |
|---|---|---|
| Daily Papers | `/daily.html` | Today's ranked submissions |
| Recent Papers | `/recent.html` | Approximately five days of ranked papers |
| Saved Papers | `/database.html` | Search, annotate, and remove saved papers |
| Chat | `/chat.html` | Ask questions about one paper with an LLM |
| My Publications | `/publications.html` | Import and manage your publication list |
| Search arXiv/ADS | `/search-arxiv.html` | Find papers and save them locally |
| ML Features | `/ml-features.html` | Inspect training state and ranking features |

All addresses are served from `http://localhost:8765`.

## Daily reading workflow

Click a paper title to open its arXiv record and use the disclosure control to
read its abstract. The relevance score reflects the current local model and any
matching custom keywords.

Click **💾** to save a useful paper. Saved papers become positive examples for
future model training. Removing a saved paper updates the local training state.

## Chat: the paper reading helper

The Chat page lets you read a paper with an LLM: pick one paper, then ask
questions about it and get grounded, streamed answers.

### Configure an LLM

1. Open **💬 Chat**.
2. In **LLM Settings**, pick a provider preset (OpenAI, DeepSeek, OpenRouter,
   Moonshot, Zhipu, or a local Ollama) or fill in any OpenAI-compatible
   **Base URL** and **Model** yourself.
3. Enter your **API key** and click **Save Settings**.

The base URL and model are stored in `chat_config.json` inside ArXistant's
data directory; the API key is stored in the operating-system keychain, never
in a plain file. The status line under the settings tells you when the Chat
page is ready.

### Read and ask

Choose a paper using the picker in the middle of the page, which covers your
saved library plus the current daily and recent ranked lists. You can also
jump straight into a chat from the 💬 buttons on the Daily, Recent, and Saved
Papers pages.

Once a paper is selected, the picker is replaced by the reader. A one-line info
bar (save button, arXiv number, and arXiv / SciX links) sits above the
conversation on the right; the paper itself fills the middle. The selectable
**Text** view is the default; use the **PDF / Text** toggle to switch to the PDF,
which is downloaded only when you first open it. The PDF is cached in the
`pdf/` folder and the full text (from arXiv's HTML) in `fulltext/`, so both
reopen instantly.
While a first download runs, a status chip counts the elapsed seconds and
offers a **read on arXiv** link; if a download fails or times out, the chip
links to the PDF on arXiv instead. Closing the paper (×) returns you to the
picker.

In **Text** view you can select any passage and click **💬 Ask about this** to
attach it as quoted context for your next question. Answers are grounded in the
paper's full text; when the assistant cites a passage it returns exact quotes,
which ArXistant highlights in the Text view so you can see where the answer
comes from.

The LLM settings live in the left panel, which starts hidden, and the
conversation in the right one. Red arrow handles at the panel edges toggle each
panel open and closed — hover over one to see whether it controls **Settings**
or **Chat** — and the boundary between the paper and the chat can be dragged to
resize them. Panel state and the chat width are remembered in the browser.

Quick prompt chips offer good first questions, such as a five-bullet summary or
the main results and caveats.

Conversations are kept for the current page session only; **New Chat** clears
the history. Nothing is sent anywhere except the LLM provider you configure —
questions and paper metadata go to that API. Choose a local endpoint (for
example Ollama) if you want the whole exchange to stay on your machine.

## Reminders and automatic refresh

The extension can schedule multiple reminders each day. By default it uses
10:30 and skips Saturday and Sunday.

At the first configured reminder, the extension asks the local server to
refresh the daily list before displaying the notification. If Chrome was closed
or the computer asleep, the next reminder catches up. Once a refresh succeeds,
later reminders that day only notify. A failed refresh remains eligible to
retry at a later reminder.

The settings page shows the exact next occurrence of every Chrome alarm. Use
**Test Notification** to check Chrome and operating-system permissions.

## Saved papers

The Saved Papers page provides full-text-style filtering across locally stored
metadata. You can edit notes and remove records. The database is SQLite and
never needs a hosted ArXistant account.

## Cloud sync

ArXistant can mirror your paper database (saved papers, publications, and custom
keywords) to Nutstore over WebDAV, so several devices share the same library.
It stays local-first: every device keeps its own SQLite database, and the cloud
only carries a mergeable JSON snapshot between devices.

### Set up Nutstore WebDAV

1. In Nutstore (坚果云), open **账户信息 → 安全选项 → 第三方应用管理 →
   添加应用密码** and generate a dedicated **app password**. Do not use your
   normal Nutstore login password.
2. In **Extension Settings → Cloud Sync**, choose **Nutstore WebDAV (坚果云)**.
3. Leave the address as `https://dav.jianguoyun.com/dav/`, enter your Nutstore
   email, and paste the app password.
4. Click **Connect**. This saves your settings, enables cloud sync, and runs a
   first sync immediately.

Once enabled, ArXistant also syncs automatically in the background on server
start and a few seconds after you save or remove a paper. Use **Disconnect** to
turn sync off and remove the stored app password.

On the first sync, ArXistant creates an `ArXistant` folder in Nutstore and
uploads the snapshot. On a second device, repeat the same steps: the first sync
there downloads and restores your library instead.

The app password is stored in the operating-system keychain, never in an
ArXistant file. Use **Disconnect** to remove it and turn sync off.

### What is and is not synced

Synced and merged (last-write-wins): saved papers with notes, publications, and
custom keywords. Not synced: the ML model and generated daily/recent pages,
which each device rebuilds from its local database.

The **Local folder** provider is an alternative that writes the snapshot into a
folder you already sync with Dropbox/iCloud/OneDrive or the Nutstore desktop app.

## Search

The Search page supports:

- **arXiv search**, which does not require an ADS token.
- **ADS search**, which includes ADS metadata and requires a token.

Search results can be saved directly into the same local database as daily
recommendations.

## Publications from SciX/ADS

1. Add an ADS API token as described in the installation guide.
2. Open **My Publications**.
3. Paste a SciX library URL such as
   `https://scixplorer.org/user/libraries/...`.
4. Click **Fetch**, review the results, and choose **Add**.

ArXistant detects duplicates using bibcode, normalized title, and arXiv ID.
Individual publications can be removed manually.

## Model training

Automatic retraining occurs after five effective saved-paper changes by
default. Change the threshold from 1 to 100 in **Extension Settings → ML
Retraining**.

Training runs in the background. Changes made during a training run remain
counted toward the next run. A successful run updates the ML Features page; a
failure preserves the accumulated count and displays an error.

Use **Train Model Now** on the ML Features page to start training immediately.

## Positive and negative keywords

The ML Features page shows stable learned features and lets you define manual
keywords:

- Positive keywords raise the log-odds of matching papers.
- Negative keywords lower the log-odds of matching papers.
- Each match changes log-odds by 0.75.
- At most three manual matches in each direction apply to one paper.

Custom keywords take display priority. Stable learned features fill the
remaining feature slots. Similar singular/plural variants and shorter
components of displayed phrases are collapsed in the inspector without
changing the underlying model score.

## Manual refresh and command-line use

Generate today's page:

```bash
python3 src/arxiv_daily_ranker_html.py \
  --output local/arxiv_ranked_personalized.html
```

Generate the recent page:

```bash
python3 src/arxiv_daily_ranker_html.py --recent \
  --output local/arxiv_recent_personalized.html
```

Train the model manually:

```bash
python3 src/arxiv_ml_ranker.py train
```

For a packaged Linux installation, the browser controls are preferred because
the installed service supplies the correct external data directory.
