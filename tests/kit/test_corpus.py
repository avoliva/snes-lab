"""snes_kit across a whole romset: header detection, checksums, address maths and BPS.

Skips unless `SNES_CORPUS` names a folder of your own ROM dumps. Every game is read once and
each check collects its failures, so one run lists every game that breaks a rule.
`SNES_CORPUS_STEP=8` checks every 8th game for a quick run."""

import os
from collections import defaultdict

import pytest
import snes_kit as sk
from snes_kit import corpus

LOCATIONS = {'lorom': 0x7FC0, 'hirom': 0xFFC0, 'exhirom': 0x40_FFC0}
BPS_EVERY = 10  # BPS round trips on every 10th game keep the run short


def word(b: bytes, at: int) -> int:
    return b[at] | b[at + 1] << 8


def sum_matches(rom: bytes, at: int, total: int) -> bool:
    return len(rom) >= at + 0x40 and word(rom, at + 0x1E) == total and word(rom, at + 0x1C) == total ^ 0xFFFF


@pytest.fixture(scope='module')
def romset():
    path = corpus.corpus_dir()
    if path is None:
        pytest.skip('Set SNES_CORPUS to a folder of SNES ROMs (.sfc/.smc/.zip) to test across a romset')
    return path


def test_every_game(romset):
    failures: dict[str, list[str]] = defaultdict(list)
    seen = 0
    step = int(os.environ.get('SNES_CORPUS_STEP', '1'))
    for i, (name, data) in enumerate(corpus.roms(romset, step)):
        seen += 1
        rom = sk.strip_copier_header(data)
        try:
            mapping, total = sk.detect_mapping(rom), sk.checksum(rom)
            sk.code_mapping(rom)
        except Exception as e:  # noqa: BLE001 - any exception is the finding
            failures['raises'].append(f'{name}: {e!r}')
            continue

        # The one header whose checksum matches the image is the real one.
        matching = [m for m, at in LOCATIONS.items() if sum_matches(rom, at, total)]
        if len(matching) == 1 and matching[0] != mapping:
            failures['mapping'].append(f'{name}: detected {mapping}, checksum is at {matching[0]}')

        # A clean dump's checksum is already right, so fixing it changes nothing.
        if matching and sk.fix_checksum(rom, matching[0]) != rom:
            failures['fix_checksum'].append(name)

        # The reset vector of a real header lands inside the image.
        if matching:
            reset = word(rom, LOCATIONS[mapping] + 0x3C)
            try:
                if not 0 <= sk.to_offset(reset, mapping) < len(rom):
                    failures['reset'].append(f'{name}: ${reset:04X} outside the image')
            except sk.RomError as e:
                failures['reset'].append(f'{name}: ${reset:04X}: {e}')

        if i % BPS_EVERY == 0:
            changed = bytearray(rom)
            changed[len(rom) // 2] ^= 0xFF
            changed += b'\x00' * 0x1000
            if sk.bps_apply(rom, sk.bps_make(rom, bytes(changed))) != changed:
                failures['bps'].append(name)

    assert seen > 0, 'the romset folder holds no ROMs'
    report = '\n'.join(f'{kind} ({len(v)}):\n  ' + '\n  '.join(v) for kind, v in failures.items())
    assert not failures, f'{seen} games checked\n{report}'
