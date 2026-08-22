#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd -P)"
TARGET="$ROOT/Daisy.app"
STAGE="$(mktemp -d "${TMPDIR:-/tmp}/daisy-build.XXXXXX")"
APP="$STAGE/Daisy.app"
CONTENTS="$APP/Contents"
RESOURCES="$CONTENTS/Resources"

cleanup() { rm -rf "$STAGE"; }
trap cleanup EXIT

mkdir -p "$CONTENTS/MacOS" "$RESOURCES"

swiftc -O \
  -o "$CONTENTS/MacOS/Daisy" \
  "$ROOT/app/main.swift" \
  -framework Cocoa -framework WebKit

cp "$ROOT/app/Info.plist" "$CONTENTS/Info.plist"
cp "$ROOT/index.html" "$RESOURCES/index.html"
cp "$ROOT/icon/AppIcon.icns" "$RESOURCES/AppIcon.icns"
cp "$ROOT/labctl.py" "$RESOURCES/labctl.py"

for package in agents garden commons port scrape obs hardware taste precedent lab; do
  rsync -a \
    --exclude '__pycache__' \
    --exclude 'test_*.py' \
    --exclude '*.pyc' \
    --exclude '*.db' \
    --exclude 'spool' \
    "$ROOT/$package/" "$RESOURCES/$package/"
done

plutil -lint "$CONTENTS/Info.plist" >/dev/null
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict "$APP"

# Synchronise an explicit app target from a disposable staging directory. The
# delete applies only inside Daisy.app and prevents a second build retaining a
# stale resource that the current source no longer ships.
mkdir -p "$TARGET"
rsync -a --delete "$APP/" "$TARGET/"
codesign --verify --deep --strict "$TARGET"

required=(
  "$TARGET/Contents/MacOS/Daisy"
  "$TARGET/Contents/Resources/index.html"
  "$TARGET/Contents/Resources/AppIcon.icns"
  "$TARGET/Contents/Resources/agents/discover.py"
  "$TARGET/Contents/Resources/garden/link.py"
  "$TARGET/Contents/Resources/port/client.py"
  "$TARGET/Contents/Resources/scrape/cli.py"
  "$TARGET/Contents/Resources/obs/otlp.py"
)
for path in "${required[@]}"; do
  test -f "$path" || { printf 'missing bundle resource: %s\n' "$path" >&2; exit 1; }
done

printf 'built and ad-hoc signed %s\n' "$TARGET"
