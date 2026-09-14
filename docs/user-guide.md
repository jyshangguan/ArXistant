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
4. Open **Settings** to choose reminder times, retraining behavior, cloud
   sync, and the LLM for the Chat page. Sections start folded — click a
   section title to show its content.

The server health endpoint is
[http://localhost:8765/api/health](http://localhost:8765/api/health).

## Main pages

| Page | Address | Purpose |
|---|---|---|
| Daily Papers | `/daily.html` | Today's ranked submissions |
| Recent Papers | `/recent.html` | Approximately five days of ranked papers |
| Saved Papers | `/database.html` | Search, annotate, and remove saved papers |
| Chat | `/chat.html` | Read papers (tabs, highlights) and ask an LLM about them |
| My Publications | `/publications.html` | Import and manage your publication list |
| Search arXiv/ADS | `/search-arxiv.html` | Find papers; save, tag, or open them in Chat |
| ML Features | `/ml-features.html` | Inspect training state and ranking features |

All addresses are served from `http://localhost:8765`.

## Daily reading workflow

Every paper row shows its arXiv ID before the title — the ID links to the
arXiv abstract, the title links to AlphaXiv's discussion page — and the
disclosure control expands the abstract. The relevance score reflects the
current local model and any matching custom keywords.

Page navigation lives in the **…** menu at the top right; the **🔄** pill next
to it syncs your library and re-fetches the list (desktop asks for
confirmation first), and on touch devices pulling down at the top of the page
refreshes as well. On pages other than Daily/Recent Papers the menu offers
**Daily Papers**.

Click **💾** to save a useful paper; the button turns into a green **✓** and
clicking it again removes the paper. Saved papers become positive examples for
future model training, and removing one updates the local training state.

Click **🏷️** (next to the 💬 chat button) to organize a paper with tags. If the
paper is not saved yet, ArXistant saves it first and then opens a small tag
editor: type a tag and press Enter (or **Add**) to attach it, **✕** to remove
one. Changes save automatically as you make them, and the editor closes when
you click anywhere else. Tags are stored with the saved paper and sync with
your library.

## Chat: the paper reading helper

The Chat page lets you read papers with an LLM: open one or more papers as
tabs, then ask questions about them (individually or together) and get
grounded, streamed answers.

### Configure an LLM

1. Open the extension's **Settings** page (popup → **Settings**, or
   right-click the toolbar icon → **Options**).
2. Expand the **LLM (Chat)** section and pick a provider preset (OpenAI,
   DeepSeek, OpenRouter, Moonshot, Zhipu, or a local Ollama) or fill in any
   OpenAI-compatible **Base URL** and **Model** yourself.
3. Enter your **API key** and click **Save LLM Settings** — the connection is
   tested immediately, so a missing or stale key is caught right away.

The base URL and model are stored in `chat_config.json` inside ArXistant's
data directory. The API key is kept in that same file with owner-only
permissions (chmod 600) — reliable even when the server runs as a background
daemon — with a best-effort copy in the operating-system keychain. The status
line in the section tells you when the Chat page is ready and where the key is
being read from; the Chat page itself shows a one-line LLM status under its
chat input (hover it for setup guidance).

### Read and ask

Choose a paper using the picker in the middle of the page, which covers your
saved library plus the current daily and recent ranked lists. You can also
jump straight into a chat from the 💬 buttons on the Daily, Recent, Saved
Papers, and Search pages.

Once a paper is selected, the picker is replaced by the reader. Each open paper
is a **tab** at the top; use **+** to add more tabs and **×** on a tab to close
it, and click a tab to make it the active paper. The reader shows the active
paper's selectable **Text** (the PDF option was removed for a simpler,
text-first reading experience). The chat is grounded in *all* open papers
(abstracts) plus the active paper's full text, and references them as [1],
[2], … when comparing. The full text (from arXiv's HTML) is cached in
`fulltext/` so it reopens instantly. Closing the last tab returns you to the
picker.

When no paper is open, you can drag a PDF directly into the **Conversation**
box. ArXistant stores it locally, extracts selectable text page by page, splits
the text into searchable chunks, and opens it in the same reader. Its
Text view uses a bundled PDF.js renderer, so the printed layout—including
headings, columns, equations, tables, and figures—is retained while a selectable
text layer remains available for highlights and annotations.
In the Text view, hover near the top of the paper to reveal a zoom bar
(− / + / reset) that appears only while your cursor is in that region and
fades a moment after you leave.
Local PDFs support highlights, colors, annotation notes, and question-aware
chunk retrieval. Scanned PDFs are not OCRed and report a clear error when no
selectable text is available. The PDF files remain local and are not included
in cloud sync.

In **Text** view you can select any passage and click **💬 Ask about this** to
attach it as quoted context for your next question. Answers are grounded in the
paper's full text; when the assistant cites a passage it returns exact quotes,
which ArXistant highlights in the Text view (matching is tolerant of case and
small differences) so you can see where the answer comes from.

Selecting text also offers **🖍 Highlight**: your own highlights are stored
locally with the paper (the paper is saved first if needed) and reappear every
time you reopen it. Clicking a highlight opens a small popup where you can
attach a note, choose its color, or remove the highlight. Pressing **Esc** saves
and closes the popup. Selection is smooth — the action bubble only appears once
you finish selecting. The **📝 Annotations** box above the conversation lists
every highlighted passage and its note; click a passage to jump to it, or edit
the note directly in the list. An underline marks highlights that have notes.

The assistant can also use a **web search** tool (keyless DuckDuckGo) for
up-to-date or general information beyond the paper; while it searches, the chat
shows a "🔎 Searching the web…" note and the answer cites the URLs it used.

### Finding more papers, right in the chat

The chat assistant has paper-search tools it can call on its own, so you can
simply ask — with or without a paper selected:

- "Find papers on X" → **search_papers** (Semantic Scholar, with citation
  counts and TLDRs).
- "Search my saved papers for X" → **search_library** (offline TF-IDF over your
  saved library's titles/abstracts/notes).
- "More papers like this one" → **find_related** (S2 recommendations, falling
  back to TF-IDF similarity over your saved/daily library).
- "What cites / does this paper reference" → **citation_graph** (ADS).
- Anything general or up-to-date → **web_search** (keyless DuckDuckGo).

When the assistant runs a search, the matching papers appear in the chat as a
"📚 N papers found" list; each entry offers **Read** (opens it in the reader)
and an arXiv link. Semantic Scholar is keyless and rate-limited — if it errors,
the assistant falls back to your local library or ADS (which uses your
configured ADS token).

For complex questions the assistant first breaks the request into
sub-questions, resolves each with the right tool or the paper, then synthesizes
a single structured answer with consistent sections: **Answer**, **Evidence**
(exact `QUOTE:` lines that get highlighted), **Related papers**, and **Sources
& caveats**.

The page is the reader plus the conversation panel: a red arrow handle at the
right edge toggles the chat panel open and closed, and the boundary between
the paper and the chat can be dragged to resize them. Panel state and the chat
width are remembered in the browser. A one-line LLM status sits under the chat
input — hover the cursor over it (or tap it on a touch screen) to open a
floating box explaining where and how to configure the LLM.

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

Paper rows match the Daily page: the arXiv ID sits before the title, the ID
links to arXiv, and the title links to AlphaXiv. Every paper card shows its
tags, and **🏷️ Edit tags** opens the same tag editor as the Daily/Recent pages.
The **Filter by tags** bar above the list shows every tag in your library with
a count; click one or more tags to show only papers that carry *all* selected
tags. Tag filtering combines with the text search box.

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

Synced and merged (last-write-wins): saved papers with notes, tags, and
highlights, publications, and custom keywords. Not synced: the ML model and generated daily/recent pages,
which each device rebuilds from its local database.

The **Local folder** provider is an alternative that writes the snapshot into a
folder you already sync with Dropbox/iCloud/OneDrive or the Nutstore desktop app.

## Search

The Search page queries two sources:

- **arXiv search**, which does not require an ADS token.
- **ADS / SciX search**, which adds ADS metadata (year, citation count,
  bibcode, DOI) and requires a token.

Results use the same card layout as the Daily page: the arXiv ID sits before
the title, the ID links to arXiv, and the title links to AlphaXiv — or, for
ADS-only records without an arXiv ID, to the ADS abstract page or the DOI.
Year and citation-count badges plus **DOI** / **ADS** links summarize each
record, and every card carries the same action buttons as the daily list:

- **💾** saves the paper into the local database (click again to remove it),
- **💬** opens the paper directly in the Chat reader,
- **🏷️** tags it (the paper is saved first if needed).

Records without an arXiv ID — some ADS-only entries — are marked "No arXiv
ID — cannot save to DB": the database is keyed by arXiv ID, so those records
can be read but not saved, tagged, or opened in Chat.

Requests to arXiv and ADS are retried automatically when a source is slow or
rate-limits the query, with the provider's `Retry-After` hint respected; if
the source stays unavailable, the page shows a clear error message instead of
a silent failure. A floating **▲** button at the bottom right returns you to
the top of a long result list.

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
