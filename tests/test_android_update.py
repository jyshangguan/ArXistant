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


class RateLimitHandlingTests(unittest.TestCase):
    """GitHub answers unauthenticated rate-limit exhaustion with HTTP 403.

    A phone network shares one public address (CGNAT), so the 60-requests-
    per-hour unauthenticated API budget is often spent by others when the
    app checks. The checker must recognise that 403 via its rate-limit
    headers instead of reporting an opaque failure, back off until the
    window resets instead of hammering, and still answer the "is there a
    newer version" question through the release page redirect — plain
    github.com traffic, outside the API budget.
    """

    def test_detects_the_rate_limit_403_through_its_headers(self):
        src = source()
        self.assertIn("X-RateLimit-Remaining", src)
        self.assertIn("X-RateLimit-Reset", src)
        self.assertIn("RateLimitedException", src)
        # Only 403/429 with a spent budget is a rate limit; anything else
        # keeps the generic "GitHub returned HTTP <code>" message.
        self.assertIn('"0".equals(header(connection, "X-RateLimit-Remaining"))', src)
        self.assertIn('"GitHub returned HTTP " + code', src)

    def test_backs_off_while_the_window_is_open(self):
        src = source()
        self.assertIn("KEY_RATE_LIMITED_UNTIL", src)
        self.assertIn('"rate_limited_until"', src)
        self.assertIn("putLong(KEY_RATE_LIMITED_UNTIL", src)
        # The check consults the window before calling the API.
        self.assertIn("prefs().getLong(KEY_RATE_LIMITED_UNTIL, 0L)", src)

    def test_falls_back_to_the_release_page_redirect(self):
        src = source()
        self.assertIn("RELEASE_PAGE_URL", src)
        self.assertIn('"https://github.com/jyshangguan/ArXistant/releases/latest"', src)
        # No body is fetched: the Location header of the 302 is the answer.
        self.assertIn("setInstanceFollowRedirects(false)", src)
        self.assertIn('lastIndexOf("/tag/")', src)

    def test_rate_limited_update_points_at_the_release_page(self):
        # Without asset metadata there is no verified auto-download, so an
        # update found via the fallback opens the release page instead.
        src = source()
        self.assertIn("fallbackViaReleasePage", src)
        self.assertIn('"Open release page"', src)
        self.assertIn("cannot be downloaded and verified automatically", src)

    def test_error_explains_the_shared_budget_and_reset_time(self):
        src = source()
        self.assertIn("60 requests per hour", src)
        self.assertIn("X-RateLimit-Reset", src)  # via the header check
        self.assertIn("The limit resets at", src)

    def test_startup_check_stays_silent_on_failure(self):
        # The silent startup path only shows dialogs when an update exists;
        # failures (rate limit included) are manual-only messages.
        src = source()
        self.assertIn("if (manual) showError(rateLimitMessage(cause));", src)


if __name__ == "__main__":
    unittest.main()
