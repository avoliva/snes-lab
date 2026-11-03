#!/usr/bin/env bash
# Fetch the SMWDisX disassembly into the snes-lab cache (read-only reference, ~12 MB).
set -euo pipefail
dest="${SNES_LAB_CACHE:-$HOME/.cache/snes-lab}/SMWDisX"
if [ -d "$dest/.git" ]; then
  git -C "$dest" pull --ff-only --quiet
else
  mkdir -p "$(dirname "$dest")"
  git clone --quiet --depth 1 https://github.com/IsoFrieze/SMWDisX.git "$dest"
fi
echo "SMWDisX at $dest"
