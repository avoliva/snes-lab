#!/usr/bin/env bash
# Build the Snes9x libretro core from source into the snes-lab cache.
set -euo pipefail
cache="${SNES_LAB_CACHE:-$HOME/.cache/snes-lab}"
src="$cache/snes9x-src"
if [ -d "$src/.git" ]; then
  git -C "$src" pull --ff-only --quiet
else
  mkdir -p "$cache"
  git clone --quiet --depth 1 https://github.com/snes9xgit/snes9x.git "$src"
fi
jobs="$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)"
make -C "$src/libretro" -j"$jobs" >"$cache/build-core.log" 2>&1 || { tail -20 "$cache/build-core.log"; exit 1; }
for lib in "$src"/libretro/snes9x_libretro.{dylib,so}; do
  if [ -f "$lib" ]; then
    cp "$lib" "$cache/"
    echo "Core at $cache/$(basename "$lib")"
  fi
done
