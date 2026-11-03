"""Where snes-lab finds the emulator core, the disassembly and its cache. All overridable by env var."""

from __future__ import annotations

import os
import shutil
from pathlib import Path


def cache_dir() -> Path:
    return Path(os.environ.get('SNES_LAB_CACHE', Path.home() / '.cache' / 'snes-lab')).expanduser()


def out_dir() -> Path:
    """Where screenshots, contact sheets, patches and routes are written."""
    d = Path(os.environ.get('SNES_LAB_OUT', cache_dir() / 'out')).expanduser()
    d.mkdir(parents=True, exist_ok=True)
    return d


def find_core() -> Path | None:
    """The Snes9x libretro library: $SNES_LAB_CORE, else the cache (scripts/build_core.sh)."""
    for c in (
        os.environ.get('SNES_LAB_CORE'),
        cache_dir() / 'snes9x_libretro.dylib',
        cache_dir() / 'snes9x_libretro.so',
    ):
        if c and Path(c).expanduser().is_file():
            return Path(c).expanduser()
    return None


def find_disasm() -> Path | None:
    """A disassembly folder with a .sym file: $SNES_LAB_DISASM, else the cached SMWDisX."""
    for c in (os.environ.get('SNES_LAB_DISASM'), cache_dir() / 'SMWDisX'):
        if c and Path(c).expanduser().is_dir():
            return Path(c).expanduser()
    return None


def default_rom() -> Path | None:
    for c in (os.environ.get('SNES_LAB_ROM'),):
        if c and Path(c).expanduser().is_file():
            return Path(c).expanduser()
    return None


def find_mesen() -> Path | None:
    """The MesenCE binary: $SNES_LAB_MESEN (the binary or a Mesen.app), else the portable copy that
    scripts/fetch_mesen.sh puts in the cache."""
    for c in (os.environ.get('SNES_LAB_MESEN'), cache_dir() / 'mesen' / 'Mesen.app', cache_dir() / 'mesen' / 'Mesen'):
        if not c:
            continue
        p = Path(c).expanduser()
        if p.suffix == '.app':
            p = p / 'Contents' / 'MacOS' / 'Mesen'
        if p.is_file():
            return p
    return None


def find_asar() -> Path | None:
    """Asar: $SNES_LAB_ASAR, `asar` on PATH, or the cache (scripts/build_asar.sh)."""
    for c in (
        os.environ.get('SNES_LAB_ASAR'),
        shutil.which('asar'),
        cache_dir() / 'asar',
    ):
        if c and Path(c).expanduser().is_file():
            return Path(c).expanduser()
    return None
