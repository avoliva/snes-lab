#!/usr/bin/env bash
# Build the Asar assembler (1.91) from source into the snes-lab cache. Uses cmake via uvx if it is not installed.
set -euo pipefail
cache="${SNES_LAB_CACHE:-$HOME/.cache/snes-lab}"
src="$cache/asar-src"
tag="${ASAR_TAG:-v1.91}"
if [ ! -d "$src/.git" ]; then
  mkdir -p "$cache"
  git clone --quiet --depth 1 --branch "$tag" https://github.com/RPGHacker/asar.git "$src"
fi
cmake=(cmake)
command -v cmake >/dev/null || cmake=(uvx cmake)
build="$src/build"
"${cmake[@]}" -S "$src/src" -B "$build" -DCMAKE_BUILD_TYPE=Release -DASAR_GEN_DLL=OFF -DASAR_GEN_LIB=OFF \
  -DASAR_GEN_EXE_TEST=OFF -DASAR_GEN_DLL_TEST=OFF \
  >"$cache/build-asar.log" 2>&1 || { tail -20 "$cache/build-asar.log"; exit 1; }
"${cmake[@]}" --build "$build" -j"$(getconf _NPROCESSORS_ONLN 2>/dev/null || echo 4)" \
  >>"$cache/build-asar.log" 2>&1 || { tail -20 "$cache/build-asar.log"; exit 1; }
cp "$(find "$build" -type f -name asar -perm -u+x | head -1)" "$cache/asar"
"$cache/asar" --version | head -1
echo "Asar at $cache/asar"
