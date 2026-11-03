"""Run snes-lab's generic tools over a whole romset and report which games break them.

For every ROM: header (mapping, checksum), a disassembly at the reset vector, and with --boot a
Snes9x boot sampled every 150 frames: a game that never draws more than one colour in that time
counts as "blank" (one screenshot is not enough: many games sit on a black fade at any given frame).
Writes report.csv to --out and prints the games that fail.

    uv run python scripts/romset_sweep.py --boot            # every game, about 1 s per game
    uv run python scripts/romset_sweep.py --step 8          # every 8th game, no emulator
    uv run python scripts/romset_sweep.py --boot --match 'Star Fox|Zoop'   # only games matching a regex

The romset folder is $SNES_CORPUS: your own ROM dumps (.sfc/.smc/.zip)."""

from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

from snes_kit import corpus

from snes_lab import config, cpu65816
from snes_lab import rom as R
from snes_lab.sessions import SessionError, Sessions


def check(data: bytes) -> dict[str, object]:
    rom = R.strip_copier_header(data)
    row: dict[str, object] = {'size_kb': len(rom) // 1024}
    try:
        h = R.header(rom)
    except R.RomError as e:
        return row | {'error': f'header: {e}'}
    row |= {'title': h.title.strip(), 'mapping': h.mapping, 'map_mode': h.map_mode, 'checksum_ok': h.checksum_ok,
            'reset': h.reset_vector}  # fmt: skip
    try:
        insns = cpu65816.disassemble(rom, h.reset_vector, 8, m8=True, x8=True)
        row['reset_code'] = '; '.join(f'{i.mnemonic} {i.operand}'.strip() for i in insns[:4])
    except R.RomError as e:
        row['error'] = f'disasm: {e}'
    return row


def boot(sessions: Sessions, data: bytes, name: str, frames: int, every: int = 150) -> dict[str, object]:
    """Run up to `frames` frames, looking every `every` frames; 'ok' at the first frame with 2+ colours."""
    try:
        s, _ = sessions.open(R.strip_copier_header(data), name, f'{name[:40]}.sfc')
    except SessionError as e:
        return {'boot': f'error: {e}'}
    colours, ran = 0, 0
    try:
        while ran < frames and colours <= 1:
            s.call('run', frames=every, timeout=max(120, every / 20))
            ran += every
            raw, _, _ = s.call('frame')
            colours = max(colours, len({raw[i : i + 2] for i in range(0, len(raw), 2)}))
    except SessionError as e:
        return {'boot': f'error: {e}'}
    finally:
        sessions.close(s.id)
    return {'boot': 'ok' if colours > 1 else 'blank', 'colours': colours, 'first_picture': ran if colours > 1 else ''}


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument('--corpus', type=Path, default=corpus.corpus_dir())
    ap.add_argument('--step', type=int, default=1, help='check every Nth game')
    ap.add_argument('--boot', action='store_true', help='also boot each game in Snes9x')
    ap.add_argument('--frames', type=int, default=1800, help='frames to watch for a picture (30 s of game time)')
    ap.add_argument('--match', help='only games whose file name matches this regex')
    ap.add_argument('--out', type=Path, default=config.out_dir() / 'romset')
    a = ap.parse_args()
    if a.corpus is None or not a.corpus.is_dir():
        print('No romset folder: pass --corpus or set SNES_CORPUS.', file=sys.stderr)
        return 2
    a.out.mkdir(parents=True, exist_ok=True)
    sessions = Sessions()
    rows: list[dict[str, object]] = []
    started = time.time()
    files = corpus.names(a.corpus)[:: a.step]
    if a.match:
        files = [f for f in files if re.search(a.match, f.stem, re.IGNORECASE)]
    for n, f in enumerate(files, 1):
        try:
            data = corpus.read(f)
        except (OSError, ValueError) as e:
            rows.append({'game': f.stem, 'error': f'read: {e}'})
            continue
        row = {'game': f.stem, **check(data)}
        if a.boot and 'error' not in row:
            row |= boot(sessions, data, f.stem, a.frames)
        rows.append(row)
        if n % 25 == 0:
            print(f'{n}/{len(files)} games, {time.time() - started:.0f} s', flush=True)
    sessions.close_all()
    fields = ['game', 'title', 'size_kb', 'mapping', 'map_mode', 'checksum_ok', 'reset', 'reset_code', 'boot',
              'colours', 'first_picture', 'error']  # fmt: skip
    with (a.out / 'report.csv').open('w', newline='') as fh:
        w = csv.DictWriter(fh, fieldnames=fields, extrasaction='ignore')
        w.writeheader()
        w.writerows(rows)
    errors = [r for r in rows if r.get('error') or str(r.get('boot', '')).startswith('error')]
    blanks = [r for r in rows if r.get('boot') == 'blank']
    print(f'{len(rows)} games: {len(errors)} errors, {len(blanks)} blank after {a.frames} frames. '
          f'Report: {a.out / "report.csv"}')  # fmt: skip
    for r in errors:
        print(f'  error  {r["game"]}: {r.get("error") or r.get("boot")}')
    for r in blanks:
        print(f'  blank  {r["game"]}')
    return 0


if __name__ == '__main__':
    sys.exit(main())
