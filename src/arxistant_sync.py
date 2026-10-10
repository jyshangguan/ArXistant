"""Cloud sync engine for the ArXistant paper database.

The paper database (saved papers + publications + custom keywords + the
Notebook's reading folders and notes) is exported to a versioned JSON
snapshot, merged with a remote snapshot using per-record last-write-wins
timestamps, and pushed to a storage provider.

Providers implement a small ``CloudProvider`` interface. Two providers ship
today: a local-folder provider (useful for a Dropbox/iCloud-synced folder and
as a test double) and a WebDAV provider (defaults to Nutstore / 坚果云).

All functions default to the application data directory but accept an explicit
``db_path`` so the merge logic can be unit-tested against a temporary database
without touching the real one.
"""

import json
import os
import sqlite3
import threading
import time
import uuid
from abc import ABC, abstractmethod
from datetime import datetime, timezone

from arxistant_paths import data_path

SYNC_FILENAME = "arxistant_sync.json"
SCHEMA_VERSION = 2
EPOCH = "1970-01-01T00:00:00+00:00"

# Non-id columns that participate in sync. The autoincrement ``id`` column is
# deliberately excluded because it is device-local and may collide across
# installs.
SAVED_COLUMNS = [
    "arxiv_id", "title", "authors", "abstract", "relevance_score",
    "date_fetched", "date_saved", "notes", "tags", "highlights",
    "updated_at",
]
PUB_COLUMNS = [
    "bibcode", "title", "authors", "abstract", "keywords", "year",
    "date_added", "updated_at",
]

# Notebook reading folders join the snapshot (issue #14). Folders sync by a
# stable ``sync_key`` so a rename keeps its identity; note keys are derived
# from (folder key, paper id) so the same paper filed on two devices merges
# into one note instead of duplicating. The separator cannot occur in folder
# names or paper ids (arXiv IDs, bibcodes, local- ids).
NOTEBOOK_KEY_SEP = "\x1f"
NOTEBOOK_FOLDER_COLUMNS = ["sync_key", "name", "created_at", "updated_at"]
NOTEBOOK_NOTE_COLUMNS = [
    "sync_key", "paper_id", "title", "authors", "url", "note",
    "created_at", "updated_at",
]


def new_sync_key():
    """A fresh device-local-unique folder sync key."""
    return uuid.uuid4().hex[:12]


def notebook_note_sync_key(folder_sync_key, paper_id):
    """Stable cross-device identity for a notebook note."""
    return f"{folder_sync_key}{NOTEBOOK_KEY_SEP}{paper_id}"

_SYNC_LOCK = threading.Lock()
_auto_sync_timer = None


# ---------------------------------------------------------------------------
# Paths and configuration
# ---------------------------------------------------------------------------

def get_db_path():
    return data_path("arxiv_papers.db")


def _data_dir_from(db_path):
    return os.path.dirname(os.path.abspath(db_path))


def _config_path_from(db_path):
    return os.path.join(_data_dir_from(db_path), "cloud", "config.json")


def _keyword_path(db_path, kind):
    return os.path.join(_data_dir_from(db_path), "ml_ranker", f"custom_{kind}.json")


def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _default_config():
    return {
        "provider": "local_folder",
        "enabled": False,
        "interval_minutes": 30,
        "device_id": uuid.uuid4().hex[:12],
        "last_sync_at": None,
        "last_error": None,
        "local_folder": {"path": ""},
        # Secrets (the WebDAV app password) live in the OS keychain via
        # arxistant_secrets, never in this file.
        "webdav": {"url": "", "username": ""},
    }


_SECRET_NESTED_KEYS = {
    "webdav": {"password"},
}


def _strip_secrets(config):
    """Return a copy of config with any secret keys removed."""
    clean = dict(config)
    for nested, keys in _SECRET_NESTED_KEYS.items():
        if isinstance(clean.get(nested), dict):
            clean[nested] = {k: v for k, v in clean[nested].items() if k not in keys}
    return clean


def load_config(db_path=None):
    db_path = db_path or get_db_path()
    path = _config_path_from(db_path)
    defaults = _default_config()
    if not os.path.exists(path):
        # Persist on first access so device_id and defaults are stable.
        save_config(defaults, db_path)
        return defaults
    try:
        with open(path, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("config root is not an object")
    except (OSError, json.JSONDecodeError, ValueError):
        # A corrupt config file must not crash request handlers; fall back to
        # defaults (the device_id changes, which only affects sync naming).
        data = dict(defaults)
    for key in defaults:
        data.setdefault(key, defaults[key])
    for nested in ("local_folder", "webdav"):
        data[nested] = {**defaults[nested], **(data.get(nested) or {})}
    # Ensure any legacy plaintext secrets are never exposed through config.
    data = _strip_secrets(data)
    return data


def save_config(config, db_path=None):
    db_path = db_path or get_db_path()
    path = _config_path_from(db_path)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(_strip_secrets(config), f, indent=2)
    os.replace(tmp, path)
    try:
        os.chmod(path, 0o600)
    except OSError:
        pass


# ---------------------------------------------------------------------------
# Schema migration
# ---------------------------------------------------------------------------

def migrate_db(conn):
    """Add sync columns/table to an existing database. Idempotent.

    Tolerates a database whose tables have not been created yet (the HTTP
    server's init_db creates them; standalone export/import on a fresh data
    directory may not have them).
    """
    cur = conn.cursor()

    cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
    existing_tables = {row[0] for row in cur.fetchall()}

    for table in ("saved_papers", "my_publications"):
        if table not in existing_tables:
            continue
        cur.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in cur.fetchall()}
        if "updated_at" not in columns:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN updated_at TEXT")

    # Tags and highlights on saved papers (issues #3 and #6). Older databases
    # get the columns lazily; pre-existing rows default to empty values.
    if "saved_papers" in existing_tables:
        cur.execute("PRAGMA table_info(saved_papers)")
        saved_columns = {row[1] for row in cur.fetchall()}
        if "tags" not in saved_columns:
            cur.execute("ALTER TABLE saved_papers ADD COLUMN tags TEXT DEFAULT ''")
        if "highlights" not in saved_columns:
            cur.execute("ALTER TABLE saved_papers ADD COLUMN highlights TEXT DEFAULT ''")
        cur.execute("UPDATE saved_papers SET tags = '' WHERE tags IS NULL")
        cur.execute("UPDATE saved_papers SET highlights = '' WHERE highlights IS NULL")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sync_tombstones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            record_key TEXT NOT NULL,
            deleted_at TEXT NOT NULL,
            UNIQUE(table_name, record_key)
        )
    """)

    # Notebook sync columns (issue #14). Existing folders get fresh keys and
    # the epoch timestamp so any real edit on any device wins over them;
    # existing notes get their derived key. Databases from before the
    # Notebook feature (or sync-only test databases) simply have no notebook
    # tables, which the export/import paths tolerate.
    for table in ("notebook_folders", "notebook_notes"):
        if table not in existing_tables:
            continue
        cur.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in cur.fetchall()}
        if "sync_key" not in columns:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN sync_key TEXT")
        if "updated_at" not in columns:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN updated_at TEXT")

    if "notebook_folders" in existing_tables:
        cur.execute("SELECT id FROM notebook_folders "
                    "WHERE sync_key IS NULL OR sync_key = ''")
        for (folder_id,) in cur.fetchall():
            cur.execute("UPDATE notebook_folders SET sync_key = ? WHERE id = ?",
                        (new_sync_key(), folder_id))
        cur.execute("UPDATE notebook_folders SET updated_at = ? "
                    "WHERE updated_at IS NULL OR updated_at = ''", (EPOCH,))

    if "notebook_notes" in existing_tables:
        cur.execute(
            "UPDATE notebook_notes SET sync_key = "
            "(SELECT f.sync_key FROM notebook_folders f "
            " WHERE f.id = notebook_notes.folder_id) || ? || paper_id "
            "WHERE sync_key IS NULL OR sync_key = ''",
            (NOTEBOOK_KEY_SEP,))
        cur.execute("UPDATE notebook_notes SET updated_at = ? "
                    "WHERE updated_at IS NULL OR updated_at = ''", (EPOCH,))

    # Unique indexes after the backfill, so the merge can key on sync_key.
    if "notebook_folders" in existing_tables:
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS "
                    "idx_notebook_folders_sync_key ON notebook_folders(sync_key)")
    if "notebook_notes" in existing_tables:
        cur.execute("CREATE UNIQUE INDEX IF NOT EXISTS "
                    "idx_notebook_notes_sync_key ON notebook_notes(sync_key)")

    # Rows created before sync existed get the epoch, so any real edit on any
    # device wins over them.
    if "saved_papers" in existing_tables:
        cur.execute(
            "UPDATE saved_papers SET updated_at = ? WHERE updated_at IS NULL OR updated_at = ''",
            (EPOCH,),
        )
    if "my_publications" in existing_tables:
        cur.execute(
            "UPDATE my_publications SET updated_at = ? WHERE updated_at IS NULL OR updated_at = ''",
            (EPOCH,),
        )
    conn.commit()


def add_tombstone(conn, table, key):
    if not key:
        return
    cur = conn.cursor()
    cur.execute(
        "INSERT OR REPLACE INTO sync_tombstones (table_name, record_key, deleted_at) "
        "VALUES (?, ?, ?)",
        (table, key, now_iso()),
    )


# ---------------------------------------------------------------------------
# Keywords
# ---------------------------------------------------------------------------

def _load_keywords(db_path, kind):
    path = _keyword_path(db_path, kind)
    if not os.path.exists(path):
        return []
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
    return [str(k).strip().lower() for k in data if str(k).strip()]


def _save_keywords(db_path, kind, keywords):
    path = _keyword_path(db_path, kind)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(keywords, f, indent=2)
    os.replace(tmp, path)


def merge_keywords(local_pos, local_neg, remote_pos, remote_neg):
    """Union merge; positive wins over negative. Deletions of individual
    keywords are not tombstoned in this version."""
    pos = sorted(set(local_pos) | set(remote_pos))
    neg = sorted((set(local_neg) | set(remote_neg)) - set(pos))
    return pos, neg


# ---------------------------------------------------------------------------
# Snapshot export / import / merge
# ---------------------------------------------------------------------------

def _connect(db_path):
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def _select_rows(cur, table, columns):
    cols = ", ".join(columns)
    cur.execute(f"SELECT {cols} FROM {table}")
    return [dict(r) for r in cur.fetchall()]


def _select_tombstones(cur):
    cur.execute("SELECT table_name, record_key, deleted_at FROM sync_tombstones")
    return [dict(r) for r in cur.fetchall()]


def _ts(value):
    return value or EPOCH


def _table_exists(conn, table):
    cur = conn.execute("SELECT 1 FROM sqlite_master "
                       "WHERE type='table' AND name=?", (table,))
    return cur.fetchone() is not None


def _notebook_note_rows(cur):
    """Note rows shaped like the snapshot: folder identified by its sync key.

    ``folder_id`` is device-local and would collide across installs, so the
    snapshot carries the folder's ``sync_key`` instead; the import resolves
    it back to a local folder id.
    """
    cur.execute(
        "SELECT n.sync_key, f.sync_key AS folder_key, n.paper_id, n.title, "
        "       n.authors, n.url, n.note, n.created_at, n.updated_at "
        "FROM notebook_notes n "
        "JOIN notebook_folders f ON f.id = n.folder_id "
        "WHERE n.sync_key IS NOT NULL AND n.sync_key != ''")
    return [dict(r) for r in cur.fetchall()]


def export_snapshot(db_path=None):
    db_path = db_path or get_db_path()
    conn = _connect(db_path)
    try:
        migrate_db(conn)
        cur = conn.cursor()
        saved = _select_rows(cur, "saved_papers", SAVED_COLUMNS)
        pubs = _select_rows(cur, "my_publications", PUB_COLUMNS)
        notebook_folders = (_select_rows(cur, "notebook_folders",
                                         NOTEBOOK_FOLDER_COLUMNS)
                            if _table_exists(conn, "notebook_folders") else [])
        notebook_notes = (_notebook_note_rows(cur)
                          if _table_exists(conn, "notebook_notes") else [])
        tombstones = _select_tombstones(cur)
    finally:
        conn.close()

    config = load_config(db_path)
    return {
        "schema_version": SCHEMA_VERSION,
        "device_id": config.get("device_id"),
        "exported_at": now_iso(),
        "saved_papers": saved,
        "my_publications": pubs,
        "notebook_folders": notebook_folders,
        "notebook_notes": notebook_notes,
        "custom_positive": _load_keywords(db_path, "positive"),
        "custom_negative": _load_keywords(db_path, "negative"),
        "tombstones": tombstones,
    }


def merge_table(local_rows, remote_rows, local_tombs, remote_tombs, key):
    """Last-write-wins merge of one table. Returns (records, tombstone_map)."""
    local_rec = {r[key]: r for r in local_rows if r.get(key)}
    remote_rec = {r[key]: r for r in remote_rows if r.get(key)}
    local_t = {t["record_key"]: t["deleted_at"] for t in local_tombs}
    remote_t = {t["record_key"]: t["deleted_at"] for t in remote_tombs}

    all_keys = set(local_rec) | set(remote_rec) | set(local_t) | set(remote_t)
    merged_records = {}
    merged_tombs = {}

    for k in all_keys:
        candidates = []
        if k in local_rec:
            candidates.append(("keep", _ts(local_rec[k].get("updated_at")), local_rec[k]))
        if k in remote_rec:
            candidates.append(("keep", _ts(remote_rec[k].get("updated_at")), remote_rec[k]))
        if k in local_t:
            candidates.append(("delete", _ts(local_t[k]), None))
        if k in remote_t:
            candidates.append(("delete", _ts(remote_t[k]), None))

        winner = max(candidates, key=lambda c: c[1])
        if winner[0] == "delete":
            merged_tombs[k] = winner[1]
        else:
            merged_records[k] = winner[2]

    return list(merged_records.values()), merged_tombs


def _upsert_row(cur, table, key, columns, rec):
    non_key_cols = [c for c in columns if c != key]
    col_list = ", ".join(columns)
    placeholders = ", ".join(["?"] * len(columns))
    updates = ", ".join(f"{c}=excluded.{c}" for c in non_key_cols)
    values = [rec.get(c) for c in columns]
    cur.execute(
        f"INSERT INTO {table} ({col_list}) VALUES ({placeholders}) "
        f"ON CONFLICT({key}) DO UPDATE SET {updates}",
        values,
    )


def _reconcile(cur, table, key, columns, local_rows, merged_records, merged_tombs):
    local_by_key = {r[key]: r for r in local_rows if r.get(key)}
    merged_keys = {r[key] for r in merged_records if r.get(key)}

    added = updated = deleted = 0
    for rec in merged_records:
        k = rec[key]
        if k not in local_by_key:
            added += 1
        elif _ts(local_by_key[k].get("updated_at")) < _ts(rec.get("updated_at")):
            updated += 1
        _upsert_row(cur, table, key, columns, rec)

    for k, deleted_at in merged_tombs.items():
        cur.execute(f"DELETE FROM {table} WHERE {key} = ?", (k,))
        cur.execute(
            "INSERT OR REPLACE INTO sync_tombstones (table_name, record_key, deleted_at) "
            "VALUES (?, ?, ?)",
            (table, k, deleted_at),
        )
        if k in local_by_key:
            deleted += 1

    for k in merged_keys:
        cur.execute(
            "DELETE FROM sync_tombstones WHERE table_name = ? AND record_key = ?",
            (table, k),
        )

    return {"added": added, "updated": updated, "deleted": deleted, "kept": len(merged_records)}


# ---------------------------------------------------------------------------
# Notebook merge (issue #14)
# ---------------------------------------------------------------------------

def _merge_notebook(cur, snapshot, local_tombs):
    """Merge the Notebook part of a remote snapshot into the local database.

    Folders merge last-write-wins on their stable ``sync_key`` (a rename keeps
    its identity, so it propagates as a rename); notes merge the same way on
    their derived (folder key, paper id) key. ``folder_id`` is device-local,
    so snapshot notes carry their folder's ``sync_key`` and are resolved back
    to a local id here.

    A remote folder whose *name* matches a local folder with a different key
    is the same user-facing folder (the seeded "To read" on both devices, or
    the same name created on both): the local row adopts the remote key —
    re-keying its notes with it — so their records and notes merge instead of
    duplicating.

    Returns per-table stats, or None when this database predates the Notebook
    feature (no notebook tables to merge into).
    """
    if not (_table_exists(cur, "notebook_folders")
            and _table_exists(cur, "notebook_notes")):
        return None

    remote_folders = snapshot.get("notebook_folders") or []
    remote_notes = snapshot.get("notebook_notes") or []
    remote_tombs = snapshot.get("tombstones") or []

    # ── Adoption pass ─────────────────────────────────────────────────────
    local_folders = [dict(r) for r in cur.execute(
        "SELECT id, sync_key, name FROM notebook_folders")]
    for rec in remote_folders:
        remote_key = rec.get("sync_key")
        remote_name = rec.get("name")
        if not remote_key or not remote_name:
            continue
        if any(f["sync_key"] == remote_key for f in local_folders):
            continue
        namesake = next(
            (f for f in local_folders if f["name"] == remote_name), None)
        if namesake is not None:
            cur.execute(
                "UPDATE notebook_folders SET sync_key = ? WHERE id = ?",
                (remote_key, namesake["id"]))
            # The folder's notes follow its new key so they meet their remote
            # twins in the merge below (keys are derived from the folder key).
            cur.execute(
                "UPDATE notebook_notes SET sync_key = ? || ? || paper_id "
                "WHERE folder_id = ?",
                (remote_key, NOTEBOOK_KEY_SEP, namesake["id"]))
            namesake["sync_key"] = remote_key

    # ── Folders ───────────────────────────────────────────────────────────
    local_folder_rows = _select_rows(cur, "notebook_folders",
                                     NOTEBOOK_FOLDER_COLUMNS)
    folders_merged, folder_tombs = merge_table(
        local_folder_rows, remote_folders,
        [t for t in local_tombs if t["table_name"] == "notebook_folders"],
        [t for t in remote_tombs if t["table_name"] == "notebook_folders"],
        "sync_key",
    )
    # Folder names are UNIQUE in the schema, and the merge can still surface
    # one twice (a device renames folder A to a name another device already
    # uses on folder B). The newest record keeps the exact name; the older
    # one gets a numbered suffix so both folders survive the merge. The
    # suffixed records are COPIES: merge_table hands back the local row
    # dicts themselves for locally-won keys, and mutating them here would
    # desynchronise the in-memory local rows from the database (and defeat
    # the name-parking check in the reconcile below).
    taken = set()
    deduped_records = []
    for rec in sorted(folders_merged,
                      key=lambda r: _ts(r.get("updated_at")), reverse=True):
        out = dict(rec)
        name = out.get("name") or "Untitled"
        if name in taken:
            base, i = name, 2
            while name in taken:
                name = f"{base} ({i})"
                i += 1
            out["name"] = name
        taken.add(out["name"])
        deduped_records.append(out)

    folder_stats = _reconcile_notebook_folders(
        cur, local_folder_rows, deduped_records, folder_tombs)

    # ── Notes ─────────────────────────────────────────────────────────────
    local_note_rows = _notebook_note_rows(cur)
    notes_merged, note_tombs = merge_table(
        local_note_rows, remote_notes,
        [t for t in local_tombs if t["table_name"] == "notebook_notes"],
        [t for t in remote_tombs if t["table_name"] == "notebook_notes"],
        "sync_key",
    )
    note_stats = _reconcile_notebook_notes(
        cur, local_note_rows, notes_merged, note_tombs)

    return {"notebook_folders": folder_stats, "notebook_notes": note_stats}


def _reconcile_notebook_folders(cur, local_rows, merged_records, merged_tombs):
    local_by_key = {r["sync_key"]: r for r in local_rows if r.get("sync_key")}
    merged_keys = {r["sync_key"] for r in merged_records if r.get("sync_key")}

    added = updated = deleted = 0
    # Deletions first: a tombstoned folder must not keep blocking a name.
    for k, deleted_at in merged_tombs.items():
        cur.execute("DELETE FROM notebook_folders WHERE sync_key = ?", (k,))
        cur.execute(
            "INSERT OR REPLACE INTO sync_tombstones "
            "(table_name, record_key, deleted_at) VALUES (?, ?, ?)",
            ("notebook_folders", k, deleted_at))
        if k in local_by_key:
            deleted += 1

    # Folder names are UNIQUE, and the merge can hand two rows each other's
    # names (a rename that lands on a name another folder is leaving).
    # Park every name that is about to move on a key-unique temporary first,
    # so the upserts below cannot collide mid-way, in any order. The whole
    # import is one transaction, so a failure rolls the parked names back.
    for rec in merged_records:
        k = rec["sync_key"]
        local = local_by_key.get(k)
        if local is not None and local.get("name") != rec.get("name"):
            cur.execute(
                "UPDATE notebook_folders SET name = ? WHERE sync_key = ?",
                ("\x00" + k, k))

    for rec in merged_records:
        k = rec["sync_key"]
        if k not in local_by_key:
            added += 1
        elif _ts(local_by_key[k].get("updated_at")) < _ts(rec.get("updated_at")):
            updated += 1
        _upsert_row(cur, "notebook_folders", "sync_key",
                    NOTEBOOK_FOLDER_COLUMNS, rec)
    for k in merged_keys:
        cur.execute(
            "DELETE FROM sync_tombstones WHERE table_name = ? AND record_key = ?",
            ("notebook_folders", k))
    return {"added": added, "updated": updated, "deleted": deleted,
            "kept": len(merged_records)}


def _reconcile_notebook_notes(cur, local_rows, merged_records, merged_tombs):
    folder_ids = {}
    for r in cur.execute("SELECT id, sync_key FROM notebook_folders"):
        if r["sync_key"]:
            folder_ids[r["sync_key"]] = r["id"]
    local_by_key = {r["sync_key"]: r for r in local_rows if r.get("sync_key")}
    merged_keys = {r["sync_key"] for r in merged_records if r.get("sync_key")}

    added = updated = deleted = 0
    for k, deleted_at in merged_tombs.items():
        cur.execute("DELETE FROM notebook_notes WHERE sync_key = ?", (k,))
        cur.execute(
            "INSERT OR REPLACE INTO sync_tombstones "
            "(table_name, record_key, deleted_at) VALUES (?, ?, ?)",
            ("notebook_notes", k, deleted_at))
        if k in local_by_key:
            deleted += 1
    for rec in merged_records:
        k = rec.get("sync_key")
        folder_id = folder_ids.get(rec.get("folder_key"))
        if folder_id is None:
            # The note's folder lost the merge (deleted on the winning
            # side), so the note goes with it. No tombstone: if a newer
            # folder record ever beats that tombstone, its notes return.
            continue
        if k not in local_by_key:
            added += 1
        elif _ts(local_by_key[k].get("updated_at")) < _ts(rec.get("updated_at")):
            updated += 1
        _upsert_notebook_note(cur, rec, folder_id)
    for k in merged_keys:
        cur.execute(
            "DELETE FROM sync_tombstones WHERE table_name = ? AND record_key = ?",
            ("notebook_notes", k))
    return {"added": added, "updated": updated, "deleted": deleted,
            "kept": len(merged_records)}


def _upsert_notebook_note(cur, rec, folder_id):
    """Upsert one note row, resolving its folder key to a local folder id."""
    cur.execute(
        "INSERT INTO notebook_notes "
        "(sync_key, folder_id, paper_id, title, authors, url, note, "
        " created_at, updated_at) "
        "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
        "ON CONFLICT(sync_key) DO UPDATE SET "
        "folder_id=excluded.folder_id, paper_id=excluded.paper_id, "
        "title=excluded.title, authors=excluded.authors, url=excluded.url, "
        "note=excluded.note, created_at=excluded.created_at, "
        "updated_at=excluded.updated_at",
        (rec.get("sync_key"), folder_id, rec.get("paper_id"),
         rec.get("title") or "", rec.get("authors") or "",
         rec.get("url") or "", rec.get("note") or "",
         rec.get("created_at") or "", rec.get("updated_at") or EPOCH))


def import_and_merge(snapshot, db_path=None):
    """Merge a remote snapshot into the local database and persist the result.

    Returns per-table statistics describing what changed locally.
    """
    db_path = db_path or get_db_path()
    conn = _connect(db_path)
    try:
        migrate_db(conn)
        cur = conn.cursor()

        local_saved = _select_rows(cur, "saved_papers", SAVED_COLUMNS)
        local_pubs = _select_rows(cur, "my_publications", PUB_COLUMNS)
        local_tombs = _select_tombstones(cur)

        remote_saved = snapshot.get("saved_papers") or []
        remote_pubs = snapshot.get("my_publications") or []
        remote_tombs = snapshot.get("tombstones") or []

        # Snapshots exported by older versions have no "tags"/"highlights"
        # fields; treat them as empty instead of NULL.
        for rec in remote_saved:
            rec["tags"] = rec.get("tags") or ""
            rec["highlights"] = rec.get("highlights") or ""

        saved_merged, saved_tombs = merge_table(
            local_saved, remote_saved,
            [t for t in local_tombs if t["table_name"] == "saved_papers"],
            [t for t in remote_tombs if t["table_name"] == "saved_papers"],
            "arxiv_id",
        )
        pubs_merged, pubs_tombs = merge_table(
            local_pubs, remote_pubs,
            [t for t in local_tombs if t["table_name"] == "my_publications"],
            [t for t in remote_tombs if t["table_name"] == "my_publications"],
            "bibcode",
        )

        saved_stats = _reconcile(cur, "saved_papers", "arxiv_id", SAVED_COLUMNS,
                                 local_saved, saved_merged, saved_tombs)
        pubs_stats = _reconcile(cur, "my_publications", "bibcode", PUB_COLUMNS,
                                local_pubs, pubs_merged, pubs_tombs)

        # Snapshots from before notebook sync (schema v1) carry no notebook
        # fields; the local notebook then simply survives and is re-uploaded.
        notebook_stats = _merge_notebook(cur, snapshot, local_tombs)

        pos, neg = merge_keywords(
            _load_keywords(db_path, "positive"),
            _load_keywords(db_path, "negative"),
            snapshot.get("custom_positive") or [],
            snapshot.get("custom_negative") or [],
        )
        _save_keywords(db_path, "positive", pos)
        _save_keywords(db_path, "negative", neg)

        conn.commit()
    finally:
        conn.close()

    result = {
        "saved_papers": saved_stats,
        "my_publications": pubs_stats,
        "keywords": {"positive": len(pos), "negative": len(neg)},
        "conflicts": 0,
    }
    if notebook_stats:
        result.update(notebook_stats)
    return result


# ---------------------------------------------------------------------------
# Providers
# ---------------------------------------------------------------------------

class CloudProvider(ABC):
    name = "abstract"

    def __init__(self, config):
        self.config = config

    @abstractmethod
    def upload(self, payload):
        """Store bytes remotely; return a metadata dict."""

    @abstractmethod
    def download(self):
        """Return (bytes, metadata) or None when no remote snapshot exists."""

    @abstractmethod
    def status(self):
        """Return a JSON-serializable status dict (no secrets)."""

    def disconnect(self):
        return None


class LocalFolderProvider(CloudProvider):
    name = "local_folder"

    def _path(self):
        path = (self.config.get("local_folder") or {}).get("path", "").strip()
        return os.path.join(path, SYNC_FILENAME) if path else None

    def upload(self, payload):
        path = self._path()
        if not path:
            raise ValueError("Local folder path is not configured")
        os.makedirs(os.path.dirname(path), exist_ok=True)
        tmp = path + ".tmp"
        with open(tmp, "wb") as f:
            f.write(payload)
        os.replace(tmp, path)
        return {"rev": str(os.path.getmtime(path)), "path": path}

    def download(self):
        path = self._path()
        if not path or not os.path.exists(path):
            return None
        with open(path, "rb") as f:
            data = f.read()
        return data, {"rev": str(os.path.getmtime(path)), "path": path}

    def status(self):
        path = self._path()
        return {
            "configured": bool(path),
            "path": path or "",
            "file_exists": bool(path) and os.path.exists(path),
        }


def get_provider(config):
    name = (config or {}).get("provider", "local_folder")
    if name == "local_folder":
        return LocalFolderProvider(config)
    if name == "webdav":
        from arxistant_cloud_providers import WebDavProvider
        return WebDavProvider(config)
    raise ValueError(f"Unknown cloud provider: {name}")


# ---------------------------------------------------------------------------
# Sync orchestration
# ---------------------------------------------------------------------------

def run_sync(db_path=None):
    db_path = db_path or get_db_path()
    config = load_config(db_path)
    if not config.get("enabled"):
        return {"success": False, "error": "Cloud sync is disabled"}

    with _SYNC_LOCK:
        try:
            provider = get_provider(config)
            downloaded = provider.download()
            merge_stats = {}
            if downloaded is not None:
                payload, _meta = downloaded
                remote = json.loads(payload.decode("utf-8"))
                merge_stats = import_and_merge(remote, db_path)

            snapshot = export_snapshot(db_path)
            payload = json.dumps(snapshot, ensure_ascii=False, indent=2).encode("utf-8")
            upload_meta = provider.upload(payload)

            config["last_sync_at"] = now_iso()
            config["last_error"] = None
            save_config(config, db_path)
            return {"success": True, "stats": merge_stats, "remote": upload_meta}
        except Exception as exc:  # surface a readable error to the UI
            config["last_error"] = str(exc)
            save_config(config, db_path)
            return {"success": False, "error": str(exc)}


def schedule_auto_sync(db_path=None, delay=30.0):
    """Debounced background sync after a local mutation, when enabled."""
    global _auto_sync_timer
    db_path = db_path or get_db_path()
    config = load_config(db_path)
    if not config.get("enabled"):
        return
    if _auto_sync_timer is not None and _auto_sync_timer.is_alive():
        return
    _auto_sync_timer = threading.Timer(delay, _auto_sync_worker, args=(db_path,))
    _auto_sync_timer.daemon = True
    _auto_sync_timer.start()


def _auto_sync_worker(db_path):
    global _auto_sync_timer
    try:
        run_sync(db_path)
    finally:
        _auto_sync_timer = None


def maybe_auto_sync_on_start(db_path=None):
    """Run an initial sync at server startup when auto-sync is enabled."""
    db_path = db_path or get_db_path()
    config = load_config(db_path)
    if config.get("enabled"):
        threading.Thread(
            target=run_sync, args=(db_path,),
            name="arxistant-cloud-sync", daemon=True,
        ).start()


def start_periodic_sync(db_path=None):
    """Start a daemon thread that syncs on a fixed interval while enabled.

    This is what keeps devices converged without any manual action: each device
    periodically pulls the remote snapshot and pushes its own changes.
    """
    db_path = db_path or get_db_path()

    def _worker():
        while True:
            try:
                config = load_config(db_path)
                minutes = max(5, int(config.get("interval_minutes") or 60))
            except Exception:
                minutes = 60
            time.sleep(minutes * 60)
            try:
                if load_config(db_path).get("enabled"):
                    run_sync(db_path)
            except Exception:
                pass  # run_sync already records errors; keep looping

    threading.Thread(target=_worker, name="arxistant-periodic-sync", daemon=True).start()
