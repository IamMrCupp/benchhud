#!/usr/bin/env bash
# Regenerate packaging/icons/benchhud.icns from the app's 1024px icon.png.
#
# Briefcase wants a real .icns for the macOS bundle; rerun this whenever
# src/benchhud/resources/icon.png changes. macOS-only (sips + iconutil).
set -euo pipefail

root="$(cd "$(dirname "$0")/.." && pwd)"
src="$root/src/benchhud/resources/icon.png"
out="$root/packaging/icons/benchhud.icns"
iconset="$(mktemp -d)/benchhud.iconset"
mkdir -p "$iconset" "$(dirname "$out")"

for size in 16 32 128 256 512; do
    sips -z "$size" "$size" "$src" --out "$iconset/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "$double" "$double" "$src" --out "$iconset/icon_${size}x${size}@2x.png" >/dev/null
done

iconutil -c icns "$iconset" -o "$out"
rm -rf "$(dirname "$iconset")"
echo "wrote $out"
