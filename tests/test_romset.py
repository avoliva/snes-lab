"""snes-lab's ROM tools across a whole romset (skips without one; SNES_CORPUS_STEP=8 for a quick run).

The emulator boot of every game lives in scripts/romset_sweep.py: it takes many minutes."""

import os

import pytest
from snes_kit import corpus

from snes_lab import cpu65816
from snes_lab import rom as R

TINY = 0x8000  # pass-through adapters (Super 8, Tri-Star) are 8 KiB with no header to read


@pytest.fixture(scope='module')
def romset():
    path = corpus.corpus_dir()
    if path is None:
        pytest.skip('Set SNES_CORPUS to a folder of SNES ROMs (.sfc/.smc/.zip) to test across a romset')
    return path


def test_header_and_reset_code_for_every_game(romset) -> None:
    failures: list[str] = []
    seen = 0
    for name, data in corpus.roms(romset, int(os.environ.get('SNES_CORPUS_STEP', '1'))):
        seen += 1
        rom = R.strip_copier_header(data)
        try:
            h = R.header(rom)
        except R.RomError as e:
            if len(rom) >= TINY:
                failures.append(f'{name}: header: {e}')
            continue
        # SA-1 games keep the LoROM header spot (mapping 'lorom'); their map mode says SA-1.
        if (h.map_mode == 'SA-1') != (R.code_mapping(rom) == 'sa1'):
            failures.append(f'{name}: map mode {h.map_mode}, code mapping {R.code_mapping(rom)}')
        try:
            cpu65816.disassemble(rom, h.reset_vector, 16, m8=True, x8=True)
        except R.RomError as e:
            if h.checksum_ok:  # a real header's reset vector must point into the ROM
                failures.append(f'{name}: reset {h.reset_vector}: {e}')
    assert seen > 0, 'the romset folder holds no ROMs'
    assert not failures, f'{len(failures)} of {seen} games:\n' + '\n'.join(failures)
