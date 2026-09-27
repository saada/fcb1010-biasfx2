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

## The layout: full pedal map

Declared once, as data, in `rig.py` (`uv run rig.py show` prints it). Everything is on
MIDI channel 1, except PC 2 (channel 2, the DAW's clean rig) and PC 3 (channel 3, its
harmony rig).

```
SONG BANKS 00-02: one song per switch
 BANK 00  SW1 Comfortably Numb  SW2 Purple Rain  SW3 Tornado of Souls  SW4 Dream Theater  SW5 Slipknot
 BANK 01  SW1 Djent             SW2 Radiohead    SW3 Oasis             SW4 Nirvana        SW5 Foo Fighters
 BANK 02  SW1 Iron Maiden (88)  SW2 My Clean     SW3 Petrucci Clean    SW4 Acoustic       SW5 Glassy Clean
          SW6 WAH   SW7 OCTAVER   SW8 LEAD (boost + echo)   SW9 DRIVE   SW10 TUNER

IRON MAIDEN BANKS 03-09: one album era per bank, same five scenes everywhere
 03 Number of the Beast  04 Piece of Mind  05 Powerslave  06 Somewhere in Time
 07 Seventh Son          08 Fear of the Dark               09 Brave New World / Dance of Death
          SW1 RHYTHM   SW2 SOLO   SW3 CLEAN   SW4 ACOUSTIC   SW5 CRUNCH
          SW6 WAH      SW7 HARMONY  SW8 BOOST  SW9 DELAY + REVERB  SW10 TUNER

EVERY BANK:  EXP A = wah sweep (CC 27)    EXP B = volume (CC 7)
```

| Switch | Sends | Does |
|---|---|---|
| Song SW1–5 (00–02) | PC 0–14, CC 80 = 63, CC 81 = 0 | loads the song; resets the scene state (heavy rig, Solo block off) |
| Maiden SW1 RHYTHM | PC heavy + PC clean (ch 2) + PC harmony (ch 3), CC 80 = 63, CC 81 = 0 | loads the era; twin-guitar rhythm |
| Maiden SW2 SOLO | CC 80 = 72 | amps pushed, +2 dB, the song's lead echo (no PC, so it's instant) |
| Maiden SW3 CLEAN | PCs + CC 81 = 127 | the era's reverby clean |
| Maiden SW4 ACOUSTIC | PCs (acoustic on ch 2) + CC 81 = 127 | electric-to-acoustic |
| Maiden SW5 CRUNCH | CC 80 = 48 | volume-knob-down drive |
| SW6 WAH | CC 20 (toggle) | Guitarix wah (DAW); EXP A sweeps it |
| SW7 OCTAVER / HARMONY | CC 21 / CC 25 (toggle) | octave down in the song banks; twin-lead harmony in the Maiden banks |
| SW8 LEAD / BOOST | CC 26 / CC 22 (toggle) | song banks: boost + solo echo together; Maiden: the boost pedal |
| SW9 DRIVE / DELAY + REVERB | CC 23 / CC 24 (toggle) | song banks: the song's drive pedal; Maiden: the era's delay + reverb |
| SW10 TUNER | CC 28 (toggle) | mutes the rig and opens Chromatic (a GTK4 tuner) on the clean input; press again to play |

**Whose solo?** Both guitarists play at once, Murray left and Smith or Gers right. To tell
the soloists apart, switch pickups: neck for Murray's fluid legato, bridge for Smith's and
Gers' bite. SOLO then adds the push and the echo on top.

## Practice guide

### Once

1. **Install** the packages from "DAW rig" below, plus Chromatic for the tuner. Run `./bootstrap.sh` to build every preset and the session.
2. **Program the FCB1010:**
   - Write the layout: `python3 rig.py syx ~/Music/fcb-rig/fcb1010-maiden.syx`.
   - Put the pedal in receive mode: hold DOWN at power-on, tap UP to CONFIGURATION, then tap SW7 (SYSEX RCV).
   - Send it: `amidi -p hw:2,0 -s ~/Music/fcb-rig/fcb1010-maiden.syx`.
   - Hold DOWN to save.
   - Recalibrate the pedals: hold SW1+SW5 at power-on, then heel and toe each pedal.
3. **Real-time audio:** add your user to the `realtime` group (`realtime-privileges`) and log out and back in. `~/.config/pipewire/jack.conf` turns rtkit off for JACK apps, because rtkit's 200 ms cap kills Qtractor.
4. **Gain staging:** set the Scarlett's monitor knob to your loudest comfortable level. The Linux output (sink) stays at 100 %. Play Spotify or YouTube at 100 %: the guitar is levelled to sit just above it.

### Every session

1. Plug in the Scarlett (guitar in **Input 2**) and the FCB, then run `python3 qtractor_rig.py up`. It takes about 10 s and starts on the Maiden 82 bank.
2. **Tune:** press SW10. The rig mutes and Chromatic opens on your guitar. Tune, then press SW10 again.
3. **Pick a bank** with UP/DOWN, and press **RHYTHM, CLEAN or ACOUSTIC** first: those load the era. SOLO and CRUNCH only change drive, so they assume the era is already loaded.
4. Play along with the track in Spotify or YouTube.
5. **Record** a take: `python3 qtractor_rig.py record` / `stop`. It saves the full rig and the dry DI in `~/Music/fcb-rig/`, so you can re-amp later.
6. When you're done: `python3 qtractor_rig.py down`.

### Maiden song recipes

| Song (bank) | How to play it on the board |
|---|---|
| Hallowed Be Thy Name (03) | CLEAN for the arpeggio intro → RHYTHM when the band comes in → SOLO for the leads. HARMONY on the twin lines (E minor). |
| The Trooper (04) | RHYTHM gallop → SOLO for the solo. HARMONY for the main riff's twin line. |
| Rime of the Ancient Mariner (05) | RHYTHM → CLEAN for the quiet middle section → back to RHYTHM → SOLO. |
| Wasted Years (06) | SOLO + SW9 (delay + reverb) for the intro lead → RHYTHM for the verses. |
| The Evil That Men Do (07) | RHYTHM, then HARMONY for the harmonised bridge. |
| Fear of the Dark (08) | CRUNCH + SW9 for the quiet intro melody (750 ms echo) → RHYTHM for the gallop → SOLO. HARMONY is set to D Dorian, but the record plays that melody in unison. |
| Journeyman (09) | ACOUSTIC for the whole song. |

**Two soloists:** use the neck pickup for Murray's fluid legato and the bridge for Smith's and Gers' bite. **Wah** is SW6 plus EXP A; **volume swells** are EXP B.

### When something's off

| Symptom | Fix |
|---|---|
| No sound | The tuner mute may be on: press SW10. Otherwise check Input 2 and the Scarlett's monitor knob. |
| Wrong era or tone after changing bank | Press RHYTHM, CLEAN or ACOUSTIC to load the era. |
| Crackles or dropouts | Close the tuner (SW10). Log out and in so Qtractor gets real-time priority. Avoid heavy apps while playing. |
| Guitar too loud or quiet next to the music | Use the Scarlett monitor knob for overall level and EXP B for the guitar; leave the Linux volume at 100 %. |
| The rig won't start | `python3 qtractor_rig.py down`, then `up`. For details: `journalctl --user -u qtractor-rig`. |

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
sudo pacman -S --needed qtractor x42-plugins-lv2 lsp-plugins-lv2 guitarix flatpak
flatpak install flathub io.github.nate_xyz.Chromatic   # the SW10 tuner
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
sweep. TONE3000 keeps its block toggles (song banks: SW8 LEAD, SW9 drive; Maiden banks: SW8 boost, SW9 delay + reverb) and EXP B output level. SW10 is the tuner. The
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

Every bank has the same footswitches (full map at the top of this README):

```
SW1 RHYTHM   SW2 SOLO     SW3 CLEAN   SW4 ACOUSTIC        SW5 CRUNCH
SW6 wah      SW7 HARMONY  SW8 boost   SW9 delay + reverb  SW10 TUNER
EXP A wah sweep                        EXP B volume
```

Scenes switch instantly. The DAW runs a heavy and a clean TONE3000 side by side, and
each switch sends absolute CCs, never a mid-song preset load (which would leave a
60–170 ms hole). What each switch does:

- **SOLO** pushes the amps and adds +2 dB with a lead echo timed to the era's signature
  song (The Trooper 375 ms, Fear of the Dark 750 ms, …).
- **HARMONY** (toggle) adds the second guitarist, a diatonic third above whatever you play,
  in the song's key, through the partner's own amp: Smith or Gers for that era. It's built
  from x42 autotune stages on the clean DI, and pitch tests put 8/8 notes on the right third.
- **CRUNCH** is the volume-knob-down sound, like the Fear of the Dark intro.
- **CLEAN** and **ACOUSTIC** use the era's reverby clean and electric-to-acoustic presets.
- **Changing song or bank:** press RHYTHM, CLEAN or ACOUSTIC first; each of those loads the era.

All 21 presets are levelled and both guitar sides are balanced to within 0.1 dB
(experiments D8–D10). A small helper (`qtractor_rig.py helper`, started by `up`) applies
each song's echo time and harmony key when the era loads (D11–D12). The gear and its sources are in `rigs/RIGS.md` and each
`rigs/NN-maiden-*.json`.

Upload the FCB layout (`python3 rig.py syx out.syx && amidi -p hw:2,0 -s out.syx`, with the
FCB in SYSEX RCV mode; see RIG-NOTES.md), then `python3 qtractor_rig.py up`. The tuner is
Chromatic (Flathub); the helper opens it on the guitar input. To make it float centred and opaque
on Hyprland (Omarchy), add to `~/.config/hypr/hyprland.lua`:

```lua
o.window("^io\\.github\\.nate_xyz\\.Chromatic$", { float = true, center = true, size = { 1100, 760 },
  tag = "-default-opacity", opacity = "1 1" })
```

## License

MIT. Vendored `lib/fcb1010.py` is MIT © Brian Walton (riban.co.uk).
