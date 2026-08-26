#!/usr/bin/env python3
"""Remove the (desktop-oriented) Chat page from the Android Python copy.

The Chat page will be redesigned for the phone later, so the Android build
should not surface it now. Run this AFTER copy_python.sh; it is idempotent.

It strips, from app/src/main/python/arxiv_db_server.py only:
  * the "Chat" entry in the "..." overflow menu,
  * the injected per-paper chat-link (💬) buttons,
  * the /chat.html route.
The chat API endpoints and CHAT_PAGE_HTML string are left in place but become
unreachable from the UI; the sync schema (incl. highlights) is untouched so
libraries still merge cleanly with desktop.
"""

import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
TARGET = os.path.join(HERE, "app", "src", "main", "python", "arxiv_db_server.py")

MENU_ITEM = (
    "        { icon: '💬', label: 'Chat', href: '/chat.html', "
    "desc: 'Read and discuss your papers with an LLM' },\n"
)

CHAT_LINK_INJECT = (
    "                if '<!-- chat-link-embedded -->' not in html:\n"
    "                    html = html.replace('</body>', CHAT_LINK_SCRIPT + '</body>')\n"
)

CHAT_ROUTE = (
    "        elif path == \"/chat.html\":\n"
    "            self._send_html(CHAT_PAGE_HTML)\n"
)


def main():
    with open(TARGET, "r", encoding="utf-8") as f:
        src = f.read()

    changed = False
    for label, chunk in (("menu item", MENU_ITEM),
                         ("chat-link injection", CHAT_LINK_INJECT),
                         ("/chat.html route", CHAT_ROUTE)):
        if chunk in src:
            count = src.count(chunk)
            src = src.replace(chunk, "")
            print(f"removed {count}x {label}")
            changed = True
        else:
            print(f"already absent: {label}")

    if changed:
        with open(TARGET, "w", encoding="utf-8") as f:
            f.write(src)
        print("Android arxiv_db_server.py updated (chat UI stripped).")
    else:
        print("Nothing to do.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
