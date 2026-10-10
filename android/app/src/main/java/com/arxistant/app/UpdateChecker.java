package com.arxistant.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Context;
import android.content.Intent;
import android.content.SharedPreferences;
import android.net.Uri;
import android.os.Build;
import android.os.Handler;
import android.os.Looper;
import android.provider.Settings;
import android.util.Log;
import android.widget.ProgressBar;
import android.widget.TextView;

import androidx.core.content.FileProvider;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.BufferedInputStream;
import java.io.File;
import java.io.FileInputStream;
import java.io.FileOutputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;
import java.security.MessageDigest;
import java.text.SimpleDateFormat;
import java.util.Date;
import java.util.Locale;

/**
 * Self-update flow for the Android app.
 *
 * Checks the GitHub release page of the project for a newer version, offers
 * to download the release APK, verifies it (byte count and SHA-256 against
 * the release asset metadata), and hands it to the system package installer.
 * Triggered from the "..." menu ("Check for Updates", via the JS bridge) and
 * silently once at startup.
 *
 * GitHub's unauthenticated API allows only 60 requests per hour per network
 * address, and answers exhaustion with HTTP 403 (not 429) — on a phone
 * network that address is shared with everyone else behind it, so the check
 * can be refused even when this app rarely calls. The checker recognises the
 * rate limit, backs off until the reset time instead of hammering, and still
 * answers the "is there a newer version" question through the release page
 * redirect, which is plain github.com traffic outside the API budget.
 */
public class UpdateChecker {
    private static final String TAG = "ArxistantUpdate";
    private static final String RELEASE_API_URL =
            "https://api.github.com/repos/jyshangguan/ArXistant/releases/latest";
    private static final String RELEASE_PAGE_URL =
            "https://github.com/jyshangguan/ArXistant/releases/latest";
    private static final String UPDATE_FILE_NAME = "arxistant-update.apk";
    /** A corrupt or truncated download is retried once before giving up. */
    private static final int DOWNLOAD_ATTEMPTS = 2;
    private static final String PREFS_NAME = "arxistant_update";
    private static final String KEY_RATE_LIMITED_UNTIL = "rate_limited_until";

    private final Activity activity;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private File pendingApk;
    private AlertDialog progressDialog;
    private TextView progressText;

    public UpdateChecker(Activity activity) {
        this.activity = activity;
    }

    /**
     * Check for a newer release. When {@code manual} is true the outcome is
     * always reported in a dialog; the silent startup check only speaks up
     * when an update is actually available.
     */
    public void check(boolean manual) {
        new Thread(() -> {
            // Inside a known rate-limit window the API call would only be
            // refused again, so go straight to the release-page fallback.
            long limitedUntil = prefs().getLong(KEY_RATE_LIMITED_UNTIL, 0L);
            if (System.currentTimeMillis() < limitedUntil) {
                fallbackViaReleasePage(manual, null);
                return;
            }
            try {
                JSONObject release = fetchJson(RELEASE_API_URL);
                String tag = release.optString("tag_name", "");
                String latest = tag.startsWith("v") ? tag.substring(1) : tag;
                String current = BuildConfig.VERSION_NAME;
                if (latest.isEmpty()) {
                    if (manual) showError("The latest GitHub release carries no version tag.");
                    return;
                }
                JSONObject asset = findApkAsset(release);
                long expectedSize = asset == null ? -1 : asset.optLong("size", -1);
                String expectedDigest = asset == null ? "" : normalizeDigest(asset.optString("digest", ""));
                // Logged on every check so a failed update can be diagnosed
                // from `adb logcat` without reproducing it interactively.
                Log.i(TAG, "release=" + latest + " installed=" + current
                        + " assetSize=" + expectedSize
                        + " assetDigest=" + (expectedDigest.isEmpty() ? "(none)" : expectedDigest));
                if (compareVersions(latest, current) <= 0) {
                    if (manual) showInfo("ArXistant is up to date (v" + current + ").");
                    return;
                }
                if (asset == null) {
                    if (manual) showError("Version " + latest
                            + " is available, but the release has no APK asset. "
                            + "Download it manually from the GitHub release page.");
                    return;
                }
                String apkUrl = asset.optString("browser_download_url", null);
                if (apkUrl == null || apkUrl.isEmpty()) {
                    if (manual) showError("Version " + latest
                            + " is available, but its APK asset has no download URL.");
                    return;
                }
                mainHandler.post(() -> offerUpdate(latest, apkUrl, expectedSize, expectedDigest));
            } catch (RateLimitedException e) {
                Log.w(TAG, "GitHub API rate-limited this network", e);
                recordRateLimit(e.resetAtMillis);
                fallbackViaReleasePage(manual, e);
            } catch (Exception e) {
                Log.w(TAG, "Update check failed", e);
                if (manual) showError("Update check failed: " + e.getMessage());
            }
        }, "arxistant-update-check").start();
    }

    /** Resume a download that was waiting for the unknown-sources permission. */
    public void resumePendingInstall() {
        if (pendingApk == null) return;
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                && !activity.getPackageManager().canRequestPackageInstalls()) {
            return; // still not allowed; keep waiting
        }
        File apk = pendingApk;
        pendingApk = null;
        launchInstaller(apk);
    }

    // ── network ─────────────────────────────────────────────────────────

    /** The unauthenticated API budget (60/hour per network) is exhausted. */
    private static final class RateLimitedException extends Exception {
        final long resetAtMillis;

        RateLimitedException(long resetEpochSeconds) {
            super("rate limited"
                    + (resetEpochSeconds > 0 ? " (reset epoch " + resetEpochSeconds + ")" : ""));
            this.resetAtMillis = resetEpochSeconds > 0 ? resetEpochSeconds * 1000L : 0L;
        }
    }

    private static JSONObject fetchJson(String url) throws Exception {
        HttpURLConnection connection =
                (HttpURLConnection) new URL(url).openConnection();
        connection.setConnectTimeout(10000);
        connection.setReadTimeout(15000);
        // GitHub rejects requests without a User-Agent header.
        connection.setRequestProperty("User-Agent", "ArXistant-Android");
        try {
            int code = connection.getResponseCode();
            if (code == 403 || code == 429) {
                // GitHub answers rate-limit exhaustion with 403 (not 429)
                // for unauthenticated callers; the headers say which.
                if ("0".equals(header(connection, "X-RateLimit-Remaining"))) {
                    throw new RateLimitedException(
                            parseEpochSeconds(header(connection, "X-RateLimit-Reset")));
                }
            }
            if (code != 200) {
                throw new RuntimeException("GitHub returned HTTP " + code);
            }
            StringBuilder body = new StringBuilder();
            try (InputStream in = connection.getInputStream()) {
                byte[] buffer = new byte[8192];
                int n;
                while ((n = in.read(buffer)) > 0) {
                    body.append(new String(buffer, 0, n, "UTF-8"));
                }
            }
            return new JSONObject(body.toString());
        } finally {
            connection.disconnect();
        }
    }

    /**
     * The latest release tag, via the release page's redirect. The page is
     * plain github.com traffic — outside the 60/hour API budget — and
     * answers 302 to {@code .../releases/tag/<tag>} without a body.
     */
    private static String fetchLatestTag() throws Exception {
        HttpURLConnection connection =
                (HttpURLConnection) new URL(RELEASE_PAGE_URL).openConnection();
        connection.setConnectTimeout(10000);
        connection.setReadTimeout(15000);
        connection.setRequestProperty("User-Agent", "ArXistant-Android");
        // Don't follow the redirect; the Location header is the answer.
        connection.setInstanceFollowRedirects(false);
        try {
            int code = connection.getResponseCode();
            String location = connection.getHeaderField("Location");
            if ((code == 301 || code == 302) && location != null) {
                int idx = location.lastIndexOf("/tag/");
                if (idx >= 0) {
                    return location.substring(idx + "/tag/".length());
                }
            }
            throw new IOException("release page answered HTTP " + code
                    + (location == null ? "" : " -> " + location));
        } finally {
            connection.disconnect();
        }
    }

    private static String header(HttpURLConnection connection, String name) {
        String value = connection.getHeaderField(name);
        return value == null ? "" : value.trim();
    }

    private static long parseEpochSeconds(String value) {
        if (value == null) return 0;
        try {
            return Long.parseLong(value.trim());
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    private SharedPreferences prefs() {
        return activity.getSharedPreferences(PREFS_NAME, Context.MODE_PRIVATE);
    }

    /**
     * Remember the rate-limit window so repeated checks (startup + manual)
     * stop calling the API until it can succeed again. A missing or past
     * reset time falls back to a five-minute backoff.
     */
    private void recordRateLimit(long resetAtMillis) {
        long until = resetAtMillis;
        if (until <= System.currentTimeMillis()) {
            until = System.currentTimeMillis() + 5 * 60 * 1000L;
        }
        prefs().edit().putLong(KEY_RATE_LIMITED_UNTIL, until).apply();
    }

    /**
     * Version-only fallback when the API is rate-limited. Asset metadata
     * (size/digest/download URL) is not available this way, so an update
     * found here points at the release page instead of auto-downloading.
     */
    private void fallbackViaReleasePage(boolean manual, RateLimitedException cause) {
        String tag;
        try {
            tag = fetchLatestTag();
        } catch (Exception e) {
            Log.w(TAG, "Release-page fallback failed too", e);
            if (manual) showError(rateLimitMessage(cause));
            return;
        }
        String latest = tag.startsWith("v") ? tag.substring(1) : tag;
        if (latest.isEmpty()) {
            if (manual) showError(rateLimitMessage(cause));
            return;
        }
        String current = BuildConfig.VERSION_NAME;
        Log.i(TAG, "release=" + latest + " installed=" + current
                + " (via release-page fallback)"
                + (cause == null ? " (within rate-limit window)" : ""));
        if (compareVersions(latest, current) <= 0) {
            if (manual) showInfo("ArXistant is up to date (v" + current + ").");
            return;
        }
        String message = "ArXistant v" + latest + " is available (installed: v"
                + current + ").\n\n"
                + "GitHub is rate-limiting the update API for this network right now, "
                + "so the APK cannot be downloaded and verified automatically. "
                + "Open the release page in your browser and download it there?";
        mainHandler.post(() -> new AlertDialog.Builder(activity)
                .setTitle("Update available")
                .setMessage(message)
                .setPositiveButton("Open release page", (d, w) -> {
                    try {
                        activity.startActivity(new Intent(
                                Intent.ACTION_VIEW, Uri.parse(RELEASE_PAGE_URL)));
                    } catch (Exception e) {
                        showError("Could not open the browser: " + e.getMessage());
                    }
                })
                .setNegativeButton(android.R.string.cancel, null)
                .show());
    }

    /** A human explanation of the 403, with the reset time when known. */
    private String rateLimitMessage(RateLimitedException cause) {
        StringBuilder sb = new StringBuilder(
                "GitHub refused the update check with HTTP 403: the "
                + "unauthenticated API allows 60 requests per hour per "
                + "network address, and yours is shared or busy right now.\n");
        if (cause != null && cause.resetAtMillis > 0) {
            sb.append("The limit resets at ")
                    .append(new SimpleDateFormat("HH:mm", Locale.getDefault())
                            .format(new Date(cause.resetAtMillis)))
                    .append(".\n");
        } else {
            sb.append("The app will retry automatically later.\n");
        }
        sb.append("You can always download the newest APK from the GitHub "
                + "release page.");
        return sb.toString();
    }

    /** The release asset whose name ends in .apk, or null. */
    private static JSONObject findApkAsset(JSONObject release) {
        JSONArray assets = release.optJSONArray("assets");
        if (assets == null) return null;
        for (int i = 0; i < assets.length(); i++) {
            JSONObject asset = assets.optJSONObject(i);
            if (asset == null) continue;
            String name = asset.optString("name", "");
            if (name.endsWith(".apk")) {
                return asset;
            }
        }
        return null;
    }

    /**
     * GitHub reports asset digests as "sha256:<hex>"; accept that form, a
     * bare hex digest, or nothing (older API responses) — in which case only
     * the byte count is verified.
     */
    static String normalizeDigest(String digest) {
        if (digest == null) return "";
        String d = digest.trim().toLowerCase();
        int colon = d.indexOf(':');
        if (colon >= 0) d = d.substring(colon + 1);
        return d.matches("[0-9a-f]{64}") ? d : "";
    }

    /** SHA-256 of a file as lowercase hex, streamed so an 80 MB APK fits. */
    static String sha256Hex(File file) throws Exception {
        MessageDigest md = MessageDigest.getInstance("SHA-256");
        try (InputStream in = new BufferedInputStream(new FileInputStream(file))) {
            byte[] buffer = new byte[65536];
            int n;
            while ((n = in.read(buffer)) > 0) {
                md.update(buffer, 0, n);
            }
        }
        byte[] digest = md.digest();
        StringBuilder sb = new StringBuilder(digest.length * 2);
        for (byte b : digest) {
            sb.append(Character.forDigit((b >> 4) & 0xF, 16));
            sb.append(Character.forDigit(b & 0xF, 16));
        }
        return sb.toString();
    }

    /**
     * Verify a downloaded APK against the release metadata. A truncated or
     * corrupt asset would otherwise reach the package installer and fail
     * there with an opaque "problem parsing the package".
     */
    static void verifyDownload(File apk, long expectedSize, String expectedDigest)
            throws IOException, Exception {
        long actual = apk.length();
        if (expectedSize > 0 && actual != expectedSize) {
            throw new IOException("incomplete download (got " + actual
                    + " of " + expectedSize + " bytes)");
        }
        if (expectedDigest != null && !expectedDigest.isEmpty()) {
            String actualDigest = sha256Hex(apk);
            if (!expectedDigest.equalsIgnoreCase(actualDigest)) {
                throw new IOException("checksum mismatch — the downloaded file is corrupt"
                        + " (expected " + expectedDigest.substring(0, 12) + "…, got "
                        + actualDigest.substring(0, 12) + "…)");
            }
        }
    }

    /** Numeric dotted-version comparison (positive when a > b). */
    static int compareVersions(String a, String b) {
        String[] as = a.split("\\.");
        String[] bs = b.split("\\.");
        int n = Math.max(as.length, bs.length);
        for (int i = 0; i < n; i++) {
            int av = i < as.length ? parseSegment(as[i]) : 0;
            int bv = i < bs.length ? parseSegment(bs[i]) : 0;
            if (av != bv) return Integer.compare(av, bv);
        }
        return 0;
    }

    private static int parseSegment(String s) {
        try {
            return Integer.parseInt(s.replaceAll("[^0-9]", ""));
        } catch (NumberFormatException e) {
            return 0;
        }
    }

    // ── UI flow ─────────────────────────────────────────────────────────

    private void offerUpdate(String latest, String apkUrl, long expectedSize, String expectedDigest) {
        new AlertDialog.Builder(activity)
                .setTitle("Update available")
                .setMessage("ArXistant v" + latest + " is available "
                        + "(installed: v" + BuildConfig.VERSION_NAME
                        + "). Download and install it now?")
                .setPositiveButton("Download",
                        (d, w) -> downloadAndInstall(apkUrl, expectedSize, expectedDigest))
                .setNegativeButton(android.R.string.cancel, null)
                .show();
    }

    private void downloadAndInstall(String url, long expectedSize, String expectedDigest) {
        showProgress("Downloading update…");
        new Thread(() -> {
            File dir = new File(activity.getCacheDir(), "update");
            File apk = new File(dir, UPDATE_FILE_NAME);
            Exception lastError = null;
            for (int attempt = 1; attempt <= DOWNLOAD_ATTEMPTS; attempt++) {
                try {
                    if (!dir.exists() && !dir.mkdirs()) {
                        throw new IOException("Could not create download directory");
                    }
                    downloadTo(url, apk);
                    setProgressMessage("Verifying download…");
                    verifyDownload(apk, expectedSize, expectedDigest);
                    Log.i(TAG, "Download verified (" + apk.length() + " bytes) on attempt " + attempt);
                    mainHandler.post(() -> {
                        hideProgress();
                        installApk(apk);
                    });
                    return;
                } catch (Exception e) {
                    lastError = e;
                    Log.w(TAG, "Update download attempt " + attempt + " failed", e);
                    // Never hand a suspect file to the installer.
                    //noinspection ResultOfMethodCallIgnored
                    apk.delete();
                    if (attempt < DOWNLOAD_ATTEMPTS) {
                        setProgressMessage("Download failed — retrying…");
                        try {
                            Thread.sleep(1500);
                        } catch (InterruptedException ie) {
                            Thread.currentThread().interrupt();
                            break;
                        }
                    }
                }
            }
            final Exception failure = lastError;
            mainHandler.post(() -> {
                hideProgress();
                showError("Download failed after " + DOWNLOAD_ATTEMPTS + " attempts: "
                        + (failure == null ? "unknown error" : failure.getMessage())
                        + "\n\nYou can download the APK manually from the GitHub "
                        + "release page and open it to install.");
            });
        }, "arxistant-update-download").start();
    }

    /** Stream the APK to disk, checking the transfer length as it arrives. */
    private static void downloadTo(String url, File apk) throws Exception {
        HttpURLConnection connection = (HttpURLConnection) new URL(url).openConnection();
        connection.setConnectTimeout(10000);
        connection.setReadTimeout(60000);
        connection.setRequestProperty("User-Agent", "ArXistant-Android");
        connection.setInstanceFollowRedirects(true);
        try {
            if (connection.getResponseCode() != 200) {
                throw new IOException("HTTP " + connection.getResponseCode());
            }
            long declared = connection.getContentLengthLong();
            long written = 0;
            try (InputStream in = connection.getInputStream();
                 OutputStream out = new FileOutputStream(apk)) {
                byte[] buffer = new byte[16384];
                int n;
                while ((n = in.read(buffer)) > 0) {
                    out.write(buffer, 0, n);
                    written += n;
                }
            }
            // Catch a connection that ends early even before the checksum.
            if (declared > 0 && written != declared) {
                throw new IOException("connection closed early (got " + written
                        + " of " + declared + " bytes)");
            }
        } finally {
            connection.disconnect();
        }
    }

    private void installApk(File apk) {
        if (Build.VERSION.SDK_INT >= Build.VERSION_CODES.O
                && !activity.getPackageManager().canRequestPackageInstalls()) {
            pendingApk = apk;
            new AlertDialog.Builder(activity)
                    .setTitle("Allow installing apps")
                    .setMessage("Android blocks ArXistant from installing the "
                            + "update. Allow \"install unknown apps\" for "
                            + "ArXistant in the next screen, then come back — "
                            + "the installer opens automatically.")
                    .setPositiveButton("Open settings", (d, w) -> {
                        Intent intent = new Intent(
                                Settings.ACTION_MANAGE_UNKNOWN_APP_SOURCES,
                                Uri.parse("package:" + activity.getPackageName()));
                        activity.startActivity(intent);
                    })
                    .setNegativeButton(android.R.string.cancel, null)
                    .show();
            return;
        }
        launchInstaller(apk);
    }

    private void launchInstaller(File apk) {
        Uri uri = FileProvider.getUriForFile(activity,
                activity.getPackageName() + ".fileprovider", apk);
        Intent intent = new Intent(Intent.ACTION_VIEW);
        intent.setDataAndType(uri, "application/vnd.android.package-archive");
        intent.addFlags(Intent.FLAG_GRANT_READ_URI_PERMISSION);
        activity.startActivity(intent);
    }

    private void showProgress(String message) {
        mainHandler.post(() -> {
            ProgressBar bar = new ProgressBar(activity);
            bar.setIndeterminate(true);
            bar.setPadding(48, 24, 48, 8);
            progressText = new TextView(activity);
            progressText.setText(message);
            progressText.setPadding(48, 0, 48, 8);
            android.widget.LinearLayout box = new android.widget.LinearLayout(activity);
            box.setOrientation(android.widget.LinearLayout.VERTICAL);
            box.addView(bar);
            box.addView(progressText);
            progressDialog = new AlertDialog.Builder(activity)
                    .setTitle("ArXistant")
                    .setView(box)
                    .setCancelable(false)
                    .show();
        });
    }

    private void setProgressMessage(String message) {
        mainHandler.post(() -> {
            if (progressText != null) progressText.setText(message);
        });
    }

    private void hideProgress() {
        if (progressDialog != null) {
            progressDialog.dismiss();
            progressDialog = null;
        }
        progressText = null;
    }

    private void showInfo(String message) {
        mainHandler.post(() -> new AlertDialog.Builder(activity)
                .setTitle("ArXistant")
                .setMessage(message)
                .setPositiveButton(android.R.string.ok, null)
                .show());
    }

    private void showError(String message) {
        mainHandler.post(() -> new AlertDialog.Builder(activity)
                .setTitle("Update")
                .setMessage(message)
                .setPositiveButton(android.R.string.ok, null)
                .show());
    }
}
