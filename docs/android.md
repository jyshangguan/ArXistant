---
layout: default
title: Android app
description: Run ArXistant standalone on Android (arXiv fetch, ML ranking, and Nutstore sync on the phone).
nav_order: 4
---

# ArXistant Android app (standalone)

The `android/` directory contains an Android app that runs the **complete
ArXistant pipeline on the phone**: fetching arXiv, ranking with the ML model,
the SQLite paper database, and Nutstore cloud sync. It does not need a remote
server or a desktop Mac.

## How it works

The app embeds the existing Python server with [Chaquopy](https://chaquo.com/chaquopy/),
which runs CPython (plus NumPy and scikit-learn) inside the Android process:

```text
[Android app]
  ├─ ServerService (foreground service)
  │     └─ Chaquopy Python runtime
  │           └─ arxiv_db_server.run_server() on 127.0.0.1:8765
  │                ├─ fetch arXiv (urllib)
  │                ├─ ML rank (numpy + scikit-learn)
  │                ├─ SQLite paper DB (app-private storage)
  │                └─ Nutstore WebDAV sync
  └─ MainActivity
        └─ WebView -> http://127.0.0.1:8765/daily.html
```

The WebView renders the same HTML pages the server already generates, and the
existing save buttons keep working because they `POST /api/...` to the embedded
server. The ML model is **not** synced; the phone trains its own model from the
Nutstore-synced saved papers, exactly like a second desktop install.

## Python-side changes that make this possible

Three small changes were made to the shared Python code (all backwards
compatible with the desktop app):

1. **In-process tasks** — `src/arxistant_tasks.py` runs training, feature-page
   generation, and daily/recent refresh. On desktop they still run as
   subprocesses; on Android (`ARXISTANT_IN_PROCESS=1`) they import the modules
   and call the functions directly, because Chaquopy cannot spawn Python
   subprocesses.
2. **Pluggable secret store** — `src/arxistant_secrets.py` accepts a custom
   backend via `set_backend(...)`. The Android app supplies a Keystore-backed
   backend (`SecretStore.java`) so the Nutstore app password is encrypted, not
   stored in plaintext.
3. **Configurable bind address** — `run_server()` honors `ARXISTANT_BIND`
   (default `localhost`); the Android bootstrap sets it to `127.0.0.1`.

## Prerequisites

- **JDK 17+** (Android Gradle Plugin 8.x requires it):
  `brew install openjdk@17`
- **Android SDK** (Android Studio, or the command-line tools).
- The Chaquopy Gradle plugin (declared in `android/build.gradle`).

## Build

```bash
cd android
./copy_python.sh        # copy ../src/*.py into app/src/main/python/

# Then either:
#   - open the android/ directory in Android Studio and build the app module, or
#   - run: gradle :app:assembleDebug
```

The first build downloads Chaquopy, NumPy, and scikit-learn wheels for the
target ABIs, so it is slow and produces a large APK (scikit-learn is the bulk
of it).

## Install and run

- **Phone:** install the debug APK (`app/build/outputs/apk/debug/app-debug.apk`)
  and launch it. The daily page appears in the WebView after the server starts.
- **Emulator:** use an arm64 system image on Apple Silicon. The
  `x86_64` ABI filter is included so x86_64 emulators also work.

## Current limitations (to be aware of)

- **No daily reminders yet.** The desktop Chrome extension's alarm/notification
  logic has no Android equivalent. A `WorkManager` job that calls
  `/api/refresh-daily` and posts a notification is the intended follow-up.
- **The server is single-threaded.** A long daily refresh blocks other requests
  while it runs (same behavior as the desktop server during a refresh).
- **First launch** generates the daily page by fetching arXiv, which needs a
  network connection and takes a few seconds.
- **Secret store** requires an Android Keystore key; the `SecretStore.java`
  example uses AES/GCM with the key held in the Keystore.

## Nutstore sync on the phone

Cloud sync is configured exactly as on desktop: open the WebView's Settings
(Options) page, choose **Nutstore WebDAV (坚果云)**, enter your email and the
第三方应用密码, and click **Connect**. The app password is stored encrypted via
the Keystore backend.
