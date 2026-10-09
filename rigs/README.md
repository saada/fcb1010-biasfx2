# rigs/ — one TONE3000 preset per file

`tone3000.py build` turns every `NN-slug.json` here into a TONE3000 preset and
loads them into PC order (`pc` 0–12; bank 0 = Maiden scene presets, banks 1–2 = song
switches). `rigs/archive/` keeps retired presets (the seven Maiden era banks and the song
presets no longer on the board) for their research; the builder only reads `rigs/*.json`. Captures and
IRs come from the public [TONE3000](https://www.tone3000.com) catalog by id.

## Research helpers

```
python3 tone3000.py search <words> [--gear=amp|amp-cab|pedal|cab|outboard|space|experimental] [--arch=2|1|any]
python3 tone3000.py models <tone_id> [...]     # description + every model id/name
python3 tone3000.py check [rigs/NN-x.json ...] # validate + resolve + build in memory
```

`--gear=amp-cab` is a full rig (amp + cab); `amp` is a DI/preamp capture that
needs a `cab` IR; `cab` = speaker IRs; `space` = reverb IRs. NAM blocks must
use A2 models (`a2` in `models` output). IR blocks use `a-` models.

## File format

```jsonc
{
  "pc": 10,                          // program change = FCB song switch
  "name": "Iron Maiden",             // preset name (stable: it keys the preset file)
  "reference": "The Evil That Men Do — Seventh Son of a Seventh Son (1988)",
  "rig": "who played what through what, on that record",
  "sources": ["https://..."],        // where the rig facts come from
  "notes": "what's approximated and why",
  "left":  [ /* blocks, slot 1..n */ ],
  "split_after": 2,                  // optional: dual-rig stereo, split after left slot N
  "right": [ /* blocks, slot R1..n */ ],
  "params": { "spreadEnabled": 1.0 } // optional global-param overrides
}
```

### Slots are fixed (footswitch mappings are positional)

| Left slot | role | FCB | default |
|---|---|---|---|
| 1 | `boost` — lead boost pedal | SW8 (CC 22) | usually off |
| 2 | `drive` — the song's drive pedal | SW9 (CC 23) | on if always-on in the song |
| 3 | `amp` — amp capture (full rig or DI) | — | on |
| 4 | `cab` — cab IR, or `insert` if slot 3 is a full rig | — | on |
| 5 | `echo` — solo delay | Maiden SW8 / 80s SW9 (CC 24) | off |
| 6 | `echo2` — second delay (Maiden heavy only), else `ambience` | Maiden SW9 (CC 30) | off |
| 6+ | `ambience` — reverb/space IRs, extra always-on blocks | — | on |

Right chain (only with `split_after`): R1 `amp`, R2 `cab`, R3 `echo` (same CC as
slot 5), R4 `echo2` (same CC as slot 6) or `ambience`, then `ambience`. Everything before the split (slots 1..N) feeds both.
Other globals: SW6 (CC 20) noise gate, SW7 (CC 21) `spreadEnabled`, EXP A
(CC 27) treble, EXP B (CC 7) output level.

### Block types

| type | fields |
|---|---|
| `nam` | `tone_id`, `model_id` (A2), `enabled`, optional `mix`, `eq`, `eq_pre`, `label`, `in_db` (drive the capture harder/softer, ±24), `out_db` |
| `ir` | `tone_id`, `model_id`, `enabled`, optional `mix` (wet/dry), `trim_seconds` (long reverbs: trims to mono N s), `eq`, `label` |
| `echo` | generated analog (BBD) delay IR: `delay_ms`, `feedback` (0–0.8), `cutoff_hz` (repeat darkening), `mix`, `enabled`, `label`; optional `reverb` = `{tone_id, model_id, trim_seconds, level_db}` (a catalog reverb IR summed into the same block, so SW10 toggles delay *and* reverb). `delay_ms` 0 = reverb only |
| `insert` | empty slot |

`eq` = 6 gains in dB for bands [lowshelf 100 Hz, bell 250, bell 650, bell 1600,
bell 3500, highshelf 8 kHz]; `eq_pre: true` applies it before the model (e.g.
humbucker → single-coil voicing `[-3, -2.5, 0, 1.5, 3, 2]`).

### Useful `params`

`spreadEnabled`, `spreadWobble` (0–1), `spreadWobbleEnabled` — stereo
widening / chorus-like movement. For dual rigs: `chainPanLeft` 0 /
`chainPanRight` 1, `alignEnabled`, `alignWobbleEnabled`, `alignWobble`
(inter-rig chorus). `toneBass`/`toneMid`/`toneTreble` (0–10, 5 = flat).
Gate (TONE3000 ≥ 0.0.11): `gateRelease` (5–500 ms, default here 100), `gateHold` (0–200 ms,
here 50), `gateRange` (20–80 dB, here 80): the builder's defaults reproduce v0.0.9's fixed gate;
a tight high-gain preset can set e.g. release 15 / hold 10. Pitch (≥ 0.0.11, off by default):
`pitchEnabled`, `pitchSemitones` (−24…24), `pitchStep` (1 = whole semitones), `pitchTonality`
(1000–20000 Hz, 20000 = off), `pitchWindow` (0–3 = 20/30/40/60 ms; adds 11/16/21/31 ms latency
while on). Unknown ids are an error. The full baseline is `BASE_PARAMS` in tone3000.py.
Output level (+12 dB) and gate (on at −60 dB) are global (`GLOBAL_PARAMS`) and always win.

IRs that aren't 48 kHz, or whose data chunk has an odd byte length, load as *silence*
in TONE3000; the builder re-encodes them
automatically (mono, first channel), so any catalog IR is usable.

TONE3000 has no modulation, wah or delay blocks (chorus/flanger/phaser/wah): use Spread/Align
for chorus-like movement and note the gap in `notes`. Since v0.0.11 it can pitch-shift the whole
input (see Useful `params`), but with no dry blend and no scale awareness.

## Scene bank (Iron Maiden, FCB bank 0)

The Maiden bank has three presets on the Brave New World / Dance of Death chain (heavy,
clean, acoustic). In it SW8 toggles the heavy preset's slot 5 / R3 echo (The Evil That Men
Do, 375 ms) and SW9 its slot 6 / R4 `echo2` (Can I Play with Madness, 415 ms); SW2 is
labelled LEAD (the `solo` scene) and adds no echo of its own. The DAW rig (`qtractor_rig.py`) runs
two TONE3000s:

| `scene` | loaded into | used by |
|---|---|---|
| `heavy` | heavy instance (PC on MIDI ch 1) | SW1 RHYTHM, SW2 SOLO, SW5 CRUNCH |
| `clean` | clean instance (PC on ch 2) | SW3 CLEAN |
| `acoustic` | clean instance (PC on ch 2) | SW4 ACOUSTIC |

Every scene switch sends absolute values, so switching is instant and always lands
in a known state:

- **CC 80** sets the heavy TONE3000's `inputLevel`: rhythm 63 (unity), solo 72 (amps pushed
  like a boost pedal; above 63 the DAW Solo block adds +2 dB and a 380 ms echo), crunch 48 (guitar
  volume rolled back). A `heavy` preset loads at the rhythm value.
- **CC 81** picks which instance hears the guitar.

Add `"scene"` and `"bank"` to these presets. Because SW10 only reaches the heavy
instance, `clean` and `acoustic` presets keep their delay and reverb switched *on*.

### Per-song settings and generated harmony presets

A heavy scene preset can carry a `song` block, which `qtractor_rig.py helper` sends to the
DAW when the bank loads. `harmony_scale` names the x42 autotune scale that has the same notes,
e.g. E natural minor = "G Major" and D Dorian = "C Major":

```jsonc
"song": {"reference": "The Trooper", "harmony_scale": "G Major", "tempo_bpm": 160,
         "solo_delay_ms": 375, "harmony_trim_db": -0.5, "sources": ["..."]}
```

The builder also generates one `harmony` preset per bank from the heavy preset's partner
chain (amp and cab only), numbered after the files. The FCB loads it on PC channel 3 for the
DAW's harmony TONE3000. Reverb tails in `echo.reverb` and `ambience` blocks are capped at
`IR_TAIL_MAX_S` (2.0 s), because long convolutions cost xruns with three instances.
