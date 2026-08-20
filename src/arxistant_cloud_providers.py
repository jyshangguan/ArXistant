"""WebDAV cloud provider for ArXistant sync.

Implements the ``CloudProvider`` interface defined in ``arxistant_sync`` using
standard WebDAV over HTTPS. It defaults to Nutstore (坚果云), whose WebDAV
endpoint only accepts a dedicated "app password" rather than the account login
password.
"""

import base64
import urllib.error
import urllib.request

import arxistant_secrets
from arxistant_sync import CloudProvider, SYNC_FILENAME

# Nutstore (坚果云) WebDAV endpoint. Overridable in Settings.
DEFAULT_WEBDAV_URL = "https://dav.jianguoyun.com/dav/"

# Folder (relative to the WebDAV base) that ArXistant creates and syncs into.
SYNC_DIR_NAME = "ArXistant"


class WebDavProvider(CloudProvider):
    """WebDAV provider (defaults to Nutstore / 坚果云).

    Uses HTTP Basic auth with a dedicated app password (stored in the OS
    keychain, never the user's real Nutstore password). The snapshot lives in a
    dedicated subfolder that is created (MKCOL) on demand, because Nutstore
    misbehaves on direct PUTs to the WebDAV root collection.
    """

    name = "webdav"

    def _config(self):
        return self.config.get("webdav") or {}

    def _base_dir(self):
        base = self._config().get("url") or DEFAULT_WEBDAV_URL
        if not base.endswith("/"):
            base += "/"
        return base

    def _dir_url(self):
        return self._base_dir() + SYNC_DIR_NAME + "/"

    def _file_url(self):
        return self._dir_url() + SYNC_FILENAME

    def _auth_header(self):
        username = self._config().get("username", "")
        password = arxistant_secrets.get_secret(arxistant_secrets.WEBDAV_PASSWORD)
        if not username or not password:
            raise ValueError(
                "Nutstore WebDAV is not configured (email and app password required)"
            )
        token = base64.b64encode(f"{username}:{password}".encode()).decode()
        return f"Basic {token}"

    def _raise_http(self, exc, operation, url):
        if exc.code in (401, 403):
            raise ValueError(
                f"Nutstore rejected the credentials (HTTP {exc.code}). Make sure the "
                "password is the dedicated Nutstore app password (第三方应用密码), "
                "not your normal login password, and that it was copied exactly."
            ) from exc
        raise ValueError(
            f"WebDAV {operation} failed (HTTP {exc.code}) for {url}: {exc.reason}"
        ) from exc

    def _ensure_dir(self):
        url = self._dir_url()
        req = urllib.request.Request(
            url, method="MKCOL", headers={"Authorization": self._auth_header()}
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            if exc.code == 405:  # 405 Method Not Allowed = folder already exists
                return
            self._raise_http(exc, "create folder (MKCOL)", url)

    def upload(self, payload):
        self._ensure_dir()
        url = self._file_url()
        req = urllib.request.Request(
            url, data=payload, method="PUT",
            headers={"Authorization": self._auth_header(), "Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                resp.read()
        except urllib.error.HTTPError as exc:
            self._raise_http(exc, "upload (PUT)", url)
        return {"url": url}

    def download(self):
        url = self._file_url()
        req = urllib.request.Request(url, headers={"Authorization": self._auth_header()})
        try:
            with urllib.request.urlopen(req, timeout=60) as resp:
                data = resp.read()
        except urllib.error.HTTPError as exc:
            # 404 = file missing; 409 = parent folder missing (Nutstore's
            # response when the ArXistant folder has not been created yet).
            if exc.code in (404, 409):
                return None
            self._raise_http(exc, "download (GET)", url)
        return data, {"url": url}

    def status(self):
        c = self._config()
        username = c.get("username", "")
        password = arxistant_secrets.get_secret(arxistant_secrets.WEBDAV_PASSWORD)
        return {
            "configured": bool(username and password and (c.get("url") or DEFAULT_WEBDAV_URL)),
            "url": c.get("url") or DEFAULT_WEBDAV_URL,
            "username": username,
            "folder": SYNC_DIR_NAME,
        }
