package com.arxistant.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.webkit.JavascriptInterface;
import android.webkit.JsResult;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import java.net.HttpURLConnection;
import java.net.URL;

/** Launcher screen: starts the embedded Python server and shows the daily page. */
public class MainActivity extends Activity implements SwipeBackWebView.OnSwipeBackListener {
    private static final String SERVER_URL = "http://127.0.0.1:8765";
    private static final int POLL_ATTEMPTS = 60;   // 60 * 500ms = up to 30s
    // Ignore a second back action (system + edge swipe) within this window so
    // a single gesture can never pop two pages off the history stack.
    private static final long BACK_DEBOUNCE_MS = 400;

    private SwipeBackWebView webView;
    private UpdateChecker updateChecker;
    private final Handler mainHandler = new Handler(Looper.getMainLooper());
    private long lastBackAt = 0;

    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Start (or keep alive) the foreground service that hosts the Python
        // server on 127.0.0.1:8765.
        startService(new Intent(this, ServerService.class));

        updateChecker = new UpdateChecker(this);

        webView = new SwipeBackWebView(this, this);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        // Keep navigation inside the WebView so the save-button JavaScript
        // works against the local server.
        webView.setWebViewClient(new WebViewClient());
        // Bridge used by the pages' "..." menu (Check for Updates) and to
        // expose the app version to JavaScript.
        webView.addJavascriptInterface(new AndroidBridge(), "ArxistantAndroid");
        // The pages use confirm() and alert(); without a WebChromeClient these
        // are silent no-ops in a WebView (confirm() returns false), which made
        // the Refresh and Delete buttons appear to do nothing.
        webView.setWebChromeClient(new WebChromeClient() {
            @Override
            public boolean onJsConfirm(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setTitle("ArXistant")
                        .setMessage(message)
                        .setPositiveButton(android.R.string.ok, (d, w) -> result.confirm())
                        .setNegativeButton(android.R.string.cancel, (d, w) -> result.cancel())
                        .setOnCancelListener(d -> result.cancel())
                        .show();
                return true;
            }

            @Override
            public boolean onJsAlert(WebView view, String url, String message, JsResult result) {
                new AlertDialog.Builder(MainActivity.this)
                        .setTitle("ArXistant")
                        .setMessage(message)
                        .setPositiveButton(android.R.string.ok, (d, w) -> result.confirm())
                        .setOnCancelListener(d -> result.confirm())
                        .show();
                return true;
            }
        });

        setContentView(webView);

        // Show a loading page until the server is reachable, then load the
        // daily page. This avoids the "webpage cannot be opened" error that
        // happens when the WebView loads before Python has finished starting.
        webView.loadDataWithBaseURL(null,
                "<html><body style='font-family:sans-serif;text-align:center;padding-top:40px;color:#666;'>"
                + "<h1 style='color:#b31b1b;'>ArXistant</h1><p>Starting server…</p></body></html>",
                "text/html", "utf-8", null);

        waitForServerThenLoad();

        // Silent, best-effort update check at startup; only speaks up when a
        // newer release exists.
        updateChecker.check(false);
    }

    @Override
    protected void onResume() {
        super.onResume();
        // If an update download was waiting for the "install unknown apps"
        // permission, launch the installer now that the user is back.
        if (updateChecker != null) {
            updateChecker.resumePendingInstall();
        }
    }

    /** System back (button or gesture navigation) pops the WebView history. */
    @Override
    public void onBackPressed() {
        if (webView != null && webView.canGoBack()) {
            goBackDebounced();
        } else {
            super.onBackPressed();
        }
    }

    /** Edge-swipe back gesture reported by {@link SwipeBackWebView}. */
    @Override
    public void onSwipeBack() {
        if (webView != null && webView.canGoBack()) {
            goBackDebounced();
        }
    }

    private void goBackDebounced() {
        long now = SystemClock.uptimeMillis();
        if (now - lastBackAt < BACK_DEBOUNCE_MS) {
            return;
        }
        lastBackAt = now;
        webView.goBack();
    }

    /** JavaScript bridge exposed to the pages as window.ArxistantAndroid. */
    private class AndroidBridge {
        @JavascriptInterface
        public String getVersion() {
            return BuildConfig.VERSION_NAME;
        }

        @JavascriptInterface
        public void checkForUpdate() {
            updateChecker.check(true);
        }
    }

    private void waitForServerThenLoad() {
        new Thread(() -> {
            for (int i = 0; i < POLL_ATTEMPTS; i++) {
                if (isServerReady()) {
                    mainHandler.post(() -> webView.loadUrl(SERVER_URL + "/daily.html"));
                    return;
                }
                try {
                    Thread.sleep(500);
                } catch (InterruptedException e) {
                    return;
                }
            }
            // Server never became ready (it may have crashed). Load anyway so
            // the WebView's own error page is shown.
            mainHandler.post(() -> webView.loadUrl(SERVER_URL + "/daily.html"));
        }, "arxistant-wait-server").start();
    }

    private boolean isServerReady() {
        HttpURLConnection connection = null;
        try {
            connection = (HttpURLConnection) new URL(SERVER_URL + "/api/health").openConnection();
            connection.setConnectTimeout(1000);
            connection.setReadTimeout(1000);
            int code = connection.getResponseCode();
            return code == 200;
        } catch (Exception e) {
            return false;
        } finally {
            if (connection != null) {
                connection.disconnect();
            }
        }
    }
}
