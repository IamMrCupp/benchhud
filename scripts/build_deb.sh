#!/usr/bin/env bash
# Build the Ubuntu .deb (amd64) for the bench, from an Apple Silicon Mac via Docker.
#
#   scripts/build_deb.sh [ubuntu-tag]      # default: 26.04, the bench's release
#
# Briefcase builds inside an amd64 Ubuntu container, which on an arm64 Mac runs
# under QEMU emulation. Everything works there except the last step: dpkg-deb's
# `tar` fails with "Cannot stat: Function not implemented", because QEMU's
# user-mode emulation lacks a stat syscall newer tar uses. dpkg-deb only packs
# files and doesn't care about the CPU, so this script lets Briefcase stage the
# package, then packs it in a *native* arm64 container. The result is still an
# amd64 .deb.
#
# If Docker Desktop has "Use Rosetta for x86_64/amd64 emulation" turned on,
# Briefcase's own package step may just work, and the fallback never runs.
# On the bench itself, skip all this: `uvx briefcase package linux system`.
set -euo pipefail

tag="${1:-26.04}"
root="$(cd "$(dirname "$0")/.." && pwd)"
cd "$root"
export DOCKER_DEFAULT_PLATFORM=linux/amd64
target="ubuntu:$tag"

if [ ! -d build/benchhud/ubuntu ]; then
    uvx briefcase create linux system --target "$target" --no-input
fi
uvx briefcase build linux system --target "$target" --no-input --update  # picks up code changes

if uvx briefcase package linux system --target "$target" --adhoc-sign --no-input; then
    ls -la dist/*.deb
    exit 0
fi

echo "briefcase package failed (expected under QEMU); packing natively instead" >&2
stage_root="$(dirname "$(find build/benchhud -path '*/DEBIAN/control' | head -1)")"
stage_root="$(dirname "$stage_root")"
[ -f "$stage_root/DEBIAN/control" ] || { echo "no staged package found" >&2; exit 1; }
codename="$(basename "$(dirname "$stage_root")")"
version="$(sed -n 's/^Version: //p' "$stage_root/DEBIAN/control")"
deb="benchhud_${version}-1~ubuntu-${codename}_amd64.deb"

mkdir -p dist
docker run --rm --platform linux/arm64 \
    -v "$root/$(dirname "$stage_root"):/src:ro" -v "$root/dist:/out" \
    "ubuntu:$tag" bash -c \
    "cp -a /src/$(basename "$stage_root") /tmp/pkg && dpkg-deb --root-owner-group --build /tmp/pkg /out/$deb"
ls -la "dist/$deb"
