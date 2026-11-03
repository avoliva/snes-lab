"""Look things up in an Asar-style disassembly (built for SMWDisX; any folder with a .sym file works).

Indexes three things once per folder:
  - ROM labels from the .sym file (no$sns format: "BBAAAA Label"),
  - RAM labels from rammap.asm, with addresses worked out from ORG + skip and the comment above each,
  - where each label is defined in the .asm sources, for showing the code around it."""

from __future__ import annotations

import bisect
import contextlib
import re
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from typing import Any

_SYM = re.compile(r'^([0-9A-Fa-f]{8})\s+(\S+)')
_DEF = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*):')
_DEFINE = re.compile(r'^\s*!([A-Za-z0-9_]+)\s*=\s*(.+?)\s*(;.*)?$')
_SKIP = re.compile(r'^(?:([A-Za-z_][A-Za-z0-9_]*):\s*)?skip\s+(.+?)\s*(;.*)?$')
_LABEL_ONLY = re.compile(r'^([A-Za-z_][A-Za-z0-9_]*):\s*(;.*)?$')
_NOISE = re.compile(r'^(macro_|pos_|neg_|:)')
_SECTIONS = {'WORK RAM': 'wram', 'AUDIO RAM': 'aram', 'VIDEO RAM': 'vram', 'SAVE RAM': 'sram', 'SRAM': 'sram'}


@dataclass
class RamEntry:
    name: str
    address: int
    size: int
    space: str
    notes: str

    def as_dict(self) -> dict[str, Any]:
        a = f'${self.address:06X}' if self.space == 'wram' else f'{self.space}:${self.address:04X}'
        return {'name': self.name, 'address': a, 'size': self.size, 'space': self.space, 'notes': self.notes}


@dataclass
class Index:
    root: Path
    rom: dict[str, int] = field(default_factory=dict)  # label -> SNES address
    rom_sorted: list[tuple[int, str]] = field(default_factory=list)
    ram: list[RamEntry] = field(default_factory=list)
    defs: dict[str, tuple[str, int]] = field(default_factory=dict)  # label -> (file, line number)
    files: dict[str, list[str]] = field(default_factory=dict)


def _eval(expr: str, defines: dict[str, int]) -> int:
    e = re.sub(r'!([A-Za-z0-9_]+)', lambda m: str(defines.get(m.group(1), 0)), expr)
    e = re.sub(r'\$([0-9A-Fa-f]+)', lambda m: str(int(m.group(1), 16)), e)
    e = re.sub(r'%([01]+)', lambda m: str(int(m.group(1), 2)), e)
    if not re.fullmatch(r'[0-9+\-*/() ]+', e):
        raise ValueError(f'Cannot evaluate {expr!r}')
    return int(eval(e, {'__builtins__': {}}))  # noqa: S307 - digits and arithmetic only, checked above


def _defines(root: Path) -> dict[str, int]:
    out: dict[str, int] = {}
    for f in sorted(root.glob('*.asm')):
        for line in f.read_text(errors='replace').splitlines():
            m = _DEFINE.match(line)
            if m:
                with contextlib.suppress(ValueError, SyntaxError):
                    out[m.group(1)] = _eval(m.group(2), out)
    return out


def _parse_rammap(path: Path, defines: dict[str, int]) -> list[RamEntry]:
    out: list[RamEntry] = []
    space, org, notes = 'wram', 0, []
    for line in path.read_text(errors='replace').splitlines():
        s = line.strip()
        if s.startswith(';'):
            text = s.lstrip('; ').rstrip()
            if text.upper() in _SECTIONS:
                space = _SECTIONS[text.upper()]
            elif not text.startswith('==='):
                notes.append(text)
            continue
        if not s:
            if notes and not out:
                notes = []
            continue
        if s.upper().startswith('ORG '):
            org = _eval(s[4:].strip(), defines)
            notes = []
            continue
        m = _SKIP.match(s)
        if m:
            try:
                size = _eval(m.group(2), defines)
            except ValueError, SyntaxError:
                size = 0
            if m.group(1):
                addr = org | 0x7E0000 if space == 'wram' and org < 0x7E0000 else org
                out.append(RamEntry(m.group(1), addr, size, space, ' '.join(notes)[:600]))
            org += size
            notes = []
            continue
        m = _LABEL_ONLY.match(s)
        if m:
            addr = org | 0x7E0000 if space == 'wram' and org < 0x7E0000 else org
            out.append(RamEntry(m.group(1), addr, 0, space, ' '.join(notes)[:600]))
            notes = []
    return out


@lru_cache(maxsize=4)
def load(root: str) -> Index:
    r = Path(root).expanduser()
    if not r.is_dir():
        raise FileNotFoundError(f'No disassembly folder at {r}.')
    idx = Index(r)
    syms = sorted(r.glob('*_U.sym')) or sorted(r.glob('*.sym'))
    if syms:
        for line in syms[0].read_text(errors='replace').splitlines():
            m = _SYM.match(line)
            if not m or _NOISE.match(m.group(2)):
                continue
            a = int(m.group(1), 16)
            if (a & 0xFFFF) >= 0x8000 and (a >> 16) not in (0x70, 0x7E, 0x7F):  # RAM labels live in rammap
                idx.rom.setdefault(m.group(2), a)
    idx.rom_sorted = sorted((a, n) for n, a in idx.rom.items())
    defines = _defines(r)
    if (r / 'rammap.asm').is_file():
        idx.ram = _parse_rammap(r / 'rammap.asm', defines)
    for f in sorted(r.glob('*.asm')):
        lines = f.read_text(errors='replace').splitlines()
        idx.files[f.name] = lines
        for i, line in enumerate(lines):
            m = _DEF.match(line)
            if m:
                idx.defs.setdefault(m.group(1), (f.name, i + 1))
    return idx


def _parse_addr(q: str) -> int | None:
    t = q.strip().replace(':', '')
    if t.startswith('$'):
        t = t[1:]
    elif t.lower().startswith('0x'):
        t = t[2:]
    elif not re.fullmatch(r'[0-9A-Fa-f]{2,6}', t) or not re.search(r'[0-9]', t):
        return None
    try:
        return int(t, 16)
    except ValueError:
        return None


def _source(idx: Index, name: str, context: int) -> dict[str, Any] | None:
    d = idx.defs.get(name)
    if not d:
        return None
    f, n = d
    lines = idx.files[f]
    body = lines[n - 1 : n - 1 + context]
    return {'file': f, 'line': n, 'code': '\n'.join(body)}


def _rom_hit(idx: Index, name: str, context: int) -> dict[str, Any]:
    a = idx.rom[name]
    hit: dict[str, Any] = {
        'kind': 'rom',
        'name': name,
        'address': f'${a >> 16:02X}:{a & 0xFFFF:04X}',
        'file_offset': f'{((a >> 16) & 0x7F) * 0x8000 + (a & 0x7FFF):#x}',
    }
    src = _source(idx, name, context)
    if src:
        hit['source'] = src
    return hit


def _ram_for(idx: Index, address: int) -> list[RamEntry]:
    a = address
    if a < 0x10000:
        a |= 0x7E0000
    return [e for e in idx.ram if e.space == 'wram' and e.address <= a < e.address + max(e.size, 1)]


def lookup(root: str, query: str, context: int = 12, limit: int = 20) -> dict[str, Any]:
    """A label name (exact, else partial) or an address ($7E0094, $05:E000, 0094)."""
    idx = load(root)
    hits: list[dict[str, Any]] = []
    addr = _parse_addr(query)
    if addr is not None:
        for e in _ram_for(idx, addr):
            hits.append({'kind': 'ram', **e.as_dict()})
        if addr >= 0x8000 and (addr >> 16) not in (0x7E, 0x7F):
            i = bisect.bisect_right(idx.rom_sorted, (addr, '￿')) - 1
            if i >= 0:
                a, name = idx.rom_sorted[i]
                if (a >> 16) == (addr >> 16):
                    h = _rom_hit(idx, name, context)
                    h['offset_from_label'] = addr - a
                    hits.append(h)
        return {'query': query, 'as_address': f'${addr:06X}', 'hits': hits}
    q = query.strip()
    exact_rom = [n for n in idx.rom if n.lower() == q.lower()]
    exact_ram = [e for e in idx.ram if e.name.lower() == q.lower()]
    for n in exact_rom:
        hits.append(_rom_hit(idx, n, context))
    for e in exact_ram:
        hits.append({'kind': 'ram', **e.as_dict()})
    if not hits and q in idx.defs:
        hits.append({'kind': 'code', 'name': q, 'source': _source(idx, q, context)})
    if hits:
        return {'query': query, 'hits': hits}
    ql = q.lower()
    partial = [{'kind': 'ram', **e.as_dict()} for e in idx.ram if ql in e.name.lower() or ql in e.notes.lower()]
    partial += [
        {'kind': 'rom', 'name': n, 'address': f'${a >> 16:02X}:{a & 0xFFFF:04X}'}
        for n, a in idx.rom.items()
        if ql in n.lower()
    ]
    return {'query': query, 'hits': partial[:limit], 'partial': True, 'total': len(partial)}


def search(root: str, pattern: str, limit: int = 40, files: str | None = None) -> dict[str, Any]:
    """Regex search over the .asm sources (case-insensitive)."""
    idx = load(root)
    rx = re.compile(pattern, re.I)
    out = []
    total = 0
    for f, lines in idx.files.items():
        if files and not re.search(files, f):
            continue
        for i, line in enumerate(lines):
            if rx.search(line):
                total += 1
                if len(out) < limit:
                    out.append(f'{f}:{i + 1}: {line.rstrip()}')
    return {'pattern': pattern, 'total': total, 'matches': out}


def stats(root: str) -> dict[str, Any]:
    idx = load(root)
    return {
        'root': str(idx.root),
        'rom_labels': len(idx.rom),
        'ram_labels': len(idx.ram),
        'asm_files': len(idx.files),
        'definitions': len(idx.defs),
    }
