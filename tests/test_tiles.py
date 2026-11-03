import pytest

from snes_lab import tiles


def test_2bpp_planes() -> None:
    # Row 0: plane0 = 0b10000000, plane1 = 0b01000000 -> pixels 1, 2, 0...; row 1: both planes set -> 3
    tile = bytes([0x80, 0x40, 0xFF, 0xFF]) + bytes(12)
    px = tiles.decode(tile, 2)
    assert px[:3] == [1, 2, 0] and px[8:16] == [3] * 8 and set(px[16:]) == {0}


def test_4bpp_upper_planes_at_plus_16() -> None:
    tile = bytearray(32)
    tile[16] = 0x80  # plane 2, row 0, pixel 0
    tile[17 + 2] = 0x01  # plane 3, row 1, pixel 7
    px = tiles.decode(bytes(tile), 4)
    assert px[0] == 4 and px[15] == 8


def test_sheet_png_and_palette() -> None:
    vram = bytes([0xFF, 0x00] * 8) * 4  # four 2bpp tiles of colour 1
    png, n = tiles.sheet(vram, bpp=2, count=4, cols=2, scale=1, palette=[0, 0x001F])
    assert n == 4 and png.startswith(b'\x89PNG')
    assert tiles.bgr555(0x001F) == (255, 0, 0) and tiles.bgr555(0x7C00) == (0, 0, 255)
    with pytest.raises(ValueError):
        tiles.sheet(vram, bpp=3)
