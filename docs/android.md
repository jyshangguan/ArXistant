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

## Download

Prebuilt, signed APKs are attached to each
[GitHub release](https://github.com/jyshangguan/ArXistant/releases/latest).
For v0.2.0 the direct download is:

<https://github.com/jyshangguan/ArXistant/releases/download/v0.2.0/arxistant-release.v0.2.0.apk>

Copy the APK to the phone and open it to install. If a debug build is already
installed, uninstall it first (the debug and release builds use different
signing keys). Building the APK from source is described under
[Build](#build).

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
  │                └─ Nutstore WebDAV sync (periodic auto-sync)
  └─ MainActivity
        └─ WebView -> http://127.0.0.1:8765/daily.html
```

The WebView renders the same HTML pages the server already generates, and the
existing save buttons keep working because they `POST /api/...` to the embedded
server. The ML model is **not** synced; the phone trains its own model from the
Nutstore-synced saved papers, exactly like a second desktop install.

## Mobile interface

Because the desktop pages were written for a wide browser window, the server
injects a small mobile layer (CSS + JavaScript) only when it detects an Android
WebView. It adds:

- **A floating ⋯ menu** (top-right) that replaces the desktop navigation bar.
  It links to Daily/Recent papers, Saved Papers, Search arXiv, My Publications,
  ML Features, and Cloud Sync. Tap ⋯ to open it, tap outside to dismiss. A
  **long-press on a menu item** shows a tooltip describing what it does.
- **Pull-to-refresh** on the daily and recent pages: drag down from the top to
  sync the library and re-fetch the list. A spinner overlay appears while the
  refresh runs (the page's own Refresh button is hidden on Android in favor of
  the gesture).
- **Sync-then-refresh**: a refresh first pulls the latest saved papers from
  Nutstore, then regenerates the ranked list, so the phone always reflects
  changes made on other devices.

The app icon is the ArXistant logo, and `MainActivity` shows a loading page and
polls the embedded server until it is ready, so the daily list appears as soon
as the server has started.

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

# Debug APK:
./gradlew :app:assembleDebug
#   -> app/build/outputs/apk/debug/app-debug.apk

# Release APK (signed; see "Release build" below):
./gradlew :app:assembleRelease
#   -> app/build/outputs/apk/release/arxistant-release.v0.2.0.apk
```

The first build downloads Chaquopy, NumPy, and scikit-learn wheels for the
target ABIs, so it is slow and produces a large APK (scikit-learn is the bulk
of it).

## Release build

The release APK is signed with a local keystore so it can be installed and
updated over the debug build. The signing material is **not** committed:

- `android/keystore/release.keystore` — the private signing key.
- `android/keystore.properties` — the store path, passwords, and key alias.

Both are listed in `android/.gitignore`. To create them from scratch:

```bash
cd android
mkdir -p keystore
keytool -genkeypair -v \
  -keystore keystore/release.keystore \
  -alias arxistant \
  -keyalg RSA -keysize 2048 -validity 10000 \
  -storepass <store-password> -keypass <key-password> \
  -dname "CN=ArXistant, OU=Personal, O=ArXistant, C=CN"
```

then write `keystore.properties`:

```properties
storeFile=keystore/release.keystore
storePassword=<store-password>
keyAlias=arxistant
keyPassword=<key-password>
```

`app/build.gradle` reads `keystore.properties` when present and applies it to
the `release` build type. Keep a backup of both files; without the same key you
cannot push an update over an existing install. The current Android release is
**v0.2.0** (`versionName "0.2.0"`, `versionCode 4` in `app/build.gradle`), matching the desktop release.

## Install and run

- **Phone:** install the release APK
  (`app/build/outputs/apk/release/arxistant-release.v0.2.0.apk`) or the debug APK
  (`app/build/outputs/apk/debug/app-debug.apk`) and launch it. The daily page
  appears in the WebView after the server starts.
- **Emulator:** use an arm64 system image on Apple Silicon. The
  `x86_64` ABI filter is included so x86_64 emulators also work.

## Nutstore sync on the phone

Cloud sync is configured exactly as on desktop: open the ⋯ menu and choose
**Cloud Sync** (or **☁️ Cloud Sync**), select **Nutstore WebDAV (坚果云)**, enter
your email and the 第三方应用密码, and click **Connect**. The app password is
stored encrypted via the Keystore backend.

Once connected, the phone syncs **automatically every 30 minutes** (the default
`interval_minutes`), and also syncs before every manual refresh. Syncing uses
the same last-write-wins versioned snapshot as desktop, so the phone and your
Mac stay in step.

## Current limitations (to be aware of)

- **No daily reminders yet.** The desktop Chrome extension's alarm/notification
  logic has no Android equivalent. A `WorkManager` job that calls
  `/api/refresh-daily` and posts a notification is the intended follow-up.
- **The server is single-threaded.** A long daily refresh blocks other requests
  while it runs (same behavior as the desktop server during a refresh).
- **First launch** generates the daily page by fetching arXiv, which needs a
  network connection and takes a few seconds.
- **Secret store** requires an Android Keystore key; the `SecretStore.java`
  backend uses AES/GCM with the key held in the Keystore.
