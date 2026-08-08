#!/bin/sh

set -eu

SCRIPT_DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
PROJECT_ROOT=$(CDPATH= cd -- "$SCRIPT_DIR/../.." && pwd)
VERSION=${1:-0.1.1}

case "$VERSION" in
    *[!0-9A-Za-z.+~-]*|'')
        echo "Invalid Debian version: $VERSION" >&2
        exit 2
        ;;
esac

BUILD_ROOT=$(mktemp -d "${TMPDIR:-/tmp}/arxistant-deb.XXXXXX")
PACKAGE_ROOT="$BUILD_ROOT/arxistant_$VERSION"
trap 'rm -rf "$BUILD_ROOT"' EXIT HUP INT TERM

install -d \
    "$PACKAGE_ROOT/DEBIAN" \
    "$PACKAGE_ROOT/usr/bin" \
    "$PACKAGE_ROOT/usr/lib/arxistant/src" \
    "$PACKAGE_ROOT/usr/lib/systemd/user" \
    "$PACKAGE_ROOT/usr/share/applications" \
    "$PACKAGE_ROOT/usr/share/arxistant/chrome-extension/icons" \
    "$PROJECT_ROOT/dist"

for source_file in "$PROJECT_ROOT"/src/*.py; do
    install -m 0644 "$source_file" "$PACKAGE_ROOT/usr/lib/arxistant/src/"
done

for extension_file in background.js manifest.json options.html options.js popup.css popup.html popup.js; do
    install -m 0644 \
        "$PROJECT_ROOT/chrome-extension/$extension_file" \
        "$PACKAGE_ROOT/usr/share/arxistant/chrome-extension/$extension_file"
done
for icon_file in "$PROJECT_ROOT"/chrome-extension/icons/*.png; do
    install -m 0644 "$icon_file" "$PACKAGE_ROOT/usr/share/arxistant/chrome-extension/icons/"
done

install -m 0755 "$SCRIPT_DIR/arxistant-server" "$PACKAGE_ROOT/usr/bin/arxistant-server"
install -m 0755 "$SCRIPT_DIR/arxistant-url-handler" "$PACKAGE_ROOT/usr/bin/arxistant-url-handler"
install -m 0755 "$SCRIPT_DIR/arxistant-native-host" "$PACKAGE_ROOT/usr/lib/arxistant/arxistant-native-host"
install -m 0644 "$SCRIPT_DIR/arxistant.service" "$PACKAGE_ROOT/usr/lib/systemd/user/arxistant.service"
install -m 0644 "$SCRIPT_DIR/arxistant-handler.desktop" "$PACKAGE_ROOT/usr/share/applications/arxistant-handler.desktop"
install -m 0755 "$SCRIPT_DIR/postinst" "$PACKAGE_ROOT/DEBIAN/postinst"
install -m 0755 "$SCRIPT_DIR/prerm" "$PACKAGE_ROOT/DEBIAN/prerm"
sed "s/@VERSION@/$VERSION/g" "$SCRIPT_DIR/control.in" > "$PACKAGE_ROOT/DEBIAN/control"

OUTPUT="$PROJECT_ROOT/dist/arxistant_${VERSION}_all.deb"
if command -v dpkg-deb >/dev/null 2>&1; then
    dpkg-deb --root-owner-group --build "$PACKAGE_ROOT" "$OUTPUT"
elif command -v ar >/dev/null 2>&1 && tar --version 2>&1 | grep -qi bsdtar; then
    ARCHIVE_ROOT="$BUILD_ROOT/archive"
    install -d "$ARCHIVE_ROOT"
    printf '2.0\n' > "$ARCHIVE_ROOT/debian-binary"
    COPYFILE_DISABLE=1 tar --no-xattrs --uid 0 --gid 0 --uname root --gname root \
        -C "$PACKAGE_ROOT/DEBIAN" -czf "$ARCHIVE_ROOT/control.tar.gz" .
    COPYFILE_DISABLE=1 tar --no-xattrs --uid 0 --gid 0 --uname root --gname root \
        -C "$PACKAGE_ROOT" --exclude ./DEBIAN -czf "$ARCHIVE_ROOT/data.tar.gz" .
    rm -f "$OUTPUT"
    (cd "$ARCHIVE_ROOT" && ar -r "$OUTPUT" debian-binary control.tar.gz data.tar.gz)
else
    echo "Building requires dpkg-deb, or ar plus bsdtar." >&2
    exit 1
fi
echo "$OUTPUT"
