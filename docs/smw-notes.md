# Using snes-lab: which tool, SMW facts and gotchas

A field guide for people and for AI agents driving the snes-lab MCP server: which tool answers
which question, the Super Mario World facts the tools rely on, and the gotchas each found by
failing first. Point your agent at this file. Call `lab_info` first to see what snes-lab found
(core, disassembly, default ROM).

## Which tool

| Want | Tool |
| --- | --- |
| Header, mapping, checksum, CRC | `rom_info` |
| CPU address <-> file offset | `rom_address` (`$05:E000` is an address, `0x2E000` an offset) |
| Bytes at an address | `rom_read` |
| Patch a copy (+ .bps) | `rom_patch` (never edits the source; fixes the checksum) |
| BPS | `bps_make`, `bps_apply` (Flips-compatible, headered sources handled) |
| Assemble a patch (+ .bps) | `asm_patch` (Asar; .asm path or inline text; lists changed ranges) |
| Read code in a game with no disassembly | `rom_disasm` (give m8/x8; every line shows the flags it used) |
| Who calls / jumps to / long-addresses X? | `rom_xrefs` (whole-ROM scan, mirrors included) |
| What is $7E1493 / Layer1Ptrs? | `disasm_lookup` (RAM notes, ROM code around a label) |
| Where is X used? | `disasm_search` (regex over SMWDisX .asm) |
| Play / poke a ROM | `emu_open` -> `emu_run` / `emu_play` / `emu_ram_*` / `emu_state_*` / `emu_screenshot` |
| Run until a value changes / reaches X | `emu_run_until` (wram, sram/BW-RAM or vram; op `changed`) |
| Which value moves when Y happens? | `emu_watch` (logs every change frame by frame) |
| Find fonts / menu text graphics | `emu_vram_tiles` (2/4/8bpp sheet), `emu_mem_dump` (whole memory to a file) |
| Decode a level | `smw_level` (hex level 0-1FF) |
| See a whole level | `smw_level_sheet` (one call), or `smw_enter_level` + `smw_tour` |
| Try N variants and compare | `experiment_run` (groups identical screenshots) |
| Can Mario finish it? | `smw_enter_level` then `route_search` (spec=None is "reach the goal tape"), `route_replay` |
| Who writes / reads / runs X? (breakpoint) | `mesen_trace` (Mesen; registers per hit, `pc` of the writer; `cpu: "sa1"` for SA-1 code and I-RAM) |
| Which bytes are code, with what M/X? | `mesen_cdl` (play the part you care about), then `rom_disasm(cdl=...)` |

Buttons are written `RIGHT+Y+B`. Input scripts are `[[frames, "BUTTONS"], ...]`.

## SMW facts (USA 1.0, headerless, LoROM)

- Clean ROM (your own dump, set with `SNES_LAB_ROM`): CRC32 B19ED489, SHA-256 0838e531...7f5b, 512 KiB.
- Level tables (file offsets): layer 1 pointers `0x2E000` ($05:E000, 3 bytes/level), layer 2
  `0x2E600`, sprite pointers `0x2EC00` (2 bytes/level, bank $07), secondary headers `0x2F000`,
  `0x2F200`, `0x2F400`, `0x2F600` (1 byte/level each). 0x200 levels.
- Level data: 5-byte header, then 3-byte objects (`NBBYYYYY bbbbXXXX SSSSSSSS`, object number
  `BB<<4|bbbb`; 0 = extended object; extended 0 = 4-byte screen exit, extended 1 = screen jump),
  ending `FF`. Sprite list: 1 header byte, then `yyyyEEsY XXXXSSSS NNNNNNNN`, ending `FF`.
- Key RAM: game mode `$0100` (`0x0E` overworld, `0x14` in level), Mario X/Y `$94`/`$96` (16-bit),
  speeds `$7B`/`$7D`, animation `$71` (`9` = dying), power-up `$19`, goal timer `$1493`, midway
  `$13CE`, lives `$0DBE`, translevel `$13BF`, star `$1490`, invulnerability `$1497`,
  overworld level settings `$1EA2 + translevel`.
- Yoshi's Island 1 is level `0x105`, translevel `0x29`. `smw_enter_level` plays any level by copying
  its pointers and secondary header into 0x105's slot.
- Free bank-07 space for sprite lists: `$07:E76E-$07:EFFF` (`insert_empty $891` in SMWDisX).
- Water flag: `((byte at $05:F200 + level) >> 3) & 7 == 7`. Not `$05:F600`.
- Midway: set bit `0x40` of `$1EA2 + translevel` on the overworld before pressing B and the level
  starts at the header's midway screen (high nibble of `$05:F400 + level`). That is how play-from-here works.

## Gotchas (each found by failing first)

1. **Snes9x aborts the whole process if `retro_run` runs before a game is loaded.** snes-lab guards
   it and runs each core in its own process; a crashed session says so and needs `emu_open` again.
2. **One libretro core per process, one thread.** Callbacks are process-global. Don't drive a core
   from MCP tool threads directly; that is why sessions are child processes.
3. **Teleporting Mario or the camera does not redraw layer 1.** Glide about 4 px per frame for
   screenshots (`smw_tour` does).
4. **Horizontal levels are 27 tiles tall.** Rows 27+ spill into the next screen's Map16.
5. **Extended object 0x82 (bush) overwrites ground with tile 0x000**, so Mario falls through.
6. **Munchers hurt even with a Star.** Glide Mario above them (`smw_tour` uses y = 0x100).
7. **After a sprite-tileset change, level entry takes longer.** Poll game mode `$0100 == 0x14`;
   never wait a fixed number of frames.
8. **Savestates carry across ROMs with the same base.** Boot once, then load a patched ROM and
   restore the state: in-level in ~0.1 s. `smw_enter_level` caches the boot state on disk.
9. **Enemy graphics depend on the sprite tileset** (header byte 3, low nibble). To learn which
   enemies draw in which set: render each in all 16 sets with `experiment_run` + a `crop`, then
   eyeball one picture per group.
10. **RAM writes in a tour are a layout check, not a play claim.** Only `route_search` /
    `route_replay` results (controller inputs only, RAM read-only) show a level is beatable.
11. **The default route goal is the goal tape.** Many levels (sublevels like 0C7, pipe or keyhole
    exits) have none: check `smw_level` for object 0x15 / sprite 0x7B first, or pass your own goal.
    Search speed is about one step (8 frames) per second at beam 20; give long levels a midway start
    (`smw_enter_level ... midway=true`) or a bigger `time_limit`.
12. **Asar encodes a branch to a number as a raw offset.** `BRA $1FF010` at `$1FF000` assembles to
    `80 10`, not `80 0E`. Branch to labels. `asm_patch` warns (it doesn't scan incsrc'd files).
13. **The 65816 can't be disassembled without the M/X flags.** Reset and many NMI handlers start
    8-bit (`m8`/`x8` true); routines called from 16-bit code usually start 16-bit. If an `LDA #`
    swallows the next opcode, rerun `rom_disasm` with the other flags.
14. **SA-1 games (Kirby Super Star, map mode $23):** banks `$C0-$FF` map ROM linearly in 64 KiB
    banks; snes-lab uses the `sa1` mapping for them automatically. SA-1 I-RAM ($3000-$37FF) is not
    readable through the libretro core (use `mesen_trace` with `cpu: "sa1", memory: "iram"`); BW-RAM
    is `kind: "sram"` with byte offsets (`$40:001C` or `$6000+$1C` = offset `0x1C`).
15. **Breakpoints are Mesen only.** Snes9x's libretro core has no debugger: `emu_watch` finds the
    frame a value changes, then `mesen_trace` with a write watch on it gives the instruction (`pc`)
    and registers. Mesen runs from power-on or a Mesen savestate (`.mss`, made with save_state=true);
    Snes9x states don't load there, so reach the scene with `inputs` once and save a state.
16. **Mesen's pc in a read/write hit is the next instruction.** `pc` is the writer worked out from
    it; it is null for DMA writes (pc is wherever the CPU was) and code running from RAM.
17. **SuperFX games (Star Fox, Yoshi's Island, Stunt Race FX) draw nothing in snes-lab's Snes9x core.**
    Use the mesen_* tools for them (`screenshot=true` shows the frame).
18. **A code/data log only knows what ran.** `mesen_cdl` over the title screen says nothing about
    level code: play to the part you study. Lines `rom_disasm(cdl=...)` marks "not run in the log"
    still use the guessed flags.

## Where things live

Defaults, each overridable by an environment variable (see the README):

- SMWDisX: `~/.cache/snes-lab/SMWDisX` (`scripts/fetch_smwdisx.sh`, or `SNES_LAB_DISASM`).
- Snes9x libretro core: `~/.cache/snes-lab/snes9x_libretro.{dylib,so}` (`scripts/build_core.sh`,
  or `SNES_LAB_CORE`).
- Asar 1.91: `asar` on PATH or `~/.cache/snes-lab/asar` (`scripts/build_asar.sh`, or `SNES_LAB_ASAR`).
- MesenCE 2.2.1 (portable): `~/.cache/snes-lab/mesen/Mesen.app` (`scripts/fetch_mesen.sh`, or
  `SNES_LAB_MESEN`).
- Romset sweep: `scripts/romset_sweep.py [--boot]` over `$SNES_CORPUS`, a folder of your own dumps.
- Outputs (screenshots, sheets, patches, routes): `~/.cache/snes-lab/out` (`SNES_LAB_OUT`).
