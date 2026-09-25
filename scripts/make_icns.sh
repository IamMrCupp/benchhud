#!/usr/bin/env bash
# Regenerate the app icons from the 1024px icon.png: benchhud.icns (macOS),
# benchhud-<size>.png (Linux) and benchhud.ico (Windows).
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

# Linux (Briefcase's .deb) wants plain PNGs at fixed sizes: benchhud-<size>.png.
for size in 16 32 64 128 256 512; do
    sips -z "$size" "$size" "$src" --out "$root/packaging/icons/benchhud-$size.png" >/dev/null
done
echo "wrote packaging/icons/benchhud-{16..512}.png"

# Windows (Briefcase's MSI) wants a .ico. An .ico can hold PNGs directly, so
# pack the ones above rather than pulling in an image library.
python3 - "$root/packaging/icons" <<'PY'
import struct, sys
from pathlib import Path
d = Path(sys.argv[1])
sizes = [16, 32, 64, 128, 256]
pngs = [(d / f"benchhud-{s}.png").read_bytes() for s in sizes]
out = struct.pack("<HHH", 0, 1, len(sizes))
offset = 6 + 16 * len(sizes)
for s, data in zip(sizes, pngs):
    out += struct.pack("<BBBBHHII", s % 256, s % 256, 0, 0, 1, 32, len(data), offset)
    offset += len(data)
(d / "benchhud.ico").write_bytes(out + b"".join(pngs))
PY
echo "wrote packaging/icons/benchhud.ico"
