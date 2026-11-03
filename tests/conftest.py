import os
import shutil
from pathlib import Path

import pytest

from snes_lab import config
from snes_lab import rom as R

FLIPS = Path(os.environ.get('FLIPS', 'flips'))


@pytest.fixture(scope='session')
def smw_rom() -> bytes:
    p = config.default_rom()
    if p is None:
        pytest.skip('No Super Mario World ROM (set SNES_LAB_ROM).')
    data = R.load(p)
    from snes_lab import smw

    if R.header(data).sha256 != smw.CLEAN_SHA256:
        pytest.skip('SNES_LAB_ROM is not the clean USA 1.0 image.')
    return data


@pytest.fixture(scope='session')
def smw_path(smw_rom: bytes) -> Path:
    p = config.default_rom()
    assert p is not None
    return p


@pytest.fixture(scope='session')
def disasm_root() -> str:
    d = config.find_disasm()
    if d is None:
        pytest.skip('No disassembly (run scripts/fetch_smwdisx.sh).')
    return str(d)


@pytest.fixture(scope='session')
def need_core() -> None:
    if config.find_core() is None:
        pytest.skip('No Snes9x libretro core (set SNES_LAB_CORE or run scripts/build_core.sh).')


@pytest.fixture
def flips() -> str:
    path = shutil.which('flips') or (str(FLIPS) if FLIPS.is_file() else None)
    if path is None:
        pytest.skip('Floating IPS not installed.')
    return path


@pytest.fixture(autouse=True)
def _isolated_out(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv('SNES_LAB_OUT', str(tmp_path / 'out'))
