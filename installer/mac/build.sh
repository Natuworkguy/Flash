#!/bin/bash
# Builds "Install Flash.app" from InstallFlash.swift, with install.sh and the setup page inside it.
#     installer/mac/build.sh [OUT_DIR]          (needs Xcode or Apple's Command Line Tools: swiftc)
# Add --dev to make one that installs the dev branch.
set -euo pipefail
HERE="$(cd "$(dirname "$0")" && pwd)"
ROOT="$(cd "$HERE/../.." && pwd)"
OUT="${1:-$ROOT/dist}"
[ "${1:-}" = "--dev" ] && OUT="$ROOT/dist"
DEV=""; for a in "$@"; do [ "$a" = "--dev" ] && DEV=1; done
APP="$OUT/Install Flash.app"
rm -rf "$APP"
mkdir -p "$APP/Contents/MacOS" "$APP/Contents/Resources/window"
swiftc -O -o "$APP/Contents/MacOS/Install Flash" "$HERE/InstallFlash.swift" -framework Cocoa -framework WebKit
cp "$ROOT/install.sh" "$APP/Contents/Resources/install.sh"
cp "$HERE/askpass.sh" "$APP/Contents/Resources/askpass.sh"
chmod +x "$APP/Contents/Resources/askpass.sh"
cp "$ROOT/installer/window/index.html" "$ROOT/installer/window/film.mp4" "$APP/Contents/Resources/window/"
[ -n "$DEV" ] && touch "$APP/Contents/Resources/dev"
cat > "$APP/Contents/Info.plist" <<PLIST
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>CFBundleName</key><string>Install Flash</string>
  <key>CFBundleDisplayName</key><string>Install Flash</string>
  <key>CFBundleIdentifier</key><string>dev.flash.installer</string>
  <key>CFBundleExecutable</key><string>Install Flash</string>
  <key>CFBundlePackageType</key><string>APPL</string>
  <key>CFBundleShortVersionString</key><string>1.0</string>
  <key>LSMinimumSystemVersion</key><string>12.0</string>
  <key>NSHighResolutionCapable</key><true/>
</dict></plist>
PLIST
# Ad-hoc signed, so it opens on Apple silicon (right-click > Open the first time, as for any app not from the store).
codesign --force --deep -s - "$APP" >/dev/null 2>&1 || true
( cd "$OUT" && rm -f "Install Flash.zip" && ditto -c -k --keepParent "Install Flash.app" "Install Flash.zip" ) 2>/dev/null || true
echo "$APP"
