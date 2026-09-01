#!/bin/bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
SRC="$ROOT/assets/icon_1024.png"
ICONSET="$ROOT/assets/HV_P2P_NMS.iconset"
OUT="$ROOT/assets/HV_P2P_NMS.icns"
rm -rf "$ICONSET" "$OUT"
mkdir -p "$ICONSET"
for size in 16 32 128 256 512; do
  sips -z "$size" "$size" "$SRC" --out "$ICONSET/icon_${size}x${size}.png" >/dev/null
  double=$((size*2))
  sips -z "$double" "$double" "$SRC" --out "$ICONSET/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns "$ICONSET" -o "$OUT"
rm -rf "$ICONSET"
echo "Created $OUT"
