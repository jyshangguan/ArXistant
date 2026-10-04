#!/bin/sh
# Copy the ArXistant Python sources into the Chaquopy app's Python directory.
# Run from the repository root, or from anywhere (it resolves paths itself).
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
DEST="$SCRIPT_DIR/app/src/main/python"

mkdir -p "$DEST"

for f in "$REPO_ROOT"/src/*.py; do
    name=$(basename "$f")
    case "$name" in
        # Desktop-only: the manuscript review assistant needs the Chat
        # page, PyMuPDF, and the src/skills + src/assets directories, none
        # of which ship in the APK. strip_chat.py removes its endpoints
        # from the server copy as well.
        arxistant_manuscripts.py|arxistant_pdf_annotations.py) ;;
        *) cp "$f" "$DEST/" ;;
    esac
done
# Also drop stale copies from before the exclusion (idempotent).
rm -f "$DEST/arxistant_manuscripts.py" "$DEST/arxistant_pdf_annotations.py"

echo "Copied src/*.py -> $DEST"

# Desktop-only UI surfaces (the Chat page, the manuscript review assistant,
# and the browser tab icons) are removed from the Android copy. Idempotent,
# and verifies that nothing stripped leaked through before succeeding.
python3 "$SCRIPT_DIR/strip_chat.py"

echo "Re-run this script after changing the Python sources."
