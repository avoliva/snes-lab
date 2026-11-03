"""SNES ROM building blocks (Rust): address maths, header and checksum, BPS patches,
HAL compression and Super Mario World level encoding. See the snes-kit README."""

from ._native import (
    COPIER_HEADER,
    HAL_DATA_SIZE,
    RomError,
    bps_apply,
    bps_make,
    checksum,
    code_mapping,
    crc32,
    detect_mapping,
    fix_checksum,
    hal_pack,
    hal_unpack,
    header_offset,
    parse_address,
    smw_layer1,
    smw_sprites,
    strip_copier_header,
    to_address,
    to_offset,
)

MAPPINGS = ('lorom', 'hirom', 'exhirom', 'sa1')

__all__ = [
    'COPIER_HEADER',
    'HAL_DATA_SIZE',
    'MAPPINGS',
    'RomError',
    'bps_apply',
    'bps_make',
    'checksum',
    'code_mapping',
    'crc32',
    'detect_mapping',
    'fix_checksum',
    'hal_pack',
    'hal_unpack',
    'header_offset',
    'parse_address',
    'smw_layer1',
    'smw_sprites',
    'strip_copier_header',
    'to_address',
    'to_offset',
]
