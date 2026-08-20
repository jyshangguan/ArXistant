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
echo "Re-run this script after changing the Python sources."
