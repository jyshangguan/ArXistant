<p align="center">
  <img src="docs/logo0.5.jpg" width="200" alt="ArXistant logo">
</p>

<h1 align="center">ArXistant</h1>

<p align="center">
  <strong>A private, personalized assistant for keeping up with arXiv astronomy papers.</strong>
</p>

<p align="center">
  <a href="https://jyshangguan.github.io/ArXistant/"><strong>Read the documentation →</strong></a>
</p>

ArXistant fetches new `astro-ph` submissions, learns from the papers you save,
and presents a ranked daily reading list in Chrome. The database, model, and
personal preferences remain on your computer.

## Highlights

- **Personalized daily ranking** — scores new and recent astronomy papers using
  a local TF-IDF and logistic-regression model.
- **Learns from your library** — saved papers improve future recommendations;
  editable positive and negative keywords provide direct control.
- **Focused reading interface** — browse ranked papers (arXiv ID before each
  title, titles linking to AlphaXiv) with collapsible abstracts and one-click
  save, tag, and chat actions.
- **Paper reading helper** — open one or more papers as tabs and chat with an
  LLM grounded in their full text: streamed answers with highlighted evidence
  quotes, your own highlights and notes, local PDF drag-and-drop, and an
  assistant that can search your library, Semantic Scholar, the citation
  graph, and the web.
- **Voice digest of the daily list** — a 🔊 Listen button on the Daily and
  Recent pages reads the top-ranked papers aloud: each paper is announced
  ("Paper N, title, by first author") and then its LLM-written digest is
  read, batch by batch, with a choice of man / woman / system-default voice,
  batch size, and rate.
- **Tags for saved papers** — organize your library with tags, filter the
  Saved Papers page by them (the tag list is folded away until you need it),
  and tag papers straight from the Daily, Recent, and Search pages or from the
  browsing panel.
- **Local paper database** — search saved papers with plain keywords or scoped
  `field:value` terms (`tag:`, `author:`, `title:`, `abs:`, `note:`, `year:`
  with ranges, `id:`), maintain notes, and remove records through a browser
  interface.
- **ADS / SciX search** — find papers (fielded queries like
  `first_author:Greene author:Ho year:2005`) and save, tag, or open them in
  Chat without leaving ArXistant, with automatic retries when a source is slow
  or rate-limited.
- **Save papers while you browse** — a small ArXistant panel on
  [scixplorer.org](https://scixplorer.org/) and
  [arxiv.org](https://arxiv.org/) paper pages adds the paper to your library
  and opens it in Chat. Adding a tag saves the paper in the same step, so
  tagging is never lost. Journal-only papers are stored by bibcode with the
  same notes/tags/highlights, the arXiv side needs no ADS token at all, and
  each site can be switched off independently in Settings.
- **Publication management** — import your publications from a SciX/ADS library
  with duplicate detection.
- **Chrome reminders** — choose multiple reminder times, skip weekends, and
  automatically refresh the daily list once per day.
- **Two settings surfaces** — the extension's folded-sections Settings page for
  desktop-only choices (server, paper panel, reminders, retraining, cloud sync,
  ADS / SciX token, LLM, voice reading, debug), and a server-rendered
  `/settings.html` for the three that also matter on a phone (LLM, voice
  reading, cloud sync), which is what the Android app uses.
- **Local-first operation** — a lightweight server runs on `localhost`; there is
  no hosted ArXistant account or remote personal database.
- **Optional cloud sync** — mirror your paper database to Nutstore (坚果云) over
  WebDAV and share it across devices.

## Documentation

Browse the complete documentation at
**[jyshangguan.github.io/ArXistant](https://jyshangguan.github.io/ArXistant/)**.

- [Installation](docs/installation.md) — macOS, Debian/Ubuntu, Windows/manual
  setup, Android, migration, updates, and removal.
- [User guide](docs/user-guide.md) — daily workflow, reminders, saved papers
  and tags, chat reading helper, search, publications, and ML controls.
- [How ArXistant works](docs/how-it-works.md) — architecture, ranking pipeline,
  local data, background refresh, and project structure.
- [Android app](docs/android.md) — run ArXistant standalone on a phone (arXiv
  fetch, ML ranking, and Nutstore sync, no server). Download the signed APK from
  the [latest release](https://github.com/jyshangguan/ArXistant/releases/latest);
  the app can also check for and install updates itself.

## At a glance

```mermaid
flowchart LR
    A["arXiv / ADS"] --> B["Local ArXistant server"]
    B --> C["Local ranking model"]
    C --> D["Ranked pages in Chrome"]
    D --> E["Saved papers"]
    E --> C
```

ArXistant is currently at version **0.4.5**. The Chrome extension is loaded as
an unpacked extension. macOS has a bundled launcher, and Debian/Ubuntu has an
experimental `.deb` package with a systemd user service. A standalone **Android
app (v0.4.5)** runs the full pipeline on the phone via Chaquopy — including
the 🔊 voice digest, spoken by the phone's own text-to-speech engine.

## License

MIT
