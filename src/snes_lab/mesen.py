"""Mesen (MesenCE) as a batch debugger: watches (breakpoints), the code/data log, SA-1, savestates.

Each call writes a job for mesen_job.lua, runs Mesen once headless (`--testrunner`) and parses what
the script wrote. Mesen must be in portable mode (a settings.json beside the binary), or it opens its
first-run window and waits; scripts/fetch_mesen.sh sets that up in the cache.

Code/data log flags per ROM byte (MesenCE Core/Debugger/DebugTypes.h, SnesCodeDataLogger.h)."""

from __future__ import annotations

import fcntl
import os
import shutil
import subprocess
import tempfile
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from . import config, cpu65816
from . import rom as R

CODE, DATA, JUMP_TARGET, SUB_ENTRY, X8, M8 = 0x01, 0x02, 0x04, 0x08, 0x10, 0x20
LUA = Path(__file__).with_name('mesen_job.lua')
KINDS = ('exec', 'read', 'write')
CPUS = ('snes', 'sa1')
# watch "memory" -> Mesen memType, per CPU. "cpu" is the CPU's own address space ($7E0019, $00:8000).
MEMORY = {
    'snes': {'cpu': 'snesMemory', 'wram': 'snesWorkRam', 'sram': 'snesSaveRam', 'rom': 'snesPrgRom'},
    'sa1': {'cpu': 'sa1Memory', 'iram': 'sa1InternalRam', 'rom': 'snesPrgRom'},
}
READ_KINDS = {'wram': 'snesWorkRam', 'sram': 'snesSaveRam', 'iram': 'sa1InternalRam', 'cpu': 'snesMemory'}
WRITES = {'STA', 'STX', 'STY', 'STZ', 'INC', 'DEC', 'ASL', 'LSR', 'ROL', 'ROR', 'TSB', 'TRB', 'MVN', 'MVP',
          'PHA', 'PHX', 'PHY', 'PHP', 'PHB', 'PHD', 'PHK', 'PEA', 'PEI', 'PER', 'JSR', 'JSL'}  # fmt: skip
READS = {'LDA', 'LDX', 'LDY', 'CMP', 'CPX', 'CPY', 'ADC', 'SBC', 'AND', 'ORA', 'EOR', 'BIT', 'INC', 'DEC',
         'ASL', 'LSR', 'ROL', 'ROR', 'TSB', 'TRB', 'MVN', 'MVP', 'PLA', 'PLX', 'PLY', 'PLP', 'PLB', 'PLD',
         'RTS', 'RTL', 'RTI', 'PEI', 'JMP', 'JML', 'JSR'}  # fmt: skip
# The portable settings.json scripts/fetch_mesen.sh writes. IO access lets the script write results;
# Port1 plugs in a controller (otherwise only Mesen's first-run window does, and inputs are ignored).
SETTINGS = (
    '{"Debug":{"ScriptWindow":{"AllowIoOsAccess":true,"AllowNetworkAccess":false,"ScriptTimeout":600}},'
    '"Snes":{"RamPowerOnState":1,"EnableRandomPowerOnState":false,"Port1":{"Type":"SnesController"}}}'
)


class MesenError(ValueError):
    """A Mesen run that could not be set up or did not finish."""


@dataclass
class Watch:
    kind: str
    start: int
    stop: int
    cpu: str = 'snes'
    memory: str = 'cpu'
    value: int | None = None

    @classmethod
    def parse(cls, w: dict[str, Any]) -> Watch:
        kind = str(w.get('kind', 'write'))
        cpu = str(w.get('cpu', 'snes'))
        memory = str(w.get('memory', 'cpu'))
        if kind not in KINDS:
            raise MesenError(f'Unknown watch kind {kind!r}; use {", ".join(KINDS)}.')
        if cpu not in CPUS:
            raise MesenError(f'Unknown cpu {cpu!r}; use {", ".join(CPUS)}.')
        if memory not in MEMORY[cpu]:
            raise MesenError(f'Unknown memory {memory!r} for the {cpu} CPU; use {", ".join(MEMORY[cpu])}.')
        if 'address' not in w:
            raise MesenError('Each watch needs an "address".')
        start = R.parse_address(w['address'])
        stop = R.parse_address(w['end']) if w.get('end') is not None else start
        if stop < start:
            raise MesenError(f'Watch end ${stop:06X} is before its start ${start:06X}.')
        value = w.get('value')
        return cls(kind, start, stop, cpu, memory, int(value) if value is not None else None)


def lua(v: Any) -> str:
    """A Python value as a Lua literal (dicts, lists, str, int, bool; None keys are dropped)."""
    if isinstance(v, bool):
        return 'true' if v else 'false'
    if isinstance(v, int):
        return str(v)
    if isinstance(v, str | Path):
        s = str(v)
        return '"' + ''.join(c if c.isprintable() and c not in '"\\' else f'\\{ord(c):03d}' for c in s) + '"'
    if isinstance(v, dict):
        return '{' + ', '.join(f'[{lua(k)}] = {lua(x)}' for k, x in v.items() if x is not None) + '}'
    if isinstance(v, list | tuple):
        return '{' + ', '.join(lua(x) for x in v) + '}'
    raise TypeError(f'cannot write {type(v).__name__} as Lua')


def mesen_buttons(buttons: str) -> dict[str, bool]:
    """'RIGHT+Y+B' -> Mesen's input table {right=true, y=true, b=true}."""
    from .core import BUTTONS

    names = [n.strip().upper() for n in buttons.replace(',', '+').split('+') if n.strip()]
    bad = [n for n in names if n not in BUTTONS]
    if bad:
        raise MesenError(f'Unknown button {bad[0]!r}. Use {"/".join(BUTTONS)}.')
    return {n.lower(): True for n in names}


def binary() -> Path:
    b = config.find_mesen()
    if b is None:
        raise MesenError('Mesen not found. Run scripts/fetch_mesen.sh, or set SNES_LAB_MESEN to a MesenCE binary.')
    settings = b.parent / 'settings.json'
    if not settings.is_file():
        raise MesenError(
            f'{b.parent} has no settings.json, so Mesen would open its first-run window and hang. '
            f'Run scripts/fetch_mesen.sh (it installs a portable copy) or write this there: {SETTINGS}'
        )
    text = settings.read_text()
    if '"AllowIoOsAccess":true' not in text.replace(' ', '') or 'SnesController' not in text:
        raise MesenError(
            f'{settings} must allow script IO and plug a SnesController into Port1, or results and inputs '
            f'are lost. Re-run scripts/fetch_mesen.sh or use: {SETTINGS}'
        )
    return b


def _clean_saves(home: Path, stem: str) -> None:
    for p in home.glob(f'**/{stem}*'):
        if p.is_file():
            p.unlink()


def run(
    rom: Path,
    *,
    inputs: list[Any] | None = None,
    frames: int = 0,
    watches: list[Watch] | None = None,
    limit: int = 200,
    reads: list[tuple[int, int, str]] | None = None,
    cdl_out: Path | None = None,
    state_in: Path | None = None,
    state_out: Path | None = None,
    screenshot: Path | None = None,
    timeout: int = 600,
) -> dict[str, Any]:
    """Run one Mesen job. Returns hits per watch, counts, reads, frames run and the files written."""
    exe = binary()
    steps = [(int(n), mesen_buttons(str(b))) for n, b in (inputs or [])]
    total = max(int(frames), sum(n for n, _ in steps), 1)
    watches = watches or []
    with tempfile.TemporaryDirectory(prefix='snes-lab-mesen-') as tmp:
        t = Path(tmp)
        stem = f'snes-lab-{uuid.uuid4().hex[:12]}'
        rom_copy = t / f'{stem}{rom.suffix or ".sfc"}'
        shutil.copyfile(rom, rom_copy)
        out = t / 'out.tsv'
        job = {
            'out': out,
            'frames': total,
            'limit': max(1, int(limit)),
            'inputs': [[n, b] for n, b in steps],
            'watches': [
                {
                    'kind': w.kind,
                    'start': w.start,
                    'stop': w.stop,
                    'cpu': w.cpu,
                    'memory': MEMORY[w.cpu][w.memory],
                    'value': w.value,
                }
                for w in watches
            ],  # fmt: skip
            'reads': [[a, n, k] for a, n, k in (reads or [])],
            'cdl': cdl_out,
            'state_in': state_in,
            'state_out': state_out,
            'shot': screenshot,
        }
        (t / 'job.lua').write_text('return ' + lua(job) + '\n')
        env = dict(os.environ, SNES_LAB_JOB=str(t / 'job.lua'))
        lock = config.cache_dir() / 'mesen.lock'
        lock.parent.mkdir(parents=True, exist_ok=True)
        with lock.open('w') as lk:
            fcntl.flock(lk, fcntl.LOCK_EX)  # one portable Mesen home: one run at a time
            try:
                proc = subprocess.run(
                    [str(exe), '--testrunner', str(rom_copy), str(LUA), f'--timeout={int(timeout)}'],
                    cwd=tmp, env=env, capture_output=True, text=True, timeout=timeout + 60,
                )  # fmt: skip
            except subprocess.TimeoutExpired as e:
                raise MesenError(f'Mesen did not exit within {timeout + 60} s.') from e
            finally:
                _clean_saves(exe.parent, stem)
        lines = out.read_text().splitlines() if out.exists() else []
    result = parse(lines, len(watches))
    if not result['done']:
        tail = '\n'.join((proc.stdout + proc.stderr).strip().splitlines()[-5:])
        raise MesenError(
            f'Mesen stopped after {result["frames_run"]} of {total} frames (exit {proc.returncode}). '
            f'Raise timeout (now {timeout} s) if the machine is busy. Output:\n{tail}'
        )
    result['frames_asked'] = total
    return result


def parse(lines: list[str], n_watches: int) -> dict[str, Any]:
    hits: list[list[dict[str, Any]]] = [[] for _ in range(n_watches)]
    counts = [0] * n_watches
    reads: dict[int, bytes] = {}
    frames_run, done, frame_count = 0, False, None
    for line in lines:
        f = line.split('\t')
        if f[0] == 'hit':
            i = int(f[1]) - 1
            # hit watch frame address value k pc ps a x y sp d dbr emu
            hits[i].append({
                'frame': int(f[2]), 'address': int(f[3]), 'value': int(f[4]), 'k': int(f[5]), 'pc': int(f[6]),
                'ps': int(f[7]), 'a': int(f[8]), 'x': int(f[9]), 'y': int(f[10]), 'sp': int(f[11]),
                'd': int(f[12]), 'dbr': int(f[13]), 'emulation': f[14] == '1',
            })  # fmt: skip
        elif f[0] == 'count':
            counts[int(f[1]) - 1] = int(f[2])
        elif f[0] == 'read':
            reads[int(f[1]) - 1] = bytes.fromhex(f[2])
        elif f[0] == 'progress':
            frames_run = int(f[1])
        elif f[0] == 'done':
            frames_run, done, frame_count = int(f[1]), True, int(f[2])
    return {'hits': hits, 'counts': counts, 'reads': reads, 'frames_run': frames_run, 'done': done,
            'frame_count': frame_count}  # fmt: skip


def writer(rom: bytes, hit: dict[str, Any], kind: str, mapping: str) -> int | None:
    """The instruction behind a read/write hit. Mesen reports pc after the instruction, so try the
    lengths that end there; keep the one that decodes to that length and is a load/store of that kind.
    None when no single instruction fits (code in RAM, DMA, or an ambiguous decode)."""
    if kind == 'exec':
        return int(hit['k'] << 16 | hit['pc'])
    m8, x8 = bool(hit['ps'] & 0x20) or hit['emulation'], bool(hit['ps'] & 0x10) or hit['emulation']
    ops = WRITES if kind == 'write' else READS
    found = []
    for n in (2, 3, 4):
        a = hit['k'] << 16 | (hit['pc'] - n) & 0xFFFF
        try:
            off = R.to_offset(a, mapping)
        except R.RomError:
            return None
        if off + 4 > len(rom):
            continue
        ins = cpu65816.decode(rom[off : off + 4], a, m8, x8)
        if len(ins.raw) == n and ins.mnemonic in ops:
            found.append(a)
    return found[0] if len(found) == 1 else None


def addr(a: int | None) -> str | None:
    return None if a is None else f'${a >> 16:02X}:{a & 0xFFFF:04X}'


def cdl_summary(cdl: bytes, rom: bytes, mapping: str, entries: int = 100) -> dict[str, Any]:
    """Code/data byte counts, code per bank and the first subroutine entry points of a code/data log."""
    code = sum(1 for b in cdl if b & CODE)
    data = sum(1 for b in cdl if b & DATA and not b & CODE)
    bank_size = 0x8000 if mapping in ('lorom', 'sa1') else 0x10000
    banks: dict[str, int] = {}
    for start in range(0, len(cdl), bank_size):
        n = sum(1 for b in cdl[start : start + bank_size] if b & CODE)
        if n:
            banks[f'${R.to_address(start, mapping) >> 16:02X}'] = n
    subs = [R.to_address(i, mapping) for i, b in enumerate(cdl) if b & SUB_ENTRY]
    return {
        'rom_bytes': len(rom),
        'code_bytes': code,
        'data_bytes': data,
        'unseen_bytes': len(cdl) - code - data,
        'code_by_bank': banks,
        'subroutines': len(subs),
        'subroutine_entries': [addr(a) for a in subs[:entries]],
    }
