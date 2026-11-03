#!/usr/bin/env bash
# Install MesenCE 2.2.1 (Apple Silicon) into the snes-lab cache as a portable copy, for mesen_* tools.
# Portable mode = a settings.json beside the binary. Without it Mesen opens its first-run window and
# --testrunner hangs. Snes.Port1 must name a controller too: only that window plugs one in, and without
# it every input is ignored. Other platforms: install MesenCE yourself, add the same settings.json
# next to the binary, and set SNES_LAB_MESEN.
set -euo pipefail
cache="${SNES_LAB_CACHE:-$HOME/.cache/snes-lab}"
dest="$cache/mesen"
version=2.2.1
zip="Mesen_${version}_macOS_ARM64_AppleSilicon.zip"
sha256=9c230cc25ad0bae79fed810c9eeb21c1c507b96b1ce795bbd22ca4331ffb6e48

if [ "$(uname -s)-$(uname -m)" != "Darwin-arm64" ]; then
  echo "Only the Apple Silicon build is pinned here; see the comment at the top." >&2
  exit 1
fi
tmp="$(mktemp -d)"
trap 'rm -rf "$tmp"' EXIT
curl -fsSL -o "$tmp/$zip" "https://github.com/nesdev-org/MesenCE/releases/download/$version/$zip"
echo "$sha256  $tmp/$zip" | shasum -a 256 -c - >/dev/null || { echo "Checksum mismatch for $zip" >&2; exit 1; }
unzip -q "$tmp/$zip" -d "$tmp/outer"
unzip -q "$tmp/outer/Mesen.app.zip" -d "$tmp/app"
rm -rf "$dest/Mesen.app"
mkdir -p "$dest"
mv "$tmp/app/Mesen.app" "$dest/Mesen.app"
cat >"$dest/Mesen.app/Contents/MacOS/settings.json" <<'JSON'
{"Debug":{"ScriptWindow":{"AllowIoOsAccess":true,"AllowNetworkAccess":false,"ScriptTimeout":600}},"Snes":{"RamPowerOnState":1,"EnableRandomPowerOnState":false,"Port1":{"Type":"SnesController"}}}
JSON
echo "MesenCE $version at $dest/Mesen.app (portable)"
