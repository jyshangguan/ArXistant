#!/usr/bin/env python3
"""Remove desktop-only features from the Android Python copy.

The Android app renders the server's pages in a WebView, so the shared
server code carries a few desktop-only surfaces that must not appear (or
must not break) on the phone. Run this AFTER copy_python.sh; it is
idempotent. It strips, from app/src/main/python/arxiv_db_server.py only:

  * the Chat page UI — the "Chat" entry in the "..." overflow menu, the
    injected per-paper chat-link (💬) buttons, and the /chat.html route.
    The chat API endpoints and CHAT_PAGE_HTML stay in place but become
    unreachable from the UI; the sync schema (incl. highlights) is
    untouched so libraries still merge cleanly with desktop. The 💬
    "Chat" chips on Notebook entries are removed for the same reason.
  * the manuscript review assistant — manuscript_service(), the
    /api/manuscripts endpoints, and the /assets/manuscripts.js route.
    The feature is desktop-only: it needs the Chat page, PyMuPDF, and
    the src/skills + src/assets directories, none of which ship in the
    APK (copy_python.sh also skips arxistant_manuscripts.py itself).
  * the browser tab icons — the favicon <link> injection
    (ensure_favicon_link) and the /favicon.svg, /favicon.ico,
    /apple-touch-icon.png routes. The app has a native launcher icon and
    the WebView has no tab bar, so the assets are not bundled.

Everything else — including the Notebook page, its /api/notebook*
endpoints, and its 📓 buttons — is phone-compatible and is left in place.

After stripping, the script verifies that no forbidden reference remains
(and that the Notebook did make it into the copy) and exits non-zero
otherwise, so a changed desktop source fails the build loudly instead of
silently leaking a stripped feature or shipping a stale copy.
"""

import os
import re
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "app", "src", "main", "python", "arxiv_db_server.py")

# (label, pattern, replacement). Exact chunks match one known spelling;
# regex chunks tolerate edits inside a block while keeping its boundaries.
STRIP_RULES = [
    # ── Chat UI ─────────────────────────────────────────────────────────
    ("chat menu item", re.compile(
        r"^        \{ icon: '💬', label: 'Chat', href: '/chat\.html', "
        r"desc: 'Read and discuss your papers with an LLM' \},\n", re.M), ""),
    # Any page's serve-time chat-button injection (daily/recent use a
    # deeper indent than search.html, so match both).
    ("chat-link injection", re.compile(
        r"[ \t]+if '<!-- chat-link-embedded -->' not in html:\n"
        r"[ \t]+html = html\.replace\('</body>', CHAT_LINK_SCRIPT \+ '</body>'\)\n"), ""),
    ("chat route (with manuscripts.js)", re.compile(
        r'^        elif path == "/chat\.html":\n'
        r"            self\._send_html\(CHAT_PAGE_HTML\.replace\('</body>',\n"
        r"                '<script src=\"/assets/manuscripts\.js\"></script></body>'\)\)\n", re.M), ""),
    ("chat route (plain)", re.compile(
        r'^        elif path == "/chat\.html":\n'
        r"            self\._send_html\(CHAT_PAGE_HTML\)\n", re.M), ""),
    # Notebook entries: drop the 💬 Chat chips (their route is gone above).
    ("notebook chat-chip CSS", re.compile(
        r"^    \.chat-chip \{ background: #555; color: white; border-radius: 4px; "
        r"padding: 2px 9px; font-size: 0\.75em; text-decoration: none; "
        r"white-space: nowrap; \}\n"
        r"    \.chat-chip:hover \{ background: #333; \}\n", re.M), ""),
    ("notebook chat-chip button", re.compile(
        r"^      const chatLink = n\.url && !isArxivId\(n\.paper_id\) \? '' :\n"
        r"        '<a class=\"chat-chip\" href=\"/chat\.html\?paper=' \+ "
        r"escapeAttr\(n\.paper_id\) \+ '\">💬 Chat</a>';\n", re.M),
     "      const chatLink = '';\n"),

    # ── Manuscript review assistant ─────────────────────────────────────
    # Module top: the service lock/registry plus the favicon <link> tuple,
    # ensure_favicon_link(), and manuscript_service() live in one block.
    ("manuscript service + favicon helpers", re.compile(
        r"^MANUSCRIPT_SERVICE_LOCK = threading\.Lock\(\)\n"
        r".*?^        return MANUSCRIPT_SERVICES\[DB_PATH\]\n", re.M | re.S), ""),
    ("manuscript GET/state/export route", re.compile(
        r'^        if path in \("/api/manuscripts", "/api/manuscripts/state", '
        r'"/api/manuscripts/export"\):\n'
        r".*?\n            return\n\n", re.M | re.S), ""),
    ("manuscripts.js asset route", re.compile(
        r'        if path == "/assets/manuscripts\.js":\n'
        r".*?\n            return\n\n", re.S), ""),
    ("manuscript POST size guard", re.compile(
        r'        if path == "/api/manuscripts" and content_length > 256000:\n'
        r".*?\n            return\n", re.S), ""),
    ("manuscript POST route", re.compile(
        r'        if path == "/api/manuscripts":\n            try:\n'
        r".*?\n            return\n\n", re.S), ""),
    ("manuscript delete hook", re.compile(
        r"            try:\n"
        r"                manuscript_service\(\)\.delete_document\(document_id\)\n"
        r"            except ValueError as exc:\n"
        r"                self\._send_json\(\{\"success\": False, \"error\": "
        r"str\(exc\)\}, 409\)\n"
        r"                return\n"), ""),

    # ── Browser tab icons ───────────────────────────────────────────────
    ("ASSETS_DIR definition", re.compile(
        r"^ASSETS_DIR = os\.path\.join\(os\.path\.dirname\(__file__\), \"assets\"\)\n",
        re.M), ""),
    ("favicon link injection in _send_html", re.compile(
        r"^        html = ensure_favicon_link\(html\)\n", re.M), ""),
    ("favicon routes", re.compile(
        r'        if path in \("/favicon\.svg", "/favicon\.ico", '
        r'"/apple-touch-icon\.png"\):\n'
        r".*?\n            return\n\n", re.S), ""),
]

# The Android copy must not contain any of these after stripping.
FORBIDDEN_MARKERS = [
    "manuscript_service", "MANUSCRIPT_SERVICE", "/api/manuscripts",
    "arxistant_manuscripts", "manuscripts.js",
    "ensure_favicon_link", "FAVICON_LINK_HTML", "ASSETS_DIR",
    "/favicon.", "apple-touch-icon",
    "chat-link-embedded -->' not in html",
    'elif path == "/chat.html"',
    "{ icon: '💬', label: 'Chat'",
    "chat-chip",
]

# And it must contain these (phone-compatible) features.
REQUIRED_MARKERS = [
    "/api/notebook",        # Notebook endpoints made it into the copy
    "notebook_folders",     # ... and the DB schema with them
]


def main():
    with open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()

    changed = False
    for label, pattern, replacement in STRIP_RULES:
        src, n = pattern.subn(replacement, src)
        if n:
            print(f"removed {n}x {label}")
            changed = True
        else:
            print(f"already absent: {label}")

    leftovers = [m for m in FORBIDDEN_MARKERS if m in src]
    missing = [m for m in REQUIRED_MARKERS if m not in src]
    if leftovers or missing:
        if leftovers:
            print("ERROR: desktop-only code remains in the Android copy:")
            for m in leftovers:
                print(f"  - {m}")
        if missing:
            print("ERROR: phone feature missing from the Android copy "
                  "(re-run copy_python.sh after updating src/):")
            for m in missing:
                print(f"  - {m}")
        return 1

    if changed:
        with open(TARGET, "w", encoding="utf-8") as f:
            f.write(src)
        print("Android arxiv_db_server.py updated (desktop-only features stripped).")
    else:
        print("Nothing to do.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
