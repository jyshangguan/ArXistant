package com.arxistant.app;

import android.app.Activity;
import android.content.Intent;
import android.os.Bundle;
import android.webkit.WebSettings;
import android.webkit.WebView;
import android.webkit.WebViewClient;

/** Launcher screen: starts the embedded Python server and shows the daily page. */
public class MainActivity extends Activity {
    @Override
    protected void onCreate(Bundle savedInstanceState) {
        super.onCreate(savedInstanceState);

        // Start (or keep alive) the foreground service that hosts the Python
        // server on 127.0.0.1:8765.
        startService(new Intent(this, ServerService.class));

        WebView webView = new WebView(this);
        WebSettings settings = webView.getSettings();
        settings.setJavaScriptEnabled(true);
        settings.setDomStorageEnabled(true);
        settings.setAllowFileAccess(true);
        // Keep navigation inside the WebView so the save-button JavaScript
        // works against the local server.
        webView.setWebViewClient(new WebViewClient());

        webView.loadUrl("http://127.0.0.1:8765/daily.html");
        setContentView(webView);
    }
}
