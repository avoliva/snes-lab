"""Assemble a patch onto a copy of a ROM with Asar, and say what changed.

Asar gotcha (verified with 1.91): a branch to a numeric address, `BRA $1FF010` at $1FF000, is encoded
with the number's low byte as the raw offset (`80 10`), not as a branch to that address (`80 0E`).
Branch to labels instead. assemble() warns when the source does this."""

from __future__ import annotations

import re
import subprocess
import tempfile
from pathlib import Path
from typing import Any

from . import config

NUMERIC_BRANCH = re.compile(
    r'^[ \t]*(?:[A-Za-z_.][\w.]*:[ \t]*)?(BRA|BRL|BEQ|BNE|BCC|BCS|BPL|BMI|BVC|BVS)(?:\.[bw])?[ \t]+\$[0-9A-Fa-f]+\b',
    re.IGNORECASE | re.MULTILINE,
)


class AsarError(RuntimeError):
    pass


def find() -> Path:
    path = config.find_asar()
    if path is None:
        raise AsarError('Asar not found. Run scripts/build_asar.sh, or set SNES_LAB_ASAR.')
    return path


def numeric_branches(source: str) -> list[str]:
    """Lines of `source` that branch to a number (files pulled in with incsrc are not scanned)."""
    lines = source.splitlines()
    out = []
    for m in NUMERIC_BRANCH.finditer(source):
        n = source.count('\n', 0, m.start())
        out.append(f'line {n + 1}: {lines[n].strip()}')
    return out


def changed_ranges(old: bytes, new: bytes, limit: int = 64) -> tuple[list[tuple[int, int]], bool]:
    """(start, end) file-offset ranges where `new` differs from `old` (growth counts as changed)."""
    ranges: list[tuple[int, int]] = []
    i, n = 0, len(new)
    while i < n:
        if i % 4096 == 0 and old[i : i + 4096] == new[i : i + 4096]:
            i += 4096
            continue
        if i < len(old) and old[i] == new[i]:
            i += 1
            continue
        j = i
        while j < n and (j >= len(old) or old[j] != new[j]):
            j += 1
        if len(ranges) == limit:
            return ranges, True
        ranges.append((i, j))
        i = j
    return ranges, False


def assemble(rom: bytes, source: str | Path, timeout: float = 60) -> dict[str, Any]:
    """Apply `source` (an .asm file path, or assembly text) to a copy of `rom`.

    Returns {'rom': patched bytes, 'ranges': [(start, end)], 'truncated', 'warnings', 'output'}.
    A file path is assembled in its own folder, so incsrc/incbin paths work as usual. Asar's title
    check is off: snes-lab strips copier headers first, which is what that check guards against."""
    exe = find()
    with tempfile.TemporaryDirectory(prefix='snes-lab-asar-') as tmp:
        target = Path(tmp) / 'rom.sfc'
        target.write_bytes(rom)
        if isinstance(source, Path):
            if not source.is_file():
                raise AsarError(f'No such file: {source}')
            asm, cwd, text = source, source.parent, source.read_text(errors='replace')
        else:
            asm, cwd, text = Path(tmp) / 'patch.asm', Path(tmp), source
            asm.write_text(source)
        run = subprocess.run(
            [str(exe), '--fix-checksum=off', '--no-title-check', str(asm), str(target)],
            capture_output=True,
            text=True,
            cwd=cwd,
            timeout=timeout,
        )
        output = (run.stdout + run.stderr).strip()
        if run.returncode:
            raise AsarError(f'Asar failed:\n{output}')
        patched = target.read_bytes()
    ranges, truncated = changed_ranges(rom, patched)
    warnings = [
        f'{w} -- Asar encodes a numeric branch target as a raw offset; branch to a label.'
        for w in numeric_branches(text)
    ]
    if len(patched) != len(rom):
        warnings.append(f'ROM size changed: {len(rom):#x} -> {len(patched):#x} bytes.')
    return {'rom': patched, 'ranges': ranges, 'truncated': truncated, 'warnings': warnings, 'output': output}
