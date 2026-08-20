# ArXistant Android app (standalone)

This is an Android app that runs the **full ArXistant pipeline on the phone** —
fetching arXiv, ML ranking, the SQLite paper database, and Nutstore sync —
with no external server. It embeds the existing Python server (via
[Chaquopy](https://chaquo.com/chaquopy/)) and renders the pages in a WebView.

See `../docs/android.md` for the full setup, build, and test guide.

## Layout

```
android/
├── settings.gradle, build.gradle, gradle.properties
├── copy_python.sh               # copies ../src/*.py into the app's Python dir
└── app/
    ├── build.gradle             # Android + Chaquopy; pip install numpy scikit-learn
    └── src/main/
        ├── AndroidManifest.xml
        ├── java/com/arxistant/app/
        │   ├── MainActivity.java   # WebView -> http://127.0.0.1:8765
        │   ├── ServerService.java  # foreground service that starts Python
        │   └── SecretStore.java    # Android Keystore-backed secret backend
        └── python/
            ├── android_bootstrap.py
            └── (copied ArXistant sources)
```

## Quick start

1. Install JDK 17 and the Android SDK.
2. Run `./copy_python.sh` (from anywhere) to sync the Python sources.
3. Open this `android/` directory in Android Studio and build the `app` module.
4. Install the resulting APK on a phone (or an emulator) and launch it.
