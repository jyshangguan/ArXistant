"""Invariants for the Android self-update flow (UpdateChecker.java).

A corrupt or truncated release asset once reached the package installer and
failed there with an opaque "problem parsing the package" (and, mid-upload,
surfaced as "Download failed: unexpected end of stream"). The downloader now
verifies every download against the release metadata before installing.
These tests pin that behaviour at the source level, in the same style as
tests/test_version.py — the Java helpers themselves are unit-tested by
extracting their real method text and running it under a JDK.
"""

import re
import sys
import unittest
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
UPDATE_CHECKER = (PROJECT_ROOT / "android" / "app" / "src" / "main" / "java"
                  / "com" / "arxistant" / "app" / "UpdateChecker.java")

sys.path.insert(0, str(PROJECT_ROOT / "src"))


def source():
    return UPDATE_CHECKER.read_text(encoding="utf-8")


class UpdateCheckerInvariantTests(unittest.TestCase):
    def test_reads_the_release_asset_metadata(self):
        src = source()
        # Size and digest come from the release asset, not guesswork.
        self.assertIn('optLong("size"', src)
        self.assertIn('optString("digest"', src)

    def test_verifies_before_installing(self):
        src = source()
        self.assertIn("verifyDownload(apk, expectedSize, expectedDigest)", src)
        # Verification must happen before the installer is offered.
        self.assertLess(src.index("verifyDownload(apk,"), src.index("installApk(apk);"))

    def test_verification_covers_both_length_and_checksum(self):
        src = source()
        self.assertIn("incomplete download", src)
        self.assertIn("checksum mismatch", src)
        self.assertIn('MessageDigest.getInstance("SHA-256")', src)

    def test_retries_and_never_installs_a_suspect_file(self):
        src = source()
        m = re.search(r"DOWNLOAD_ATTEMPTS\s*=\s*(\d+)", src)
        self.assertIsNotNone(m, "DOWNLOAD_ATTEMPTS constant disappeared")
        self.assertGreaterEqual(int(m.group(1)), 2)
        # The suspect file is deleted, so a stale partial download can never
        # be handed to the installer on a later attempt.
        self.assertIn("apk.delete()", src)

    def test_detects_an_early_closed_connection(self):
        src = source()
        self.assertIn("getContentLengthLong()", src)
        self.assertIn("connection closed early", src)

    def test_logs_the_parsed_release_for_remote_diagnosis(self):
        # A failed update must be diagnosable from `adb logcat` alone.
        src = source()
        self.assertIn('Log.i(TAG, "release="', src)
        self.assertIn("assetDigest=", src)

    def test_manual_failure_points_to_the_release_page(self):
        src = source()
        self.assertIn("GitHub ", src)
        self.assertIn("release page", src)


if __name__ == "__main__":
    unittest.main()
