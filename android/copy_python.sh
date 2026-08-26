#!/bin/sh
# Copy the ArXistant Python sources into the Chaquopy app's Python directory.
# Run from the repository root, or from anywhere (it resolves paths itself).
set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
REPO_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)
DEST="$SCRIPT_DIR/app/src/main/python"

mkdir -p "$DEST"

for f in "$REPO_ROOT"/src/*.py; do
    cp "$f" "$DEST/"
done

echo "Copied src/*.py -> $DEST"

# The Chat page is desktop-oriented and will be redesigned for the phone
# later; keep it out of the Android build. Idempotent.
python3 "$SCRIPT_DIR/strip_chat.py"

echo "Re-run this script after changing the Python sources."
