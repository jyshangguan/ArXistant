package com.arxistant.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
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

/**
 * Self-update flow for the Android app.
 *
 * Checks the GitHub release page of the project for a newer version, offers
 * to download the release APK, verifies it (byte count and SHA-256 against
 * the release asset metadata), and hands it to the system package installer.
 * Triggered from the "..." menu ("Check for Updates", via the JS bridge) and
 * silently once at startup.
 */
public class UpdateChecker {
    private static final String TAG = "ArxistantUpdate";
    private static final String RELEASE_API_URL =
            "https://api.github.com/repos/jyshangguan/ArXistant/releases/latest";
    private static final String UPDATE_FILE_NAME = "arxistant-update.apk";
    /** A corrupt or truncated download is retried once before giving up. */
    private static final int DOWNLOAD_ATTEMPTS = 2;

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

    private static JSONObject fetchJson(String url) throws Exception {
        HttpURLConnection connection =
                (HttpURLConnection) new URL(url).openConnection();
        connection.setConnectTimeout(10000);
        connection.setReadTimeout(15000);
        // GitHub rejects requests without a User-Agent header.
        connection.setRequestProperty("User-Agent", "ArXistant-Android");
        try {
            if (connection.getResponseCode() != 200) {
                throw new RuntimeException("GitHub returned HTTP "
                        + connection.getResponseCode());
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
