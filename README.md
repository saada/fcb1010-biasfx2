# fcb1010-biasfx2

End-to-end automation for a Behringer FCB1010 MIDI foot controller driving
Positive Grid BIAS FX 2 — no front-panel foot-dance, no MIDI-learn clicking.
Two scripts configure both ends from plain files:

- **`rig.py`** programs the FCB1010: generates its full 2352-byte SysEx memory
  dump from a declarative layout and uploads it over MIDI.
- **`biasfx2.py`** programs BIAS FX 2: builds a dedicated preset bank, maps
  Program Changes to presets, wires footswitch CCs to specific pedals, and can
  even insert missing pedals (wah / octaver / harmonizer / volume) directly
  into preset chains.

Both are single-file [uv](https://docs.astral.sh/uv/) scripts — no install, no
virtualenv: `uv run rig.py`.

## The layout

Declared once, as data, in `rig.py`:

```
Every bank:  [6 WAH ] [7 OCT/HARM] [8 DLY] [9 DIST] [10 TUNER]
BANK 00:     five song presets          (PC 0-4)
BANK 01:     five more                  (PC 5-9)
BANK 02:     one more + four cleans     (PC 10-14)

EXP A = wah sweep (CC 27)    EXP B = volume (CC 7)
```

Song switches send a single Program Change; the toggle row sends CC 20–24;
both expression pedals stay live in every preset. Edit the `SONGS` and
`TOGGLES` tables and re-send to change any of it.

## FCB1010 side

```
uv run rig.py show      # print the board layout
uv run rig.py syx       # write rig.syx (verified round-trip)
uv run rig.py send      # upload to the pedal over MIDI
uv run rig.py monitor   # watch what each pedal press actually sends
uv run rig.py pull      # read back the device's memory and diff vs the rig
```

Upload flow (stock firmware): hold DOWN while powering on → tap UP until the
CONFIGURATION LED lights → tap footswitch 7 (SYSEX RCV) → `rig.py send` →
**hold DOWN ~2.5 s to save** → recalibrate the expression pedals (hold 1+5
while powering on). Full details in [RIG-NOTES.md](RIG-NOTES.md).

The SysEx encoding is vendored from
[riban-bw/fcb1010](https://github.com/riban-bw/fcb1010) (MIT), verified
against real V2.5 hardware.

## BIAS FX 2 side

BIAS FX 2 stores everything as JSON under
`~/Documents/PositiveGrid/BIAS_FX2/` — reverse-engineered here:

| File | Role |
|---|---|
| `midi.json` | global map: PC number → preset UUID (`preset.goto`), CC → app action (`utility.tuner`) |
| `GlobalPresets/bank.json` | bank registry (`LiveBanks`) |
| `GlobalPresets/<bank>/preset.json` | bank index (`LivePresets`) — presets invisible until listed here |
| `GlobalPresets/<bank>/<preset>/data.json` | signal chain (`sigPath`), scenes, embedded amps |
| `GlobalPresets/<bank>/<preset>/midi.json` | per-pedal CC wiring (`power_midi` toggle, `midi_param` sweep) |

Key findings that make file-level editing safe:

- A preset's `midi.json` references chain modules by the `id` field in
  `data.json` — stable UUIDs.
- Scene snapshots (`scenes.slot[].iTonesPreset.Fxs`) are keyed by module
  `uniqueid`, **not position**, so inserting modules doesn't corrupt scenes —
  append matching snapshot entries and everything stays consistent.
- PC targets any bank by UUID, so pedal-triggered presets don't have to share
  a bank (this repo builds a dedicated, PC-ordered bank anyway).

```
uv run biasfx2.py map      # preview PC -> preset and CC -> pedal wiring
uv run biasfx2.py wire     # build the bank, write all MIDI mappings
uv run biasfx2.py pedals   # insert missing pedals into chains, then wire
```

Quit BIAS FX 2 before running — it overwrites these files on exit.
`wire` is idempotent and preserves in-app chain edits; re-run it after adding
modules in the app and they get wired automatically.

## Hardware gotcha worth knowing

Cheap no-name USB-MIDI cables can pass 3-byte channel messages perfectly and
**silently drop SysEx — sometimes in one direction only**. A working
`monitor` proves nothing about dumps. `pull`'s byte count is the real test;
if it stays silent while the pedal's transmit LED blinks, the cable eats
SysEx and you need a proper interface (Roland UM-ONE mkII works).

## Adapting it

Fork, then edit the tables at the top of each script: `SONGS`/`TOGGLES` in
`rig.py`; `TARGETS` (source bank/preset UUIDs from your own library) and
`ADDITIONS` in `biasfx2.py`. `TONES.md` documents the reference rig's 15
presets — from Gilmour to djent — as a worked example.

Quit the app and back up `~/Documents/PositiveGrid/BIAS_FX2/` before the
first `wire`.

## License

MIT. Vendored `lib/fcb1010.py` is MIT © Brian Walton (riban.co.uk).
