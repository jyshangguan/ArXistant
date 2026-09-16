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
| Search | `/search.html` | Find papers via ADS / SciX; save, tag, or open them in Chat |
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

The **Filter by tags** bar is folded by default — the title shows how many
tags your library has, and clicking it shows every tag with a count. Click
one or more tags to show only papers that carry *all* selected tags; the
selected tags stay visible in the folded title so the active filter is always
obvious, and **Clear** resets it. Tag filtering combines with the text search
box.

### Search format

Plain keywords match the title, authors, abstract, notes, and paper ID. A
term can also be scoped to one category with `field:value`:

| Token | Matches |
|---|---|
| `tag:lrd` | papers tagged exactly `lrd` |
| `author:shangguan` | substring of the author list |
| `title:quasar` | substring of the title |
| `abs:feedback` | substring of the abstract |
| `note:followup` | substring of your notes |
| `year:2023` | publication year (also ranges: `year:2020-2024`) |
| `id:1802.08364` | substring of the paper ID (`arXiv:` is an alias) |

Multiple terms combine — every term must match, so
`tag:lrd author:shangguan year:2020-2024` lists papers that carry the tag
**and** the author **and** fall in the years. Values containing spaces can be
quoted (`title:"dark matter"`). The publication year is derived from the
arXiv ID or ADS bibcode, so it works for bibcode-keyed SciX papers too.

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

The Search page queries **ADS / SciX**, which indexes arXiv papers *and*
journal-only records, and adds metadata (year, citation count, bibcode, DOI).
It requires an
[ADS token](https://ui.adsabs.harvard.edu/user/settings/token) — set it from
the extension's **Settings → ADS / SciX** section (see
[Installation](installation.html#ads-api-token)). Without a token, the page
says so before you search.

Results use the same card layout as the Daily page: the arXiv ID sits before
the title, the ID links to arXiv, and the title links to AlphaXiv — or, for
ADS-only records without an arXiv version, the bibcode and title link to the
paper's page on [scixplorer.org](https://scixplorer.org/). Year and
citation-count badges plus **DOI** / **ADS** links summarize each record, and
every card carries the same action buttons as the daily list:

- **💾** saves the paper into the local database (click again to remove it),
- **💬** opens the paper directly in the Chat reader,
- **🏷️** tags it (the paper is saved first if needed).

Saving from the search page and saving from the Daily page produce the same
record: a paper with an arXiv version is always stored under its arXiv ID —
the same key the daily list uses — and journal-only papers are stored under
their ADS bibcode. Saving a paper you already saved from the daily list
updates that row instead of duplicating it, keeping its notes, tags, and
highlights. Only the two informational fields differ: `date_fetched` records
where the save came from (the list date on the Daily page, the search date
here), and `relevance_score` is 0 when saved from search (the daily-page
ranking score; the ML model trains on title and abstract only, so this does
not affect learning).

All of the save/tag/chat actions work for ADS-only records too (Chat grounds
those discussions in the abstract — see
[SciXplorer papers](#papers-from-scixplorerorg)). Only records with neither
an arXiv ID nor a bibcode, which are rare, cannot be saved.

Requests to ADS are retried automatically when the source is slow or
rate-limits the query, with the provider's `Retry-After` hint respected; if
the source stays unavailable, the page shows a clear error message instead of
a silent failure. A floating **▲** button at the bottom right returns you to
the top of a long result list.

### Search syntax

Plain keywords search every field; a space between terms means AND. A term
can be scoped to one category with `field:value` — the hint under the search
box shows the same syntax:

| Example | Meaning |
|---|---|
| `first_author:"Shangguan"` | first (lead) author |
| `author:"Shangguan"` | any author |
| `title:quasar` | title |
| `abs:"AGN feedback"` | abstract phrase |
| `year:2018` | year (or `year:2018-2020`, `year:[2018 TO 2020]`) |
| `arXiv:1802.08364` | paper by its arXiv ID |
| `bibcode:2018ApJ...854..158S` | paper by its bibcode |
| `property:refereed` | only refereed papers |
| `first_author:"Shangguan" year:2018 abs:"AGN feedback"` | all must match |

The Saved-Papers-style `id:` token is remapped to ADS's `identifier:` field
automatically; `tag:` and `note:` have no ADS equivalent (they filter your
local library only).

## Papers from scixplorer.org

While you browse [scixplorer.org](https://scixplorer.org/), the ArXistant
extension shows a small panel on paper pages (`/abs/<bibcode>`). The panel
reads the paper's bibcode from the page URL and resolves the record through
your local server, so it works even though scixplorer.org itself cannot be
accessed by the server.

The panel offers:

- **▸ Show abstract** — the paper's abstract, fetched once per paper.
- **💾 Save / ✓ Saved** — save the paper to (or remove it from) your library;
  the same unified key rule applies, so a paper saved from the Daily page
  and the same paper saved from scixplorer are one record, never a
  duplicate.
- **💬 Chat** — opens the paper in the Chat reader on your local server.
  Journal-only papers get an abstract-only reader you can highlight and
  annotate; papers with an arXiv version load the full text as usual.

The panel needs the **ADS / SciX token**. Set it once from the extension's
**Settings → ADS / SciX** section: paste the token from
[NASA ADS API settings](https://ui.adsabs.harvard.edu/user/settings/token),
click **Save Token** (it is verified immediately), and reload any open
scixplorer.org tabs. The token is stored by your local server with
owner-only file permissions.

If the panel does not appear: confirm you are on a paper page, check that the
server is running (the extension popup shows its status), and that the
extension was reloaded after gaining the new scixplorer.org permission —
Chrome asks you to re-approve host permissions when an unpacked extension is
reloaded.

## Publications from SciX/ADS

1. Add an ADS API token — either from the extension's **Settings → ADS /
   SciX** section or as described in the installation guide.
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
