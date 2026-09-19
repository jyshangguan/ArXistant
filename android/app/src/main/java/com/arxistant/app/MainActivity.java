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
    // True when the last init attempt FAILED (typically: no text-to-speech
    // engine installed or enabled on the device — common on Xiaomi/MIUI).
    // Distinct from "still initializing": the page shows different guidance.
    private volatile boolean ttsInitError = false;
    // Throttles re-init attempts so polling cannot spin the engine.
    private volatile long lastTtsInitAt = 0L;

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

    /**
     * (Re)start the native TTS engine used by the Listen (voice digest)
     * feature. Always called on the main thread. Safe to call again after a
     * failed init — e.g. after the user installed/enabled an engine in the
     * system settings — because the old instance is shut down first.
     */
    private void initTts() {
        lastTtsInitAt = SystemClock.uptimeMillis();
        ttsInitError = false;
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
        try {
            tts = new TextToSpeech(getApplicationContext(), status -> {
                ttsReady = (status == TextToSpeech.SUCCESS);
                ttsInitError = (status != TextToSpeech.SUCCESS);
                // The callback can run before the constructor assigns the
                // field, so re-read it instead of capturing; guard everything.
                TextToSpeech engine = tts;
                if (ttsReady && engine != null) {
                    try {
                        engine.setLanguage(Locale.US);
                    } catch (Exception ignored) {
                    }
                    try {
                        engine.setOnUtteranceProgressListener(new UtteranceProgressListener() {
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
                    } catch (Exception ignored) {
                    }
                }
            });
        } catch (Exception e) {
            ttsReady = false;
            ttsInitError = true;
        }
    }

    /**
     * Best-effort engine recovery, called when the page finds the engine
     * unavailable. The first attempt may have failed because no engine was
     * installed/enabled yet (the user then fixed it in the system settings)
     * or because the bind silently stalled; a throttled re-init on the main
     * thread lets the next poll see a working engine without an app restart.
     */
    private void maybeReinitTts() {
        if (ttsReady) {
            return;
        }
        long now = SystemClock.uptimeMillis();
        // At most one attempt every 3s; also give a freshly started attempt
        // (whose onInit callback may still be pending) time to complete.
        if (now - lastTtsInitAt < 3000L) {
            return;
        }
        mainHandler.post(this::initTts);
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
        // The user may have just installed/enabled a TTS engine in the
        // system settings (the Listen panel offers a direct link); recover
        // without requiring an app restart.
        if (!ttsReady) {
            maybeReinitTts();
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
            if (ttsReady) {
                return true;
            }
            // Recovery hook: the engine may have become usable since the
            // last check (installed/enabled in settings, or a stalled bind).
            maybeReinitTts();
            return ttsReady;
        }

        /**
         * Why the engine is unavailable, for actionable guidance in the
         * Listen panel: "ok" (ready), "no_engine" (last init failed —
         * typically no TTS engine installed/enabled on the device), or
         * "starting" (initializing; worth waiting/retrying).
         */
        @JavascriptInterface
        public String ttsProblem() {
            if (ttsReady) {
                return "ok";
            }
            return ttsInitError ? "no_engine" : "starting";
        }

        /**
         * Open the system text-to-speech settings so the user can
         * install/enable an engine (e.g. "Speech Services by Google") —
         * the fix for the common no-engine case on Xiaomi and similar.
         */
        @JavascriptInterface
        public void openTtsSettings() {
            mainHandler.post(() -> {
                try {
                    startActivity(new Intent("com.android.settings.TTS_SETTINGS"));
                    return;
                } catch (Exception ignored) {
                }
                try {
                    startActivity(new Intent(android.provider.Settings.ACTION_SETTINGS));
                } catch (Exception ignored) {
                }
            });
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
         * numeric id echoed back on completion. Engine calls run on the main
         * thread (this method is invoked on the WebView's bridge thread);
         * if the state changes before the chunk is queued, a completion is
         * synthesized so the page's chunk chain can never stall.
         */
        @JavascriptInterface
        public boolean ttsSpeak(String voiceName, String text, String rate, String utteranceId) {
            if (!ttsReady || tts == null || text == null || text.isEmpty()) {
                return false;
            }
            mainHandler.post(() -> {
                TextToSpeech engine = tts;
                if (engine == null || !ttsReady) {
                    // Engine went away between the check and the post (e.g.
                    // re-init): report the chunk as errored so the page
                    // moves on instead of waiting forever.
                    notifyTtsDone(utteranceId, true);
                    return;
                }
                try {
                    float r;
                    try {
                        r = Float.parseFloat(rate);
                    } catch (NumberFormatException e) {
                        r = 1f;
                    }
                    engine.setSpeechRate(Math.max(0.5f, Math.min(2f, r)));
                    boolean matched = false;
                    if (voiceName != null && !voiceName.isEmpty()) {
                        for (Voice v : engine.getVoices()) {
                            if (voiceName.equals(v.getName())) {
                                engine.setVoice(v);
                                matched = true;
                                break;
                            }
                        }
                    }
                    if (!matched) {
                        // No (or empty) voice name: fall back to plain US
                        // English with the engine's default voice.
                        engine.setLanguage(Locale.US);
                    }
                    engine.speak(text, TextToSpeech.QUEUE_FLUSH, new Bundle(), utteranceId);
                } catch (Exception e) {
                    notifyTtsDone(utteranceId, true);
                }
            });
            return true;
        }

        @JavascriptInterface
        public void ttsStop() {
            mainHandler.post(() -> {
                TextToSpeech engine = tts;
                if (engine != null) {
                    try {
                        engine.stop();
                    } catch (Exception ignored) {
                    }
                }
            });
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
