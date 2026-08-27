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

import androidx.core.content.FileProvider;

import org.json.JSONArray;
import org.json.JSONObject;

import java.io.File;
import java.io.FileOutputStream;
import java.io.InputStream;
import java.io.OutputStream;
import java.net.HttpURLConnection;
import java.net.URL;

/**
 * Self-update flow for the Android app.
 *
 * Checks the GitHub release page of the project for a newer version, offers
 * to download the release APK, and hands it to the system package installer.
 * Triggered from the "..." menu ("Check for Updates", via the JS bridge) and
 * silently once at startup.
 */
public class UpdateChecker {
    private static final String TAG = "ArxistantUpdate";
    private static final String RELEASE_API_URL =
            "https://api.github.com/repos/jyshangguan/ArXistant/releases/latest";
    private static final String UPDATE_FILE_NAME = "arxistant-update.apk";

    private final Activity activity;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private File pendingApk;
    private AlertDialog progressDialog;

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
                if (compareVersions(latest, current) <= 0) {
                    if (manual) showInfo("ArXistant is up to date (v" + current + ").");
                    return;
                }
                String apkUrl = findApkAsset(release);
                if (apkUrl == null) {
                    if (manual) showError("Version " + latest
                            + " is available, but the release has no APK asset. "
                            + "Download it manually from the GitHub release page.");
                    return;
                }
                mainHandler.post(() -> offerUpdate(latest, apkUrl));
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

    private static String findApkAsset(JSONObject release) {
        JSONArray assets = release.optJSONArray("assets");
        if (assets == null) return null;
        for (int i = 0; i < assets.length(); i++) {
            JSONObject asset = assets.optJSONObject(i);
            if (asset == null) continue;
            String name = asset.optString("name", "");
            if (name.endsWith(".apk")) {
                return asset.optString("browser_download_url", null);
            }
        }
        return null;
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

    private void offerUpdate(String latest, String apkUrl) {
        new AlertDialog.Builder(activity)
                .setTitle("Update available")
                .setMessage("ArXistant v" + latest + " is available "
                        + "(installed: v" + BuildConfig.VERSION_NAME
                        + "). Download and install it now?")
                .setPositiveButton("Download", (d, w) -> downloadAndInstall(apkUrl))
                .setNegativeButton(android.R.string.cancel, null)
                .show();
    }

    private void downloadAndInstall(String url) {
        showProgress();
        new Thread(() -> {
            try {
                File dir = new File(activity.getCacheDir(), "update");
                if (!dir.exists() && !dir.mkdirs()) {
                    throw new RuntimeException("Could not create download directory");
                }
                File apk = new File(dir, UPDATE_FILE_NAME);
                HttpURLConnection connection =
                        (HttpURLConnection) new URL(url).openConnection();
                connection.setConnectTimeout(10000);
                connection.setReadTimeout(60000);
                connection.setRequestProperty("User-Agent", "ArXistant-Android");
                connection.setInstanceFollowRedirects(true);
                try {
                    if (connection.getResponseCode() != 200) {
                        throw new RuntimeException("Download failed: HTTP "
                                + connection.getResponseCode());
                    }
                    try (InputStream in = connection.getInputStream();
                         OutputStream out = new FileOutputStream(apk)) {
                        byte[] buffer = new byte[16384];
                        int n;
                        while ((n = in.read(buffer)) > 0) {
                            out.write(buffer, 0, n);
                        }
                    }
                } finally {
                    connection.disconnect();
                }
                mainHandler.post(() -> {
                    hideProgress();
                    installApk(apk);
                });
            } catch (Exception e) {
                Log.w(TAG, "Update download failed", e);
                mainHandler.post(() -> {
                    hideProgress();
                    showError("Download failed: " + e.getMessage());
                });
            }
        }, "arxistant-update-download").start();
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

    private void showProgress() {
        mainHandler.post(() -> {
            ProgressBar bar = new ProgressBar(activity);
            bar.setIndeterminate(true);
            bar.setPadding(48, 24, 48, 8);
            progressDialog = new AlertDialog.Builder(activity)
                    .setTitle("ArXistant")
                    .setMessage("Downloading update…")
                    .setView(bar)
                    .setCancelable(false)
                    .show();
        });
    }

    private void hideProgress() {
        if (progressDialog != null) {
            progressDialog.dismiss();
            progressDialog = null;
        }
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
