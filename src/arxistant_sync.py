"""Cloud sync engine for the ArXistant paper database.

The paper database (saved papers + publications + custom keywords) is exported
to a versioned JSON snapshot, merged with a remote snapshot using per-record
last-write-wins timestamps, and pushed to a storage provider.

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
SCHEMA_VERSION = 1
EPOCH = "1970-01-01T00:00:00+00:00"

# Non-id columns that participate in sync. The autoincrement ``id`` column is
# deliberately excluded because it is device-local and may collide across
# installs.
SAVED_COLUMNS = [
    "arxiv_id", "title", "authors", "abstract", "relevance_score",
    "date_fetched", "date_saved", "notes", "tags", "updated_at",
]
PUB_COLUMNS = [
    "bibcode", "title", "authors", "abstract", "keywords", "year",
    "date_added", "updated_at",
]

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
    with open(path, "r", encoding="utf-8") as f:
        data = json.load(f)
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
    """Add sync columns/table to an existing database. Idempotent."""
    cur = conn.cursor()

    for table in ("saved_papers", "my_publications"):
        cur.execute(f"PRAGMA table_info({table})")
        columns = {row[1] for row in cur.fetchall()}
        if "updated_at" not in columns:
            cur.execute(f"ALTER TABLE {table} ADD COLUMN updated_at TEXT")

    # Tags on saved papers (issue #3). Older databases get the column lazily;
    # rows created before tags existed default to an empty tag list.
    cur.execute("PRAGMA table_info(saved_papers)")
    saved_columns = {row[1] for row in cur.fetchall()}
    if "tags" not in saved_columns:
        cur.execute("ALTER TABLE saved_papers ADD COLUMN tags TEXT DEFAULT ''")
    cur.execute("UPDATE saved_papers SET tags = '' WHERE tags IS NULL")

    cur.execute("""
        CREATE TABLE IF NOT EXISTS sync_tombstones (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            table_name TEXT NOT NULL,
            record_key TEXT NOT NULL,
            deleted_at TEXT NOT NULL,
            UNIQUE(table_name, record_key)
        )
    """)

    # Rows created before sync existed get the epoch, so any real edit on any
    # device wins over them.
    cur.execute(
        "UPDATE saved_papers SET updated_at = ? WHERE updated_at IS NULL OR updated_at = ''",
        (EPOCH,),
    )
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


def export_snapshot(db_path=None):
    db_path = db_path or get_db_path()
    conn = _connect(db_path)
    try:
        migrate_db(conn)
        saved = _select_rows(conn.cursor(), "saved_papers", SAVED_COLUMNS)
        pubs = _select_rows(conn.cursor(), "my_publications", PUB_COLUMNS)
        tombstones = _select_tombstones(conn.cursor())
    finally:
        conn.close()

    config = load_config(db_path)
    return {
        "schema_version": SCHEMA_VERSION,
        "device_id": config.get("device_id"),
        "exported_at": now_iso(),
        "saved_papers": saved,
        "my_publications": pubs,
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

        # Snapshots exported by older versions have no "tags" field; treat
        # them as an empty tag list instead of NULL.
        for rec in remote_saved:
            rec["tags"] = rec.get("tags") or ""

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

    return {
        "saved_papers": saved_stats,
        "my_publications": pubs_stats,
        "keywords": {"positive": len(pos), "negative": len(neg)},
        "conflicts": 0,
    }


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
