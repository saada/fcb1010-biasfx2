# rigs/ — one TONE3000 preset per file

`tone3000.py build` turns every `NN-slug.json` here into a TONE3000 preset and
loads them into PC order (`pc` 0–14 = FCB1010 song switches). Captures and
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
| 5 | `echo` — solo delay | SW10 (CC 24) | off |
| 6+ | `ambience` — reverb/space IRs, extra always-on blocks | — | on |

Right chain (only with `split_after`): R1 `amp`, R2 `cab`, R3 `echo` (also
SW10), R4+ `ambience`. Everything before the split (slots 1..N) feeds both.
Other globals: SW6 (CC 20) noise gate, SW7 (CC 21) `spreadEnabled`, EXP A
(CC 27) treble, EXP B (CC 7) output level.

### Block types

| type | fields |
|---|---|
| `nam` | `tone_id`, `model_id` (A2), `enabled`, optional `mix`, `eq`, `eq_pre`, `label` |
| `ir` | `tone_id`, `model_id`, `enabled`, optional `mix` (wet/dry), `trim_seconds` (long reverbs: trims to mono N s), `eq`, `label` |
| `echo` | generated analog (BBD) delay IR: `delay_ms`, `feedback` (0–0.8), `cutoff_hz` (repeat darkening), `mix`, `enabled`, `label` |
| `insert` | empty slot |

`eq` = 6 gains in dB for bands [lowshelf 100 Hz, bell 250, bell 650, bell 1600,
bell 3500, highshelf 8 kHz]; `eq_pre: true` applies it before the model (e.g.
humbucker → single-coil voicing `[-3, -2.5, 0, 1.5, 3, 2]`).

### Useful `params`

`spreadEnabled`, `spreadWobble` (0–1), `spreadWobbleEnabled` — stereo
widening / chorus-like movement. For dual rigs: `chainPanLeft` 0 /
`chainPanRight` 1, `alignEnabled`, `alignWobbleEnabled`, `alignWobble`
(inter-rig chorus). `toneBass`/`toneMid`/`toneTreble` (0–10, 5 = flat).
Output level and gate (+24 dB, on at −35 dB) are global and always win.

IRs that aren't 48 kHz load as *silence* in TONE3000; the builder resamples them
automatically (mono, first channel), so any catalog IR is usable.

TONE3000 can't do time-varying effects (chorus/flanger/phaser/wah/pitch):
use Spread/Align for chorus-like movement and note the gap in `notes`.
