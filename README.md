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
BANK 0  MAIDEN (home bank, Brave New World / Dance of Death tone)
          SW1 RHYTHM   SW2 LEAD     SW3 CLEAN        SW4 ACOUSTIC       SW5 CRUNCH
          SW6 WAH      SW7 HARMONY  SW8 EVIL DELAY   SW9 MADNESS DELAY  SW10 TUNER

BANK 1  80s: one song preset per switch
          SW1 VAN HALEN I   SW2 VAN HALEN 1984   SW3 DEF LEPPARD PYROMANIA   SW4 DEF LEPPARD HYSTERIA   SW5 U2 STREETS
          SW6 WAH      SW7 CHORUS   SW8 BOOST        SW9 DELAY          SW10 TUNER

BANK 2  80s CLEAN: the chorused cleans, one song preset per switch (one UP from the 80s bank)
          SW1 IS THIS LOVE   SW2 RULE THE WORLD   SW3 EVERY BREATH YOU TAKE   SW4 HYSTERIA CLEAN   SW5 THIS CHARMING MAN
          SW6 WAH      SW7 CHORUS   SW8 BOOST        SW9 DELAY          SW10 TUNER

BANK 3  VARIETY: one song preset per switch
          SW1 COMFORTABLY NUMB   SW2 RADIOHEAD   SW3 NIRVANA   SW4 DJENT   SW5 PURPLE RAIN
          SW6 WAH      SW7 OCTAVER  SW8 LEAD (boost + echo)  SW9 DRIVE  SW10 TUNER

BANK 4  MODERN: Randall Satan sounds (Ola Englund's rig) and Glenn Fricker's Stormblade, one preset per switch
          SW1 SATAN FULL RIG   SW2 SATAN 50 MODERN   SW3 SATAN 50 LOW TUNED   SW4 SATAN 50 LEAD   SW5 STORMBLADE
          SW6 WAH      SW7 OCTAVER  SW8 LEAD (boost + echo)  SW9 DRIVE  SW10 TUNER

EVERY BANK:  EXP A = wah sweep (CC 27)    EXP B = volume (CC 7)
```

| Switch | Sends | Does |
|---|---|---|
| Maiden SW1 RHYTHM | PC 0 + PC 1 (ch 2) + PC 23 (ch 3), CC 80 = 63, CC 81 = 0 | loads the bank; twin-guitar rhythm (Murray JMP-1 left, Smith JCM2000 right); turns both delays off |
| Maiden SW2 LEAD | CC 80 = 72 | amps pushed and +2 dB; no echo of its own (no PC, so it's instant) |
| Maiden SW3 CLEAN | PCs + CC 81 = 127 | the reverby clean (its own 410 ms echo is always on) |
| Maiden SW4 ACOUSTIC | PCs (acoustic on ch 2) + CC 81 = 127 | electric-to-acoustic (Journeyman) |
| Maiden SW5 CRUNCH | CC 80 = 48 | volume-knob-down drive |
| Maiden SW8 EVIL DELAY | CC 24 (toggle) | The Evil That Men Do: 375 ms, feedback 0.3, 25 % wet, bright, short plate tail |
| Maiden SW9 MADNESS DELAY | CC 30 (toggle) | Can I Play with Madness: 415 ms, feedback 0.35, 25 % wet, darker, plate tail |
| Song SW1–5 (banks 1–4) | PC 3–7 (80s), 18–22 (80s Clean), 8–12 (Variety), 13–17 (Modern); CC 80 = 63, CC 81 = 0 | loads the song; resets the scene state |
| SW6 WAH | CC 20 (toggle) | Guitarix wah (DAW); EXP A sweeps it |
| SW7 HARMONY / CHORUS / OCTAVER | CC 25 / CC 31 / CC 21 (toggle) | Maiden: twin-lead harmony a third up in E minor; 80s and 80s Clean: a real stereo chorus (LSP, DAW) at the song's rate, depth and mix; Variety/Modern: octave down |
| 80s / 80s Clean SW8 BOOST / SW9 DELAY | CC 22 / CC 24 (toggle) | the preset's lead boost; the song's delay (on by default in U2 Streets, Is This Love, Rule the World and Hysteria Clean) |
| Variety/Modern SW8 LEAD / SW9 DRIVE | CC 26 / CC 23 (toggle) | boost + solo echo together; the song's drive pedal (Modern: the Fortin 33 or Solar CHUG) |
| SW10 TUNER | CC 28 (toggle) | mutes the rig and opens the Fretwise tuner (Omarchy bar plugin) on the clean input; press again to play |

The two Maiden delays are blocks in the heavy TONE3000 preset, so they work in RHYTHM, LEAD
and CRUNCH, and a RHYTHM press (it reloads the preset) turns them off. They don't reach
CLEAN or ACOUSTIC, which carry their own echo and reverb.

**Whose solo?** Both guitarists play at once, Murray left and Smith or Gers right. To tell
the soloists apart, switch pickups: neck for Murray's fluid legato, bridge for Smith's and
Gers' bite. LEAD then adds the push on top; add a delay with SW8 or SW9.

## GuitarMood: the rig as an Omarchy app

GuitarMood (`guitarmood/`) is the rig's face. Open it and the rig starts; close it and the
rig is saved and shut down. It shows the board as it sits under your feet, live: the bank
and era, what every switch does in that bank, which scene and toggles are on, both
expression pedals, the song's key, tempo and solo echo, and what UP/DOWN would take you to.

```
uv run --project guitarmood guitarmood install   # once: Omarchy launcher entry + Hyprland rules
```

Then **SUPER+SPACE → GuitarMood** turns the rig on and **SUPER+W** turns it off. Launching
it again only focuses the window, so there is never a second rig.

- **Two-way:** click a switch (or press **1–9, 0** for SW1–SW10) and the rig switches exactly as
  if you stomped it. GuitarMood sends the same messages the FCB would, taken from the layout
  `rig.py` uploads, into the same Qtractor port. Clicking a bank in the setlist or the UP/DOWN
  tile (or pressing **↑/↓**) loads that bank's SW1. The FCB1010 itself can't be told what
  happened, so its display keeps its own bank, and GuitarMood marks the press "on screen".
  Toggles, SOLO and CRUNCH work from any bank on the pedal. RHYTHM, CLEAN and ACOUSTIC on the
  pedal load the bank the pedal shows.
- **Autoscale:** everything is sized from the window, so any tile works. A wide tile adds the
  setlist; a small one shrinks to the scene name plus the toggles that are lit.
- **Themed:** colours come from the active Omarchy theme (`colors.toml`) and follow a theme
  switch live. The font is the system monospace.
- **Out of sight:** Qtractor opens on a hidden special workspace; **SUPER+CTRL+G** peeks at it.
- **One source of truth:** GuitarMood does the helper's job itself: it sends the per-song echo
  and harmony key, and runs the SW10 tuner. So the tuner light on screen is the real state.
  If the rig was already running (`qtractor_rig.py up`), GuitarMood joins it. It takes over
  the helper, reads the wah, harmony and tuner states from the running session, and shows
  "?" on anything only a press can tell (bank, scene, TONE3000 blocks) until you press a switch.
- **The state model** follows the rig's own rules. Wah, octaver, chorus and harmony are Qtractor
  plugins and survive a song change. Boost, drive, delay and lead are TONE3000 blocks, so
  every preset load puts them back to the preset's state. A scene is read from the CC 81 that
  ends each scene switch's burst. The FCB's UP/DOWN send no MIDI, so the bank shown is the one
  your last press came from.
- **Clean shutdown:** closing the window, logging out (SIGTERM) or Ctrl-C all close the
  tuner, save the session (SIGUSR1) and quit Qtractor.

Tests replay a real session from the FCB (`guitarmood/tests/fixtures/fcb-live.log`):
`cd guitarmood && uv run --group dev pytest`.

## Practice guide

### Once

1. **Install** the packages from "DAW rig" below and the Fretwise tuner (`omarchy plugin add https://github.com/WayneKruger/omarchy-fretwise.git --enable`). Run `./bootstrap.sh` to build every preset and the session, then `uv run --project guitarmood guitarmood install`.
2. **Program the FCB1010:**
   - Write the layout: `uv run rig.py syx ~/Music/fcb-rig/fcb1010-maiden.syx`.
   - Put the pedal in receive mode: hold DOWN at power-on, tap UP to CONFIGURATION, then tap SW7 (SYSEX RCV).
   - Send it: `amidi -p hw:2,0 -s ~/Music/fcb-rig/fcb1010-maiden.syx`.
   - Hold DOWN to save.
   - Recalibrate the pedals: hold SW1+SW5 at power-on, then heel and toe each pedal.
3. **Real-time audio:** add your user to the `realtime` group (`realtime-privileges`) and log out and back in. `~/.config/pipewire/jack.conf` turns rtkit off for JACK apps, because rtkit's 200 ms cap kills Qtractor.
4. **Gain staging:** set the Scarlett's monitor knob to your loudest comfortable level. The Linux output (sink) stays at 100 %. Play Spotify or YouTube at 100 %: the guitar is levelled to sit just above it.

### Every session

1. Plug in the Scarlett (guitar in **Input 2**) and the FCB, then open **GuitarMood** (SUPER+SPACE). It takes about 10 s and starts on the Maiden bank (bank 0).
2. **Tune:** press SW10. The rig mutes and Fretwise opens in the bar on your guitar. Tune, then press SW10 again.
3. **Pick a bank** with UP/DOWN. In the Maiden bank press **RHYTHM, CLEAN or ACOUSTIC** first: those load it. LEAD and CRUNCH only change drive, so they assume the bank is already loaded. In the 80s, 80s Clean, Variety and Modern banks each of SW1–5 is a song. The 80s Clean bank is one UP from the 80s bank: stomp **SW7 CHORUS** for the chorus these songs live on (it stays on across songs until you press it again). Each song carries its own rate, depth and mix (`chorus` in its preset, sent as CC 89–91 by `qtractor_rig.chorus_ccs` from whatever reads the FCB); until that is wired, the session default plays: 0.6 Hz, 4 ms, 50 % wet.
4. Play along with the track in Spotify or YouTube.
5. **Record** a take: `uv run qtractor_rig.py record` / `stop`. It saves the full rig and the dry DI in `~/Music/fcb-rig/`, so you can re-amp later.
6. When you're done, close GuitarMood (SUPER+W): it saves and stops the rig.

### Song recipes

Maiden songs all play on bank 0, which has the Brave New World / Dance of Death tone.

| Song (bank) | How to play it on the board |
|---|---|
| The Evil That Men Do (0) | RHYTHM, then **SW8 EVIL DELAY** for the melodic lines and the solo (LEAD). HARMONY for the harmonised bridge. |
| Can I Play with Madness (0) | RHYTHM, then **SW9 MADNESS DELAY** + LEAD for the lead lines and the solo. |
| The Wicker Man / Blood Brothers (0) | RHYTHM gallop → LEAD for the solos. HARMONY on the twin lines (E minor). |
| Hallowed Be Thy Name / The Trooper (0) | CLEAN for the Hallowed intro → RHYTHM → LEAD. HARMONY for the twin lines. |
| Fear of the Dark (0) | CRUNCH + SW9 for the quiet intro melody → RHYTHM for the gallop → LEAD. |
| Journeyman (0) | ACOUSTIC for the whole song. |
| Ain't Talkin' 'bout Love / Runnin' with the Devil (1) | SW1 VAN HALEN I. SW8 BOOST for the solos. |
| Panama / Hot for Teacher (1) | SW2 VAN HALEN 1984. SW9 DELAY (319 ms) for the solo. |
| Photograph / Rock of Ages (1) | SW3 PYROMANIA. SW9 DELAY (363 ms) for Photograph's solo. |
| Pour Some Sugar on Me / Armageddon It (1) | SW4 HYSTERIA. SW9 DELAY (529 ms) for the solo. |
| Where the Streets Have No Name (1) | SW5 U2 STREETS: the dotted-eighth delays are already on; play eighths. |

The seven Maiden era banks (Number of the Beast to Dance of Death) are retired. Their research
is in `rigs/archive/`, and the builder skips it.

**Two soloists:** use the neck pickup for Murray's fluid legato and the bridge for Smith's and Gers' bite. **Wah** is SW6 plus EXP A; **volume swells** are EXP B.

### When something's off

| Symptom | Fix |
|---|---|
| No sound | The tuner mute may be on: press SW10. Otherwise check Input 2 and the Scarlett's monitor knob. |
| Wrong era or tone after changing bank | Press RHYTHM, CLEAN or ACOUSTIC to load the era. |
| Crackles or dropouts | Close the tuner (SW10). Log out and in so Qtractor gets real-time priority. Avoid heavy apps while playing. |
| Guitar too loud or quiet next to the music | Use the Scarlett monitor knob for overall level and EXP B for the guitar; leave the Linux volume at 100 %. |
| The rig won't start | GuitarMood shows the error. Close it and open it again; from a terminal, `uv run qtractor_rig.py down`, then `up`. For details: `journalctl --user -u qtractor-rig`. |
| GuitarMood shows "?" | It joined a rig that was already running: press a scene switch and it syncs. |

## FCB1010 side

**The pedal is a fixed address generator, flashed once; all mapping is software.** Every
switch sends only its own address on MIDI channel 16 (bank b: SW1–5 = PC `b*10 + sw-1`, SW6–10 =
CC 102–106 with value `b`; EXP A/B stay CC 27/7 on channel 1). `fcb_router.py`, a persistent
ALSA sequencer client ("FCB Router"), sits between the FCB and Qtractor's "FCB" bus: it turns
each address into exactly what `rig.py`'s layout defines for that switch (plus the song's
chorus CCs after a song's PC), passes everything else through, and reloads the layout within a
second of any edit to `rig.py`, `tone3000.py` or `rigs/`. **A layout change never needs a flash.**

```
FCB1010 ──ch16 address──► FCB Router ──rig.py layout──► Qtractor "FCB" bus ──► TONE3000s, DAW plugins
```

The router runs whenever the rig is up: in-process in GuitarMood, or inside the
`qtractor-rig-helper` unit that `qtractor_rig.py up` starts (never both: GuitarMood stops the
unit when it joins). It connects itself (FCB → router → Qtractor) and drops any direct
FCB → Qtractor link, so nothing arrives twice. If no router runs, GuitarMood's footer says
**FCB ROUTER DOWN** in red. It adds about 0.06 ms (experiments D21).

```
uv run rig.py show              # print the board layout
uv run fcb_router.py table      # every address and what it sends right now
uv run rig.py syx --universal   # write ~/Music/fcb-rig/fcb1010-universal.syx (verified)
uv run rig.py send --port "USB Midi"   # the one-time flash: the universal address map
uv run rig.py monitor           # watch what each press sends (decodes the addresses)
uv run rig.py pull              # read back the device's memory and check it
uv run fcb_router.py bench      # measure the router's added latency on test ports
```

Upload flow (stock firmware, once): hold DOWN while powering on → tap UP until the
CONFIGURATION LED lights → tap footswitch 7 (SYSEX RCV) → `rig.py send --port "USB Midi"` →
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
rig from the public TONE3000 catalog.

The rig is built and measured against **TONE3000 v0.0.12** (linux-x64 release from
[GitHub](https://github.com/tone-3000/tone3000-plugin/releases/tag/v0.0.12), sha256
`c45ea5d64e6eef991b14f1883a4249ed1a883253db1a9d9f0605e0540aba5fc7`). `./bootstrap.sh`
downloads, checks and installs that release; by hand it's `tar xzf` and the tarball's
`./install.sh` (VST3/LV2/CLAP/standalone + factory presets under `~/.config/TONE3000`),
then `uv run tone3000.py configure` to restore the launcher's PipeWire quantum. Don't run
an older build: v0.0.9 sized every IR convolver to the host's *maximum* block (Qtractor
promises 2048 at a 256 quantum), which glitched the acoustic presets (experiments D15).

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
| `~/.config/TONE3000/Presets/<id>.t3kpreset` | preset: `T3KH` + int32 header size + a `T3KPresetHeader` (id, name) ValueTree + the `T3KPreset` body (TONE3000 ≥ 0.0.10; v0.0.9's `T3KB` + body still loads, its id = the file stem); each block embeds its tone JSON and the raw `.nam`/`.wav` |
| `~/.config/TONE3000/Presets/order.json` | JSON array of `user:<id>` / `factory:<id>`; Program Change N = Nth entry |
| `~/.config/TONE3000/TONE3000.settings` | JUCE standalone settings; `filterState` (JUCE base64) holds plugin state incl. `MidiMappings`, `audioSetup` holds device + enabled MIDI inputs |

TONE3000 has no wah/modulation/delay blocks (v0.0.12 adds only a global input pitch
shifter, see TONE3000.md) and maps CCs globally by block
*position*, so every preset shares one layout: block 1 lead boost (CC 22),
block 2 song drive (CC 23), block 3 full stack; CC 20 noise gate, CC 21 stereo
spread, CC 27 treble sweep, CC 7 output level. Toggles flip on any value ≥ 64 (and on a
value < 64 that doesn't follow one ≥ 64, i.e. isn't a release),
so the stock FCB1010's constant 127 works. The tuner isn't MIDI-mappable.

## DAW rig (Qtractor, Linux)

`qtractor_rig.py` puts the same TONE3000 presets inside Qtractor. It adds real
pedals, a compressor and limiter after the amp, and recording. The whole session
is generated, so there is nothing to click:

```
sudo pacman -S --needed qtractor x42-plugins-lv2 lsp-plugins-lv2 guitarix uv
omarchy plugin add https://github.com/WayneKruger/omarchy-fretwise.git --enable   # the SW10 tuner
uv run qtractor_rig.py up       # write ~/Music/fcb-rig/rig.qtr, launch at quantum 256 (GuitarMood does this for you)
uv run qtractor_rig.py record   # take: processed stereo rig + dry DI
uv run qtractor_rig.py stop
uv run qtractor_rig.py down     # save + quit
```

```
Scarlett In 2 ─► Wah ─► TONE3000 (CLAP) ─► Octaver ─► Compressor ─► Limiter ─► Scarlett out
FCB1010 ─► PC/CC straight into TONE3000; Qtractor binds SW6 wah, SW7 octaver, EXP A wah sweep
```

This brings back the FCB layout's original intent: SW6 wah, SW7 octaver, EXP A wah
sweep. TONE3000 keeps its block toggles (song banks: SW8 LEAD, SW9 drive; Maiden banks: SW8 boost, SW9 delay + reverb) and EXP B output level. SW10 is the tuner. The
limiter keeps peaks under −1 dBFS. The compressor is in the chain but bypassed by default:
it cut the loudness spread across presets from 9.2 to 4.6 dB (`experiments/README.md` D0–D4),
but it also flattened palm mutes, and the rig sounds better without it (D16). Run the standalone
*or* the DAW, not both.

### Iron Maiden scenes (history: the seven era banks 03–09, now retired to rigs/archive; bank 0 keeps the 2000s rig and the same scene mechanics)

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
(experiments D8–D10). A small helper (`qtractor_rig.py helper`, started by `up`; GuitarMood does the same job in-process) applies
each song's echo time and harmony key when the era loads (D11–D12). The gear and its sources are in `rigs/RIGS.md` and each
`rigs/NN-maiden-*.json`.

Upload the FCB layout (`uv run rig.py syx out.syx && amidi -p hw:2,0 -s out.syx`, with the
FCB in SYSEX RCV mode; see RIG-NOTES.md), then open GuitarMood (or `uv run qtractor_rig.py up`).
The SW10 tuner is [Fretwise](https://github.com/WayneKruger/omarchy-fretwise), a verified
Omarchy bar plugin. It listens only while its panel is open, so SW10 sets its `captureSource`
to the guitar input and summons the panel (`omarchy-shell shell summon`), and the next press
hides it. Off Omarchy, the helper falls back to Chromatic (Flathub), re-patched onto the guitar.

## License

MIT. Vendored `lib/fcb1010.py` is MIT © Brian Walton (riban.co.uk).
