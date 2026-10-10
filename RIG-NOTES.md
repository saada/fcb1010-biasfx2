# FCB1010 rig for BIAS FX 2

Everything the FCB1010 needs is uploaded over MIDI — no front-panel programming.

**Since the router (October 2026) the pedal is flashed once, with the universal address map,
and never again.** Every switch sends only its own address on MIDI channel 16; `fcb_router.py`
turns each address into the messages `rig.py`'s layout defines, live. Edit the layout
(`SONGS` / `TOGGLES` / banks in `rig.py`, `rigs/`) and it plays at once: no upload.

| Switch | The FCB sends (ch 16) | The router sends |
|---|---|---|
| bank b, SW1–5 | Program Change `b*10 + sw-1` (0–99) | `rig.py layout` for (b, sw): what `build()` puts on the switch, then the song's chorus CCs 89–91 after a ch-1 PC |
| bank b, SW6–10 | CC 102–106 (SW6..SW10), value `b` | the same, e.g. CC 20 = 127 (wah) |
| EXP A / EXP B | CC 27 / CC 7 on ch 1 | passed straight through |

SW6–10 are encoded like the old toggle presets (CC 1 only, no PC), so the LEDs behave as
before. UP/DOWN still send nothing, but every press now carries its bank, so GuitarMood's bank
is exact even on a toggle. Anything not on channel 16 passes through unchanged, so the old
per-layout flash keeps working through the router until the one-time flash.

The one-time flash is the upload below with `uv run rig.py send --port "USB Midi"` (it sends
the universal map; `--legacy` sends the old per-layout config). The same bytes are in
`~/Music/fcb-rig/fcb1010-universal.syx` (`uv run rig.py syx --universal`). After it,
`uv run rig.py pull` says "Device holds the universal address map", and `monitor` prints
`= bank b SWs` next to each press.

## One-time upload

1. Connect the USB MIDI interface, cable from **interface MIDI OUT → FCB1010 MIDI IN**.
2. Put the FCB1010 in receive mode:
   - Hold **DOWN** ~2.5 s while powering on (global config mode)
   - **Tap UP repeatedly — short presses, holding does nothing.** The first
     tap(s) land on the MIDI FUNCTION page; keep tapping until the green
     **CONFIGURATION** LED next to the display lights
   - Tap **footswitch 7** (SYSEX RCV) — its LED stays lit, waiting
3. Run:

   ```
   uv run rig.py send --port "USB Midi"
   ```

   The footswitch 7 LED flashes during transfer, then goes out. Then **hold
   DOWN ~2.5 s — this saves the received settings and exits config mode;
   skipping it loses the upload.** If the LED never flashed, the data didn't
   arrive — check that the cable runs **interface MIDI OUT → FCB1010 MIDI IN**
   and the right port was used, then verify with `monitor` in normal mode.
4. Calibrate the expression pedals — **after** the upload, since the dump resets
   calibration: power off, hold **switches 1+5** while powering on, then follow
   the heel/toe prompts (pedal A min → UP → max → UP, same for pedal B).

## Verify without guessing

Cable from **FCB1010 MIDI OUT → interface MIDI IN**, then:

```
uv run rig.py monitor
```

Press pedals and watch: song switches print `Program Change 0–10`, the toggle
row prints `CC 20–24 = 127`, the pedals sweep `CC 27` / `CC 7`. That output is
exactly what BIAS FX 2 receives.

## Check what's actually on the pedal

> **Known cable limitation (July 2026):** the current no-name MIDI-USB cable
> passes sysex Mac→pedal but silently drops it pedal→Mac. `send` works;
> `pull` cannot — verify uploads with `monitor` instead (channel messages
> pass fine both ways). `pull` needs a proper interface (e.g. Roland UM-ONE mkII).

```
uv run rig.py pull
```

Then on the pedal: global config → tap UP to the **CONFIGURATION** LED → tap
**footswitch 6** (SYSEX SND). Prints `Received N bytes` (2352 = healthy
transfer; anything less means the MIDI-USB cable drops long sysex), saves
`device-backup.syx`, and diffs every switch against this rig. Note: a working
`monitor` only proves short messages pass — `pull`'s byte count is the real
cable test.

## BIAS FX 2 side (also automated)

`biasfx2.py` builds a dedicated **FCB1010 bank** in the app containing all 15
rig sounds in PC order (copies — your originals stay untouched in their
banks), maps PC 0–14 to them, and writes per-preset pedal CC wiring (scheme
from rig.py). Tweak rig tones by editing the FCB1010-bank copies in-app.
Quit BIAS FX 2 before running; it flushes/overwrites these files on exit.

- `uv run biasfx2.py map` — preview which preset each PC hits and which pedal
  each CC toggles
- `uv run biasfx2.py wire` — apply (idempotent; re-run after editing chains
  in-app — new modules get wired on the next run)
- `uv run biasfx2.py pedals` — insert missing rig pedals (wah / octaver /
  harmonizer / volume, per `ADDITIONS`) into the FCB-bank chains with scene
  snapshots, then re-wire. Wire also (re)writes tuner → CC 24 (`utility.tuner`).

Preset choices live in the `TARGETS` table. A timestamped
`biasfx2-backup-*.tar.gz` of all presets sits in this directory.

## Other commands

- `uv run rig.py show` — print the board layout
- `uv run rig.py syx` — write `rig.syx` (2352 bytes, self-verified round-trip);
  loadable in FCB/UnO Control Center or any sysex sender as a fallback

## Layout

| | SW1 | SW2 | SW3 | SW4 | SW5 |
|---|---|---|---|---|---|
| Bank 00 | Comfortably Numb | Purple Rain | Tornado of Souls | Dream Theater | Slipknot |
| Bank 01 | Djent | Radiohead | Oasis | Nirvana | Foo Fighters |
| Bank 02 | Iron Maiden | My Clean | Petrucci Clean | Acoustic | Glassy Clean |

Every bank: SW6 Wah (CC20) · SW7 Octaver/Harmonizer (CC21) · SW8 Delay (CC22) ·
SW9 Distortion (CC23) · SW10 Tuner (CC24). EXP A = wah sweep (CC27), EXP B = volume (CC7).
All on MIDI channel 1. Hitting a song switch again reloads its preset (that's the
"back to base sound" move).

If anything goes sideways: factory reset = hold **switches 1+7** while powering on.

The sysex encoding comes from [riban-bw/fcb1010](https://github.com/riban-bw/fcb1010)
(MIT, verified against real V2.5 hardware), vendored in `lib/`.
