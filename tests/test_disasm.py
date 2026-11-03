import pytest

from snes_lab import disasm


@pytest.mark.parametrize(
    ('query', 'name', 'address'),
    [
        ('$7E0094', 'PlayerXPosNext', '$7E0094'),
        ('$95', 'PlayerXPosNext', '$7E0094'),  # inside a 2-byte entry
        ('$1493', 'EndLevelTimer', '$7E1493'),
        ('$13BF', 'TranslevelNo', '$7E13BF'),
        ('$1EA2', 'OWLevelTileSettings', '$7E1EA2'),
        ('$0100', 'GameMode', '$7E0100'),
    ],
)
def test_ram_addresses_come_from_org_and_skip(disasm_root: str, query: str, name: str, address: str) -> None:
    hits = [h for h in disasm.lookup(disasm_root, query)['hits'] if h['kind'] == 'ram']
    assert any(h['name'] == name and h['address'] == address for h in hits), hits


def test_rom_label_by_name_has_source(disasm_root: str) -> None:
    hit = disasm.lookup(disasm_root, 'Layer1Ptrs', context=3)['hits'][0]
    assert hit['address'] == '$05:E000'
    assert hit['file_offset'] == '0x2e000'
    assert hit['source']['code'].startswith('Layer1Ptrs:')


def test_rom_address_finds_enclosing_label(disasm_root: str) -> None:
    hit = next(h for h in disasm.lookup(disasm_root, '$05:E005')['hits'] if h['kind'] == 'rom')
    assert hit['name'] == 'Layer1Ptrs' and hit['offset_from_label'] == 5


def test_ram_labels_are_not_reported_as_rom(disasm_root: str) -> None:
    hits = disasm.lookup(disasm_root, 'PlayerXPosNext')['hits']
    assert [h['kind'] for h in hits] == ['ram']


def test_partial_and_search(disasm_root: str) -> None:
    r = disasm.lookup(disasm_root, 'goalta')
    assert r['partial'] and any(h['name'] == 'GoalTape' for h in r['hits'])
    s = disasm.search(disasm_root, r'Ptrs05EC00', files='bank_05')
    assert s['total'] >= 2 and all(m.startswith('bank_05.asm:') for m in s['matches'])
