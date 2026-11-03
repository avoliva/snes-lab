import pytest

from snes_lab import cpu65816 as D
from snes_lab import rom as R
from snes_lab.disasm import load


def lorom(code: dict[int, bytes], size: int = 0x80000) -> bytes:
    rom = bytearray(size)
    for off, b in code.items():
        rom[off : off + len(b)] = b
    return bytes(rom)


def test_smw_reset_code(smw_rom: bytes) -> None:
    lines = D.disassemble(smw_rom, '$00:8000', 6, m8=True, x8=True)
    assert [i.mnemonic for i in lines] == ['SEI', 'STZ', 'STZ', 'STZ', 'STZ', 'STZ']
    assert lines[1].operand == '$4200' and lines[1].raw == bytes.fromhex('9c0042')
    assert lines[0].text().startswith('$00:8000  78')


def test_rep_sep_change_immediate_size() -> None:
    # REP #$30 ; LDA #$1234 ; LDX #$5678 ; SEP #$20 ; LDA #$12 ; RTS ; NOP
    rom = lorom({0: bytes.fromhex('c230 a93412 a27856 e220 a912 60 ea')})
    ins = D.disassemble(rom, '$00:8000', 10, m8=True, x8=True, stop_at_return=True)
    assert [i.as_dict()['asm'] for i in ins] == [
        'REP #$30',
        'LDA #$1234',
        'LDX #$5678',
        'SEP #$20',
        'LDA #$12',
        'RTS',
    ]
    assert ins[4].m8 and not ins[4].x8


def test_branch_and_call_targets() -> None:
    rom = lorom({0: bytes.fromhex('80fe 22 9a8800 20 0080 82fdff 4400 7f')})
    b, jsl, jsr, brl, mvp = D.disassemble(rom, '$00:8000', 5)
    assert b.target == 0x008000 and b.operand == '$8000'
    assert jsl.target == 0x00889A and jsl.operand == '$00889A'
    assert jsr.target == 0x008000
    assert brl.target == 0x008009  # BRL -3 from $8009 + 3
    assert mvp.mnemonic == 'MVP' and mvp.operand == '$7F,$00'


def test_xrefs_match_the_disassembly(smw_rom: bytes, disasm_root: str) -> None:
    """Every JSL to FinishOAMWrite in SMWDisX, no more, no fewer."""
    target = load(disasm_root).rom['FinishOAMWrite']
    r = D.xrefs(smw_rom, target)
    assert len(r['hits']) == 32 and {h['kind'] for h in r['hits']} == {'JSL'}


def test_xrefs_mirrors_same_bank_and_long_data() -> None:
    rom = lorom(
        {
            0x0100: bytes.fromhex('22 0090 80'),  # JSL $80:9000 (FastROM mirror of $00:9000)
            0x0200: bytes.fromhex('20 0090'),  # JSR $9000 in bank $00
            0x8200: bytes.fromhex('20 0090'),  # JSR $9000 in bank $01: a different routine
            0x0300: bytes.fromhex('8f 3412 7e'),  # STA $7E1234
        }
    )
    calls = D.xrefs(rom, '$00:9000')['hits']
    assert [(h['address'], h['kind']) for h in calls] == [('$00:8100', 'JSL'), ('$00:8200', 'JSR')]
    data = D.xrefs(rom, '$7E1234', kinds=('long',))['hits']
    assert [(h['address'], h['kind']) for h in data] == [('$00:8300', 'STA')]
    assert D.xrefs(rom, '$7E1234', kinds=('call',))['hits'] == []


def test_sa1_mapping() -> None:
    kss_like = bytearray(0x400000)
    kss_like[0x7FC0 + 0x15] = 0x23
    kss_like[0x7FC0 + 0x3C : 0x7FC0 + 0x3E] = b'\x00\x80'
    assert R.code_mapping(bytes(kss_like)) == 'sa1'
    assert R.to_offset(0xC01234, 'sa1') == 0x1234
    assert R.to_offset(0x018000, 'sa1') == 0x8000
    assert R.to_address(0x8000, 'sa1') == 0x018000
    assert R.to_address(0x234567, 'sa1') == 0xE34567
    with pytest.raises(R.RomError):
        D.disassemble(bytes(kss_like), '$7E:0000')


def test_listing_stops_at_the_end_of_the_bank() -> None:
    # Zoop and XBAND keep a reset stub at $FFE0-$FFF0 that jumps away; a listing that runs on past
    # $FFFF wraps to $0000, which is not ROM. Return the lines up to there instead of failing.
    rom = bytearray(0x10000)
    rom[0xFFF0:0xFFF8] = bytes([0x78, 0x18, 0xFB, 0x5C, 0x00, 0x80, 0xC0, 0xEA])  # SEI CLC XCE JML $C08000 NOP
    lines = D.disassemble(bytes(rom), '$00:FFF0', 40, mapping='hirom')
    assert [i.mnemonic for i in lines[:4]] == ['SEI', 'CLC', 'XCE', 'JML']
    assert lines[-1].address <= 0xFFFF
    with pytest.raises(R.RomError):
        D.disassemble(bytes(rom), '$00:0000', 4, mapping='hirom')  # the start itself is not ROM
