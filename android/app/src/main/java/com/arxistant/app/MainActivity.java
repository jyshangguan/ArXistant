package com.arxistant.app;

import android.app.Activity;
import android.app.AlertDialog;
import android.content.Intent;
import android.os.Bundle;
import android.os.Handler;
import android.os.Looper;
import android.os.SystemClock;
import android.speech.tts.TextToSpeech;
import android.speech.tts.UtteranceProgressListener;
import android.speech.tts.Voice;
import android.webkit.JavascriptInterface;
import android.webkit.JsResult;
import android.webkit.WebChromeClient;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

import org.json.JSONArray;
import org.json.JSONObject;

import java.net.HttpURLConnection;
import java.net.URL;
import java.util.Locale;

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

    // Native TTS engine for the pages' Listen (voice digest) feature. The
    // WebView has no speechSynthesis, so the injected page script drives the
    // engine through the ArxistantAndroid bridge below.
    private TextToSpeech tts;
    private volatile boolean ttsReady = false;

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
        // Bridge used by the pages' "..." menu (Check for Updates), to
        // expose the app version to JavaScript, and to give the Daily page's
        // Listen feature a native voice.
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

        initTts();

        // Silent, best-effort update check at startup; only speaks up when a
        // newer release exists.
        updateChecker.check(false);
    }

    /** Start the native TTS engine used by the Listen (voice digest) feature. */
    private void initTts() {
        try {
            tts = new TextToSpeech(getApplicationContext(), status -> {
                ttsReady = (status == TextToSpeech.SUCCESS);
                if (ttsReady) {
                    try {
                        tts.setLanguage(Locale.US);
                    } catch (Exception ignored) {
                    }
                    tts.setOnUtteranceProgressListener(new UtteranceProgressListener() {
                        @Override
                        public void onStart(String utteranceId) {
                        }

                        @Override
                        public void onDone(String utteranceId) {
                            notifyTtsDone(utteranceId, false);
                        }

                        @Override
                        public void onError(String utteranceId) {
                            // Also fired when stop() interrupts an utterance
                            // (Skip/Stop/Pause); the page drops those by id.
                            notifyTtsDone(utteranceId, true);
                        }
                    });
                }
            });
        } catch (Exception e) {
            ttsReady = false;
        }
    }

    /**
     * Report an utterance completion to the page. The page script registers
     * window.__arxTtsOnEnd / window.__arxTtsOnError and chains the next
     * sentence chunk from there; utterance ids are numeric tokens generated
     * by the page and echoed back so stale events are ignored.
     */
    private void notifyTtsDone(String utteranceId, boolean error) {
        if (utteranceId == null || !utteranceId.matches("\\d+")) {
            return;
        }
        String call = "window.__arxTtsOn" + (error ? "Error" : "End") + "(" + utteranceId + ")";
        SwipeBackWebView wv = webView;
        if (wv != null) {
            // evaluateJavascript must run on the UI thread; the TTS
            // callbacks arrive on a binder thread.
            wv.post(() -> wv.evaluateJavascript(call, null));
        }
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

    @Override
    protected void onDestroy() {
        // Release the TTS engine; the Listen feature cannot speak after
        // this (the activity would be recreated with a fresh engine).
        if (tts != null) {
            try {
                tts.stop();
            } catch (Exception ignored) {
            }
            try {
                tts.shutdown();
            } catch (Exception ignored) {
            }
            tts = null;
        }
        ttsReady = false;
        super.onDestroy();
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

        // ── Voice reading (Listen) bridge ─────────────────────────────────
        // The WebView has no speechSynthesis, so the Daily/Recent pages'
        // Listen button drives the Android TTS engine through these
        // methods. The page reports each voice as {name, lang} (from
        // ttsVoices) to resolve its voice role (system default / man /
        // woman) locally, then speaks sentence chunks one at a time:
        // ttsSpeak returns immediately and the completion is signaled by a
        // window.__arxTtsOnEnd(id) / __arxTtsOnError(id) callback, with the
        // same numeric id the page passed in.

        @JavascriptInterface
        public boolean ttsAvailable() {
            return ttsReady;
        }

        @JavascriptInterface
        public String ttsVoices() {
            if (!ttsReady || tts == null) {
                return "[]";
            }
            try {
                JSONArray arr = new JSONArray();
                for (Voice v : tts.getVoices()) {
                    JSONObject o = new JSONObject();
                    o.put("name", v.getName());
                    o.put("lang", v.getLocale().toLanguageTag());
                    arr.put(o);
                }
                return arr.toString();
            } catch (Exception e) {
                return "[]";
            }
        }

        /**
         * Speak one chunk of text. {@code voiceName} selects the engine
         * voice by name (empty = the engine's default for US English);
         * {@code rate} is a 0.5–2.0 multiplier; {@code utteranceId} is the
         * numeric id echoed back on completion.
         */
        @JavascriptInterface
        public boolean ttsSpeak(String voiceName, String text, String rate, String utteranceId) {
            if (!ttsReady || tts == null || text == null || text.isEmpty()) {
                return false;
            }
            try {
                float r;
                try {
                    r = Float.parseFloat(rate);
                } catch (NumberFormatException e) {
                    r = 1f;
                }
                tts.setSpeechRate(Math.max(0.5f, Math.min(2f, r)));
                boolean matched = false;
                if (voiceName != null && !voiceName.isEmpty()) {
                    for (Voice v : tts.getVoices()) {
                        if (voiceName.equals(v.getName())) {
                            tts.setVoice(v);
                            matched = true;
                            break;
                        }
                    }
                }
                if (!matched) {
                    // No (or empty) voice name: fall back to plain US English
                    // with the engine's default voice.
                    tts.setLanguage(Locale.US);
                }
                tts.speak(text, TextToSpeech.QUEUE_FLUSH, new Bundle(), utteranceId);
                return true;
            } catch (Exception e) {
                return false;
            }
        }

        @JavascriptInterface
        public void ttsStop() {
            if (tts != null) {
                try {
                    tts.stop();
                } catch (Exception ignored) {
                }
            }
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
