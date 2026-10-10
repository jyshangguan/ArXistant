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
| Saved Papers | `/database.html` | Search, annotate, tag, and remove saved papers |
| Notebook | `/notebook.html` | Reading-list folders — notes that link to papers you want to read |
| Chat | `/chat.html` | Read papers (tabs, highlights) and ask an LLM about them |
| My Publications | `/publications.html` | Import and manage your publication list |
| Search | `/search.html` | Find papers via ADS / SciX; save, tag, or open them in Chat |
| ML Features | `/ml-features.html` | Inspect training state and ranking features |
| Settings | `/settings.html` | LLM, voice reading, and cloud sync — works without the extension, so the Android app uses it |
| Cloud Sync | `/cloud-sync.html` | The standalone cloud-sync page (the same form is embedded in Settings) |

All addresses are served from `http://localhost:8765`.

Two settings surfaces exist, and the split is deliberate. The extension's own
**Settings** page (right-click the toolbar icon → Options) holds the
desktop-only settings — server URL, Paper Panel, reminders, retraining, the
ADS / SciX token, and debugging. The server-rendered `/settings.html` holds the
three that also matter on a phone: LLM, voice reading, and cloud sync. Android
has no extension, so it reaches the latter through the **⋯ → Settings** menu.

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

### Listen to the daily digest

The **🔊 Listen** pill on the Daily and Recent pages reads the list aloud.
Click it and ArXistant summarizes the top-ranked papers — title, first
author, and abstract condensed into a short spoken paragraph each — and plays
them one after another with the browser's built-in speech synthesis. Each
paper is announced first — "Paper 12. *Title*. By *first author* and
colleagues." — then a short pause lets you register that a new paper has
started, and another brief stop follows the last sentence of each paper. The
paper currently being read is highlighted and scrolled into view, and the
panel shows progress plus a transcript you can expand (**📜 Transcript**).
**⏸ Pause**, **⏭ Skip paper**, and **⏹ Stop** control the reading.

When the batch finishes, the panel asks whether to **Continue** with the next
papers of the list, so a long reading list can be consumed in batches; at
the end of the list it reports that all papers have been read. On the
Android app the Listen button works the same way through the phone's own
speech engine, and the panel itself shows the voice / rate / papers
settings (there is no extension settings page there); a browser or device
without any voice engine shows the digest as text instead.

The digests are written by the same LLM as the Chat page (see
[Configure an LLM](#configure-an-llm)). Without a configured LLM the title
and abstract are read directly. Summaries are cached per paper, so listening
a second time starts immediately.

The extension's Settings page has a **Voice Reading (Listen)** section to
choose the voice — the **system default**, a **man's**, or a **woman's**
voice, previewable with **Test Voice** — how many papers each reading
covers, and the speaking rate. American English is preferred: the woman's
voice resolves to Google's US English voice when Chrome offers it, and the
man's to a US system voice such as Alex or David. Voice and rate changes
save immediately; papers per reading saves with the **Save** button. The
settings live on the local server (`tts_config.json`) so every browser
reading the same server shares them, and each device resolves the chosen
voice from its own installed voices.

## The ⚙️ Settings page

Every installation also has a server-rendered **⚙️ Settings** page (the
**⋯ → Settings** menu entry, or `/settings.html` directly) with three
folded sections: the **LLM** used by Chat and the voice digests (base URL,
model, API key, provider presets, and a connection test — exactly what the
extension's LLM section configures), **Voice Reading** (voice, rate,
papers per reading), and **Cloud Sync** (the same configuration as the
[Cloud Sync](#cloud-sync) page). It is the settings surface for the
Android app — which has no Chrome extension — and a convenient fallback
in any browser: everything it saves lives on the device's own server, so
the same settings apply to every client of that server.

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

### Review and revise a manuscript

Open an uploaded PDF in Chat, then click **Manuscript** beside the Conversation
heading. Choose **New manuscript → Open workspace**, or link the PDF to an
existing workspace as a new draft. Manuscript conversations, preferences,
proposed wording, and comments are stored locally and survive page reloads.
You can also open **Manuscript** first and drop a PDF directly into its upload
area, or use **Choose PDF**; ArXistant imports and opens it automatically.

- **Review entire paper** runs one consolidated review of all pages, with a
  prioritized summary and comments anchored to passages or pages. Review options
  select language, citations, and figures/tables. ArXistant first reviews
  bounded text chunks of up to two pages, then runs a separate visual pass only
  for pages with detected figure or table captions. It saves a checkpoint after
  every chunk. Each text request includes only nearby text and cited reference
  entries, and bibliographic searches no longer require an extra model call.
  Use **Cancel** or **Resume** without repeating completed chunks.
- For focused revision, select text in the reader and click **Revise**, or enter
  a scope such as `2-4, 7`, `section 3.1`, or `figure 2`. Ask for proofreading,
  clearer wording, alternative structures, citation support, or figure feedback.
  Follow-up messages retain the target and earlier proposals. A named section
  or figure in a message can also select that target.
- **Use this proposal** records your chosen wording. **Copy wording** lets you
  apply it in your manuscript editor. Proposals are working text: they do not
  rewrite the PDF body. **Add to paper comments** creates an exportable comment.
- Click a comment's **Page** button to locate it, or **Discuss** to continue the
  conversation about it. Existing native PDF comments are included. Comments
  can be edited, resolved, or dismissed; edits retain their history. Explicitly
  asking to update the active comment can update that comment without duplicating it.
- **Download PDF** exports open ArXistant comments while preserving the original
  PDF and its native annotations. Check individual comments and choose **Export
  selected** for a narrower export. An ambiguous text match becomes a labeled
  page note rather than a guessed highlight. Resolving an imported comment does
  not erase its original annotation from the source PDF.
- After recompiling your manuscript, upload the new PDF and link it to the same
  workspace. **Check earlier comments** suggests whether issues were addressed;
  it does not automatically resolve them. **Carry comment** is available for a
  unique matching passage in the new version. Earlier comments keep their old
  PDF locations unless explicitly carried forward.

The workspace uses your configured LLM. **Inspect page images** requires image
input support; unsupported requests fall back to text checks with visible
coverage limitations. **Look up cited sources** uses ADS when a token is
configured, otherwise Semantic Scholar, and attempts arXiv full text for one
candidate per query. Queries and retrieved excerpts are bounded: a review does
not certify every citation. Bibliographic correctness and support for a claim
are separate checks; missing evidence remains unverified. No journal-specific
submission compliance is certified from the PDF alone.

The desktop/server installation requires PyMuPDF (included in the Python
requirements). Scanned PDFs need OCR before upload. The standalone Android build
currently omits Chat, including this workspace. Manuscript files and workspace
records are excluded from cloud-sync snapshots; model requests still go to the
provider configured in Settings. Removing a local PDF also removes its workspace
records for that version, after any active review has stopped.

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

## Notebook

The Notebook page (`/notebook.html`, also in the extension popup and the **⋯**
menu) collects the papers you want to read into folders. It starts with a
ready-made **To read** folder for papers to get to soon.

A folder holds *notes that link to papers* — not copies of them. Each entry
shows the paper title (linking to AlphaXiv, or to SciX for bibcode-keyed
papers), the arXiv ID, a **💬 Chat** shortcut, an optional note of your own,
and the date you added it. The paper itself stays wherever it already lives
(your library, the daily list, arXiv).

- **Add a paper** — click **➕ Add paper** and either search your library
  (saved, daily, and recent papers) or paste an arXiv ID, an arxiv.org /
  AlphaXiv link, or any paper URL. The optional note field attaches a reason
  ("read before the proposal deadline") to the entry you are adding. A paper
  can sit in several folders at once, but never twice in the same one —
  re-adding it there just refreshes its note.
- **Add from the Daily / Recent / Search pages** — each paper there has a
  **📓** button
  (right after the 🏷️ tag button). Clicking it opens a small box below the
  paper listing your folders; click **Add** next to one, or type a new folder
  name and **Create & add**. The button turns green once that paper is in your
  Notebook.
- **Add while browsing** — the same **📓 Notebook** button in the extension's
  page panel on [scixplorer.org](https://scixplorer.org/) and
  [arxiv.org](https://arxiv.org/) opens the same picker, so a paper can be
  filed while you are still reading its abstract page.
- **Edit a note** — click the note text on any entry, then Save (⌘/Ctrl+Enter
  works too). **✕** removes the entry from the folder.
- **Manage folders** — **＋ New** creates a folder; hover a folder and use ✏️
  to rename it (Enter saves, Esc cancels) or 🗑 to delete it together with its
  notes. Folder names are unique. The seeded **To read** folder is an ordinary
  folder: rename or delete it like any other, and a deleted folder never comes
  back on restart.

Notes added by bare arXiv ID keep showing the ID until the paper is saved to
your library — once it is, the entry picks up the real title and authors
automatically. Filing a paper never adds it to your library: the Notebook is a
reading list, not a preference signal, so notebook-only papers do not affect
the ML ranking of your daily feed. Your folders and notes travel with the rest
of your database through [cloud sync](#cloud-sync), including to the Android
app — while uploaded local PDFs stay on the device that imported them.

## Cloud sync

ArXistant can mirror your paper database (saved papers, publications, custom
keywords, and the Notebook's folders and notes) to Nutstore over WebDAV, so
several devices share the same library. It stays local-first: every device
keeps its own SQLite database, and the cloud only carries a mergeable JSON
snapshot between devices.

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
start and a few seconds after you save or remove a paper or change the
Notebook. Use **Disconnect** to turn sync off and remove the stored app
password.

On the first sync, ArXistant creates an `ArXistant` folder in Nutstore and
uploads the snapshot. On a second device, repeat the same steps: the first sync
there downloads and restores your library instead.

The app password is stored in the operating-system keychain, never in an
ArXistant file. Use **Disconnect** to remove it and turn sync off.

### What is and is not synced

Synced and merged (last-write-wins): saved papers with notes, tags, and
highlights, publications, custom keywords, and the Notebook's folders and
notes. Not synced: uploaded local PDFs and manuscript workspaces, the ML
model, and generated daily/recent pages, which each device keeps or rebuilds
locally.

The **Local folder** provider is an alternative that writes the snapshot into a
folder you already sync with Dropbox/iCloud/OneDrive or the Nutstore desktop app.

## Search

The Search page queries **ADS / SciX**, which indexes arXiv papers *and*
journal-only records, and adds metadata (year, citation count, bibcode, DOI).
It requires an
[ADS token](https://ui.adsabs.harvard.edu/user/settings/token) — set it from
the extension's **Settings → ADS / SciX** section (see
[Installation]({{ '/installation/' | relative_url }}#ads-api-token)). Without a token, the page
says so before you search.

Results use the same card layout as the Daily page: the arXiv ID sits before
the title, the ID links to arXiv, and the title links to AlphaXiv — or, for
ADS-only records without an arXiv version, the bibcode and title link to the
paper's page on [scixplorer.org](https://scixplorer.org/). Year and
citation-count badges plus **DOI** / **ADS** links summarize each record, and
every card carries the same action buttons as the daily list:

- **💾** saves the paper into the local database (click again to remove it),
- **💬** opens the paper directly in the Chat reader,
- **🏷️** tags it (the paper is saved first if needed),
- **📓** adds it to a Notebook folder (see [Notebook](#notebook)).

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
[SciXplorer papers](#papers-from-scixplorerorg-and-arxivorg)). Only records with neither
an arXiv ID nor a bibcode, which are rare, cannot be saved.

Requests to ADS are retried automatically when the source is slow or
rate-limits the query, with the provider's `Retry-After` hint respected; if
the source stays unavailable, the page shows a clear error message instead of
a silent failure. A floating **▲** button at the bottom right returns you to
the top of a long result list.

### Search syntax

Plain keywords search every field; a space between terms means AND. A term
can be scoped to one category with `field:value` — the **Search syntax** card
under the search box lists the same fields:

| Example | Meaning |
|---|---|
| `first_author:Greene` | first (lead) author |
| `author:Ho` | any author |
| `title:quasar` | title |
| `abs:"AGN feedback"` | abstract phrase |
| `year:2005` | year (or `year:2005-2010`, `year:[2005 TO 2010]`) |
| `arXiv:1802.08364` | paper by its arXiv ID |
| `bibcode:2018ApJ...854..158S` | paper by its bibcode |
| `property:refereed` | only refereed papers |
| `first_author:Greene author:Ho year:2005` | all must match |

Single-word values need no quotes; quote any value that contains spaces. The
Saved-Papers-style `id:` token is remapped to ADS's `identifier:` field
automatically; `tag:` and `note:` have no ADS equivalent (they filter your
local library only).

## Papers from scixplorer.org and arxiv.org

While you browse [scixplorer.org](https://scixplorer.org/) or
[arxiv.org](https://arxiv.org/), the ArXistant extension shows the same small
panel in the bottom-right corner. It deliberately does not repeat the abstract
— the page you are on already shows it — and offers only what that page cannot:

- **🏷️ Tags** — type a tag and press Enter (or click **Add**). Existing tags
  appear as chips and each has a **✕**. Tags you already use elsewhere in your
  library are offered as suggestions while you type. After a tag is added the
  box is cleared and **keeps focus**, so you can type the next one straight away
  without clicking back into it.
  - On a paper that is **not saved yet**, adding a tag **saves the paper** in
    the same write — tagging is taken as "keep this paper", so there is no
    separate Save click. The input reads `add a tag (auto-saves)` to say so, the
    button flips to **✓ Saved**, and the status line confirms it. If the save
    fails, the tag is not applied and the reason is shown.
  - On a paper that is **already saved**, each change persists immediately,
    exactly like the tag editors on the ArXistant pages. Removing a tag never
    removes the paper.
- **💾 Save / ✓ Saved** — add the paper to your library, or remove it. The
  unified key rule applies, so a paper saved from a browsing page and the same
  paper saved from the Daily page are one record, never a duplicate.
- **💬 Chat** — opens the paper in the Chat reader on your local server.
  Journal-only papers get an abstract-only reader you can highlight and
  annotate; papers with an arXiv version load the full text as usual.
- **📓 Notebook / ✓ Notebook** — files the paper into a reading-list folder
  without leaving the page (see [Notebook](#notebook)). Clicking it expands a
  picker below the buttons: one **Add** per existing folder — papers already
  filed show **✓ Added** — plus a **New folder name… / Create & add** row for
  a folder that does not exist yet. Typing a name that already exists (even in
  different case) reuses that folder. The button turns green once the paper is
  in any folder. Filing a paper does not save it first: the folder row stores
  the paper's identifier, title, authors, and the page URL on its own, so it
  works for papers you do not intend to keep in your library. Unlike saving,
  filing is not a preference signal — notebook-only papers never enter the ML
  ranker's training data, never bump its retraining counter, and are not part
  of cloud sync.
- **–** in the header collapses the panel out of the way.

**On scixplorer.org** the panel appears on paper pages — `/abs/<bibcode>` and
its sub-pages (`/abstract`, `/citations`, `/references`, `/metrics`,
`/graphics`). It reads the bibcode from the page URL and resolves the record
through your local server, so it works even though scixplorer.org itself
cannot be fetched by the server. This path needs the **ADS / SciX token**: set
it once from the extension's **Settings → ADS / SciX** section by pasting the
token from
[NASA ADS API settings](https://ui.adsabs.harvard.edu/user/settings/token) and
clicking **Save Token** (it is verified immediately), then reload any open
scixplorer.org tabs. The token is stored by your local server with owner-only
file permissions.

**On arxiv.org** the panel appears on abstract pages (`/abs/<arxiv-id>`). It
reads the paper's `citation_*` metadata straight off the page, so it needs
**no ADS token and makes no arXiv API call** — which also keeps it clear of
arXiv's rate limits. Version suffixes are dropped from the identifier, so
`/abs/1802.08364v1` saves under `1802.08364`, the same key the Daily page
uses.

If the panel does not appear: check **Settings → Paper Panel**, where each site
has its own on/off checkbox; confirm you are on a paper page; check that the
server is running (the extension popup shows its status); and confirm the
extension was reloaded after gaining these site permissions — Chrome asks you
to re-approve host permissions when an unpacked extension is reloaded, and its
**Site access** must not be restricted to specific sites. Opening DevTools on
the page shows `[ArXistant …]` console lines reporting whether the script
loaded, whether the panel is disabled, which identifier it read from the URL,
and whether the panel was attached.

### Turning the panel off

**Settings → Paper Panel** has one checkbox per site — arxiv.org abstract pages
and scixplorer.org paper pages. Both are on by default, and an upgrade from a
version without this setting leaves them on. Click **Save Settings**, then
reload the paper tab: the choice is read when the page loads. Turning a site
off stops the panel entirely — it makes no requests and reads nothing from the
page.

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
