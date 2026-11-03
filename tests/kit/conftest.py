import os
from pathlib import Path

import pytest

EXHAL_DIR = Path(os.environ.get('EXHAL_DIR', '/nonexistent'))
KSS_ROM = Path(os.environ.get('KSS_ROM', '/nonexistent'))


@pytest.fixture(scope='session')
def exhal_dir() -> Path:
    if not (EXHAL_DIR / 'exhal').exists() or not (EXHAL_DIR / 'inhal').exists():
        pytest.skip('Set EXHAL_DIR to a built exhal/inhal to compare against them')
    return EXHAL_DIR


@pytest.fixture(scope='session')
def kss_rom() -> bytes:
    if not KSS_ROM.exists():
        pytest.skip('Set KSS_ROM to a Kirby Super Star (USA) ROM to test on real HAL data')
    return KSS_ROM.read_bytes()
