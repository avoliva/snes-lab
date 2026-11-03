"""Read a folder of ROMs (a romset) for tests and sweeps across many games.

The folder may hold .sfc/.smc files and .zip files with one ROM inside, as romsets usually
come. Nothing is extracted to disk. Point `SNES_CORPUS` at the folder; tests skip without it."""

from __future__ import annotations

import os
import zipfile
from collections.abc import Iterator
from pathlib import Path

ROM_SUFFIXES = ('.sfc', '.smc')


def corpus_dir() -> Path | None:
    """`$SNES_CORPUS`, if it is set and names a folder."""
    value = os.environ.get('SNES_CORPUS')
    if not value:
        return None
    path = Path(value).expanduser()
    return path if path.is_dir() else None


def names(path: Path) -> list[Path]:
    """The ROM and .zip files in `path`, sorted by name."""
    return sorted(p for p in path.iterdir() if p.suffix.lower() in (*ROM_SUFFIXES, '.zip'))


def read(file: Path) -> bytes:
    """A ROM's bytes as stored (copier header kept), from a .sfc/.smc or the first ROM in a .zip."""
    if file.suffix.lower() != '.zip':
        return file.read_bytes()
    with zipfile.ZipFile(file) as z:
        inner = [n for n in z.namelist() if n.lower().endswith(ROM_SUFFIXES)]
        if not inner:
            raise ValueError(f'{file.name} holds no .sfc or .smc file.')
        return z.read(inner[0])


def roms(path: Path, step: int = 1) -> Iterator[tuple[str, bytes]]:
    """(name without extension, bytes) for every `step`-th ROM in `path`."""
    for file in names(path)[::step]:
        yield file.stem, read(file)
