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

## TONE3000 side (Linux)

`tone3000.py` builds the same rig for the native TONE3000 plugin (NAM
captures + IRs), using full-stack captures of each player's actual record/live
rig from the public TONE3000 catalog:

```
uv run tone3000.py map        # preview presets and MIDI map
uv run tone3000.py configure  # standalone: JACK 48k/256, guitar input, FCB MIDI in,
                              # mono input, NAM calibration, interface at unity
uv run tone3000.py build      # download captures, write 15 presets + MIDI map
```

Quit TONE3000 first — the standalone rewrites its settings on exit.

What's reverse-engineered:

| File | Role |
|---|---|
| `~/.config/TONE3000/Presets/<id>.t3kpreset` | preset: `T3KB` + JUCE ValueTree binary; each block embeds its tone JSON and the raw `.nam`/`.wav` |
| `~/.config/TONE3000/Presets/order.json` | JSON array of `user:<id>` / `factory:<id>`; Program Change N = Nth entry |
| `~/.config/TONE3000/TONE3000.settings` | JUCE standalone settings; `filterState` (JUCE base64) holds plugin state incl. `MidiMappings`, `audioSetup` holds device + enabled MIDI inputs |

TONE3000 has no wah/modulation/delay and maps CCs globally by block
*position*, so every preset shares one layout: block 1 lead boost (CC 22),
block 2 song drive (CC 23), block 3 full stack; CC 20 noise gate, CC 21 stereo
spread, CC 27 treble sweep, CC 7 output level. Toggles flip on any value ≥ 64,
so the stock FCB1010's constant 127 works. The tuner isn't MIDI-mappable.

## DAW rig (Qtractor, Linux)

`qtractor_rig.py` puts the same TONE3000 presets inside Qtractor. It adds real
pedals, a compressor and limiter after the amp, and recording. The whole session
is generated, so there is nothing to click:

```
sudo pacman -S --needed qtractor x42-plugins-lv2 lsp-plugins-lv2 guitarix
python3 qtractor_rig.py up       # write ~/Music/fcb-rig/rig.qtr, launch at quantum 256
python3 qtractor_rig.py record   # take: processed stereo rig + dry DI
python3 qtractor_rig.py stop
python3 qtractor_rig.py down     # save + quit
```

```
Scarlett In 2 ─► Wah ─► TONE3000 (CLAP) ─► Octaver ─► Compressor ─► Limiter ─► Scarlett out
FCB1010 ─► PC/CC straight into TONE3000; Qtractor binds SW6 wah, SW7 octaver, EXP A wah sweep
```

This brings back the FCB layout's original intent: SW6 wah, SW7 octaver, EXP A wah
sweep. TONE3000 keeps SW8 boost, SW9 drive, SW10 echo and EXP B output level. The
compressor and limiter cut the loudness spread across presets from 9.2 to 4.6 dB and
keep peaks under −1 dBFS; see `experiments/README.md` D0–D4. Run the standalone
*or* the DAW, not both.

### Iron Maiden banks (03–09): one album era per bank, instant scenes

| Bank | Era | Rigs (Murray left, partner right) |
|---|---|---|
| 03 | The Number of the Beast (1982) | Marshall JMP 2204 / JMP 1987 jumpered |
| 04 | Piece of Mind (1983) | '76 JMP 50 W full rig / JMP 2204 |
| 05 | Powerslave (1984) | Marshall 1987 50 W / JMP 2203 |
| 06 | Somewhere in Time (1986) | Gallien-Krueger 250ML ×2 |
| 07 | Seventh Son (1988) | GK 250ML / GK 2000CPL |
| 08 | Fear of the Dark (1992) | JCM900 4100 / JCM800 2203 (Gers) |
| 09 | Brave New World / Dance of Death | Marshall JMP-1 / JCM2000 DSL |

Every bank has the same footswitches:

```
SW1 RHYTHM   SW2 SOLO   SW3 CLEAN   SW4 ACOUSTIC   SW5 CRUNCH
SW6 wah      SW7 octaver  SW8 boost  SW9 drive     SW10 delay + reverb
EXP A wah sweep                      EXP B volume
```

Scenes switch instantly. The DAW runs a heavy and a clean TONE3000 side by side, and
each switch sends absolute CCs, never a mid-song preset load (which would leave a
60–170 ms hole). What each switch does:

- **SOLO** pushes the amps and adds +2 dB with a 380 ms lead echo.
- **CRUNCH** is the volume-knob-down sound, like the Fear of the Dark intro.
- **CLEAN** and **ACOUSTIC** use the era's reverby clean and electric-to-acoustic presets.
- **Changing song or bank:** press RHYTHM, CLEAN or ACOUSTIC first; each of those loads the era.

All 21 presets are levelled and both guitar sides are balanced to within 0.1 dB
(experiments D8–D10). The gear and its sources are in `rigs/RIGS.md` and each
`rigs/NN-maiden-*.json`.

Upload the new FCB layout once (`uv run rig.py send`, see RIG-NOTES.md), then
`python3 qtractor_rig.py up`.

## License

MIT. Vendored `lib/fcb1010.py` is MIT © Brian Walton (riban.co.uk).
