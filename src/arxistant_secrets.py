"""Secure storage for cloud credentials.

Cloud credentials (the Nutstore/WebDAV app password) are kept in the system
keychain rather than written to disk in plaintext. ``keyring`` provides the
desktop backends (macOS Keychain, Windows Credential Manager, Linux Secret
Service).

On Android there is no ``keyring`` backend, so the Android app installs a custom
backend via ``set_backend()`` (for example an Android Keystore /
EncryptedSharedPreferences bridge exposed through Chaquopy). When a custom
backend is set it takes precedence over ``keyring``.
"""

try:
    import keyring
except ImportError:
    keyring = None

SERVICE = "arxistant"

WEBDAV_PASSWORD = "webdav_password"
LLM_API_KEY = "llm_api_key"

# Optional custom backend. Must expose get(key), set(key, value), delete(key).
_backend = None


class SecretStoreError(RuntimeError):
    pass


def set_backend(backend):
    """Install a custom secret backend (e.g. an Android Keystore bridge)."""
    global _backend
    _backend = backend


def is_available():
    return _backend is not None or keyring is not None


def get_secret(key):
    """Return the stored secret, or None when missing/unavailable."""
    if _backend is not None:
        try:
            return _backend.get(key)
        except Exception:
            return None
    if keyring is None:
        return None
    try:
        return keyring.get_password(SERVICE, key)
    except Exception:
        return None


def set_secret(key, value):
    """Store a secret; empty value deletes it. Raises when storage is unavailable."""
    if not value:
        delete_secret(key)
        return
    if _backend is not None:
        try:
            _backend.set(key, value)
            return
        except Exception as exc:
            raise SecretStoreError(
                "Could not store the credential in the secure store: " + str(exc)
            ) from exc
    if keyring is None:
        raise SecretStoreError(
            "The 'keyring' package is required to store cloud credentials securely. "
            "Install it with: pip install keyring"
        )
    try:
        keyring.set_password(SERVICE, key, value)
    except Exception as exc:
        # On Linux this is typically a NoKeyringError when no Secret Service
        # provider (e.g. gnome-keyring) is available. Surface a clean message
        # instead of an unhandled traceback.
        raise SecretStoreError(
            "Could not store the credential in the system keychain: " + str(exc)
        ) from exc


def delete_secret(key):
    if _backend is not None:
        try:
            _backend.delete(key)
        except Exception:
            pass
        return
    if keyring is None:
        return
    try:
        keyring.delete_password(SERVICE, key)
    except Exception:
        pass
