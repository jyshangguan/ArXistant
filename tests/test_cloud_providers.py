import base64
import io
import sys
import unittest
import urllib.error
from pathlib import Path
from unittest import mock

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT / "src"))

import arxistant_cloud_providers as providers


class _FakeResponse(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()


class WebDavProviderTests(unittest.TestCase):
    def test_status_flags_configuration(self):
        provider = providers.WebDavProvider({
            "webdav": {"url": "https://dav.jianguoyun.com/dav/", "username": "me@example.com"}
        })
        with mock.patch.object(providers.arxistant_secrets, "get_secret", return_value="app-pass"):
            status = provider.status()
        self.assertTrue(status["configured"])
        self.assertEqual(status["username"], "me@example.com")
        self.assertEqual(status["url"], "https://dav.jianguoyun.com/dav/")

    def test_file_url_joins_base_and_filename(self):
        provider = providers.WebDavProvider({"webdav": {"url": "https://dav.jianguoyun.com/dav/"}})
        self.assertEqual(
            provider._file_url(),
            "https://dav.jianguoyun.com/dav/ArXistant/arxistant_sync.json",
        )

    def test_upload_uses_put_with_basic_auth(self):
        provider = providers.WebDavProvider({
            "webdav": {"url": "https://dav.jianguoyun.com/dav/", "username": "me@example.com"}
        })
        captured = {}

        def fake_urlopen(req, timeout=None):
            captured["method"] = req.get_method()
            captured["auth"] = req.get_header("Authorization")
            captured["body"] = req.data
            return _FakeResponse(b"")

        with mock.patch.object(providers.urllib.request, "urlopen", side_effect=fake_urlopen), \
             mock.patch.object(providers.arxistant_secrets, "get_secret", return_value="app-pass"):
            provider.upload(b"payload")

        expected_auth = "Basic " + base64.b64encode(b"me@example.com:app-pass").decode()
        self.assertEqual(captured["method"], "PUT")
        self.assertEqual(captured["auth"], expected_auth)
        self.assertEqual(captured["body"], b"payload")

    def test_download_returns_none_on_404(self):
        provider = providers.WebDavProvider({
            "webdav": {"url": "https://dav.jianguoyun.com/dav/", "username": "me@example.com"}
        })

        def fake_urlopen(req, timeout=None):
            raise urllib.error.HTTPError(req.full_url, 404, "Not Found", None, None)

        with mock.patch.object(providers.urllib.request, "urlopen", side_effect=fake_urlopen), \
             mock.patch.object(providers.arxistant_secrets, "get_secret", return_value="app-pass"):
            self.assertIsNone(provider.download())


class SecretStoreTests(unittest.TestCase):
    def test_set_secret_wraps_keyring_errors(self):
        # Simulate a Linux host without a Secret Service daemon (NoKeyringError).
        fake_keyring = mock.MagicMock()
        fake_keyring.set_password.side_effect = Exception("no keyring daemon")
        with mock.patch.object(providers.arxistant_secrets, "keyring", fake_keyring):
            with self.assertRaises(providers.arxistant_secrets.SecretStoreError):
                providers.arxistant_secrets.set_secret("k", "v")


if __name__ == "__main__":
    unittest.main()
