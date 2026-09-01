#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
: "${TARGET_ARCH:?TARGET_ARCH must be x86_64 or arm64}"
case "$TARGET_ARCH" in x86_64) SUFFIX="Intel-x86_64" ;; arm64) SUFFIX="Apple-Silicon-arm64" ;; *) echo "Unsupported TARGET_ARCH=$TARGET_ARCH"; exit 2 ;; esac
VERSION="v26.09.01.04"
export MACOSX_DEPLOYMENT_TARGET="13.0"
rm -rf build dist release
mkdir -p release
python -m PyInstaller --noconfirm --clean HV_P2P_NMS.spec
APP="dist/HV P2P NMS.app"
BIN="$APP/Contents/MacOS/HV P2P NMS"
test -d "$APP"
test -x "$BIN"
ACTUAL_ARCHS="$(lipo -archs "$BIN")"
echo "Built executable architectures: $ACTUAL_ARCHS"
echo "$ACTUAL_ARCHS" | tr ' ' '\n' | grep -Fx "$TARGET_ARCH" >/dev/null
# PyInstaller ad-hoc signs arm64 content; re-sign the final bundle after packaging transformations.
codesign --force --deep --sign - "$APP"
codesign --verify --deep --strict --verbose=2 "$APP"
"$BIN" --self-test
ZIP="release/HV_P2P_NMS_${VERSION}_macOS_${SUFFIX}.zip"
DMG="release/HV_P2P_NMS_${VERSION}_macOS_${SUFFIX}.dmg"
ditto -c -k --sequesterRsrc --keepParent "$APP" "$ZIP"
hdiutil create -quiet -volname "HV P2P NMS" -srcfolder "$APP" -ov -format UDZO "$DMG"
(
  cd release
  shasum -a 256 "$(basename "$ZIP")" "$(basename "$DMG")" > "SHA256_${SUFFIX}.txt"
)
echo "Build complete:"
ls -lh release/
