"""OS-keychain-backed storage for cloud credentials.

Cloud credentials (the Nutstore/WebDAV app password) are kept in the system
keychain rather than written to disk in plaintext. ``keyring`` provides the
cross-platform backend (macOS Keychain, Windows Credential Manager, Linux
Secret Service).
"""

try:
    import keyring
except ImportError:
    keyring = None

SERVICE = "arxistant"

WEBDAV_PASSWORD = "webdav_password"


class SecretStoreError(RuntimeError):
    pass


def is_available():
    return keyring is not None


def get_secret(key):
    """Return the stored secret, or None when missing/unavailable."""
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
    if keyring is None:
        return
    try:
        keyring.delete_password(SERVICE, key)
    except Exception:
        pass
