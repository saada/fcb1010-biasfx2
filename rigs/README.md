# rigs/ — one TONE3000 preset per file

Every number we tune lives in **`tone.csv`**, one sheet; the JSONs keep captures, ids, files and research.
- **Add columns** (`add dB`: levels, `in_db`, `eq_*`): a `*` or `@tag` cell is an offset added to each preset's own value.
- **Set columns** (`set …`: switches, echo, ambience, gate, tone stack): a `*` or `@tag` cell is the default for presets whose own cell is empty.

Precedence: JSON → `*` → `@tag` rows (file order) → the preset's row. Empty cell = inherit.

Workflow, from words to sound:
1. Say it: "make all cleans fatter".
2. `uv run tone3000.py tune @clean fatter` edits one row of tone.csv and prints each affected preset's before → after (`x2` doubles, `--undo` reverts).
3. `sim`: render the affected presets offline (`affected()` + `build_presets()`) before `build` puts them on the rig.

`tone3000.py build` turns every `NN-slug.json` here into a TONE3000 preset and
loads them into PC order (`pc` 0–22; bank 0 = Maiden scene presets, banks 1–4 = song
switches: 80s PC 3–7, 80s Clean PC 18–22, Variety PC 8–12, Modern PC 13–17; `rig.py` SONGS
maps PCs to switches, so a new bank takes the next free PCs). `rigs/archive/` keeps retired presets (the seven Maiden era banks and the song
presets no longer on the board) for their research; the builder only reads `rigs/*.json`. Captures and
IRs come from the public [TONE3000](https://www.tone3000.com) catalog by id.

## Research helpers

```
python3 tone3000.py search <words> [--gear=amp|amp-cab|pedal|cab|outboard|space|experimental] [--arch=2|1|any]
python3 tone3000.py models <tone_id> [...]     # description + every model id/name
python3 tone3000.py check [rigs/NN-x.json ...] # validate + resolve + build in memory
```

`--gear=amp-cab` is a full rig (amp + cab); `amp` is a DI/preamp capture that
needs a `cab` IR; `cab` = speaker IRs; `space` = reverb IRs. Catalog NAM blocks use A2
models (`a2` in `models` output). IR blocks use `a-` models.

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
  "params": { "spreadEnabled": 1.0 }, // optional global-param overrides
  "chorus": {"rate_hz": 0.6, "depth_ms": 4.0, "mix": 0.5, "note": "..."} // optional: the DAW chorus
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
(CC 27) treble. (In the DAW rig Qtractor takes CC 20/21/27 for the wah and octaver, and the
80s banks' SW7 sends CC 31 to the DAW chorus.) EXP B (CC 7) is not a TONE3000 param: it is the
DAW's Volume stage before the limiter, so presets never reset it (experiments D22).

### The DAW chorus (`chorus`)

TONE3000 has no time-varying effects, so the DAW rig (`qtractor_rig.py` `CHORUS`) puts LSP
Chorus Stereo after the heavy TONE3000: two voices, triangle LFO, the sides 180° apart, 7 ms
base delay, bypassed by default and toggled by SW7 (CC 31) in the 80s and 80s Clean banks. A
preset's optional `chorus` block sets its `rate_hz` (0.01–20), `depth_ms` (0.1–20) and `mix`
(0–1, the wet share); missing keys take `CHORUS_DEFAULT` (0.6 Hz, 4 ms, 0.5, a slow CE-2-style
80s chorus). `qtractor_rig.chorus_ccs(pc)` turns them into CC 89/90/91, which Qtractor maps
linearly onto the ports (`check` validates the ranges). The chorus is not in tone.csv or the
simulator.

### Block types

| type | fields |
|---|---|
| `nam` | `tone_id`, `model_id` (A2), `enabled`, optional `mix`, `eq`, `eq_pre`, `label`, `in_db` (drive the capture harder/softer, ±24), `out_db`, `file` (see Local files) |
| `ir` | `tone_id`, `model_id`, `enabled`, optional `mix` (wet/dry), `trim_seconds` (long reverbs: trims to mono N s), `eq`, `label`, `file` |
| `echo` | generated analog (BBD) delay IR: `delay_ms`, `feedback` (0–0.8), `cutoff_hz` (repeat darkening), `mix`, `enabled`, `label`; optional `reverb` = `{tone_id, model_id, trim_seconds, level_db}` (a catalog reverb IR summed into the same block, so SW10 toggles delay *and* reverb). `delay_ms` 0 = reverb only |
| `insert` | empty slot |

`eq` = 6 gains in dB for bands [lowshelf 100 Hz, bell 250, bell 650, bell 1600,
bell 3500, highshelf 8 kHz]; `eq_pre: true` applies it before the model (e.g.
humbucker → single-coil voicing `[-3, -2.5, 0, 1.5, 3, 2]`).

### Local files (packs outside the catalog)

A `nam` or `ir` block may add `"file"`, the path of a `.nam` model or IR `.wav` on this
machine. At build time the file replaces the block's catalog `tone_id`/`model_id` (keep
those as the fallback and the credit) and is embedded in the preset like a catalog model.
A missing file fails `check` and `build` with its path; delete the field to fall back.

```jsonc
{"role": "amp", "type": "nam", "tone_id": 1627, "model_id": 425687,
 "file": "~/Music/fcb-rig/packs/ola/randall-satan-50.nam", "label": "...", "out_db": 0.0}
```

Drop folder: `~/Music/fcb-rig/packs/<pack>/`, e.g. `packs/ola/` for Ola Englund's free
Randall Satan 50 and Solar CHUG models (olaenglundshop.com). Pack files never go in git:
the repo is public and those packs grant no redistribution. TONE3000 0.0.12 plays both NAM
generations, A2 (`SlimmableContainer`) and A1 (plain `WaveNet`), so an older free `.nam`
works. A1 files may lack loudness metadata, so re-level with `out_db` after a swap
(experiments D17: the A1 Satan 50 played 5.7 dB under its A2 twin).

## Tone sheet (`tone.csv`)

`uv run tone3000.py csv` prints what every preset resolves to (`L/R` where its two chains
differ, `-` where it has no such block); `column -t -s, rigs/tone.csv` shows the raw sheet.
Rows: the header, a `#rule` row (each column's rule and unit), `*`, any `@tag` rows, then one row
per preset keyed by its JSON stem. A preset's `tags` cell (space separated: bank `maiden` `80s`
`80s-clean` `variety` `modern`; the 80s Clean presets carry `80s` too, character `clean` `crunch` `heavy` `lead` `acoustic`, `stereo`) is what
`@tag` rows match; edit the tags freely. `check` rejects unknown stems, columns and tags,
non-numbers and out-of-range cells, and names the line, row and column.

| column | sets | rule |
|---|---|---|
| `out_db` | preset fader: added to each chain's end block (its cab, or its amp when the cab slot is empty) | add, and the preset cell adds too |
| `in_db` | drive into every amp capture (`in_db` of the amp blocks, both chains) | add |
| `boost_db` `drive_db` `amp_db` `cab_db` | `out_db` of slots 1–4, left/mono chain | add |
| `r_amp_db` `r_cab_db` | `out_db` of R1, R2 | add |
| `eq_100` `eq_250` `eq_650` `eq_1k6` `eq_3k5` `eq_8k` | the six `eq` bands (dB) of each chain's end block | add |
| `boost_on` `drive_on` `echo_on` | slot 1, slot 2, slot 5 + R3 on at preset load (0/1) | set |
| `echo_ms` `echo_fb` `echo_mix` `echo_cutoff` | the echo blocks' `delay_ms`, `feedback`, `mix`, `cutoff_hz` (slot 5 + R3) | set |
| `amb_mix` | `mix` of the ambience IRs | set |
| `gate_db` `gate_hold` `gate_release` `gate_range` | `gateThreshold` (beats `GLOBAL_PARAMS`), `gateHold`, `gateRelease`, `gateRange` | set |
| `bass` `mid` `treble` | the TONE3000 tone stack, 0–10, 5 = flat | set |

A preset cell overrides its blocks on both chains. Where the two chains differ today (U2 and
Radiohead echoes, Van Halen I ambience, the acoustic's cab EQ), `csv --init` left the cell empty
and the JSON keeps the numbers; `*` and `@tag` offsets still reach both chains. Pre-model EQ
voicings (`eq_pre`), `echo2`, reverb tails and `params` stay in the JSON.
`csv --init [--force]` regenerates the sheet from the JSONs. The bootstrap built all 19 presets
byte for byte as before, also with every CSV-covered JSON field deleted.

`tune` moves come from **`vocab.csv`** (word, column steps, why): fatter, thinner, brighter,
darker, tighter, looser, more/less attack, more/less gain, less fizz, warmer, more air, scooped,
mid-forward. A `*`/`@tag` add cell moves its offset; an empty preset cell starts from the
preset's JSON value; a set cell starts from today's resolved value. A tune that would take a
value out of range leaves tone.csv unchanged. History is `rigs/.tune-history` (gitignored).
Hooks for the simulator and docs, in tone3000.py: `resolved(name)` (merged settings, params and
resolved blocks), `affected(target)` (preset names a `*`/`@tag`/preset target reaches) and
`build_presets(names, out_dir)` (the `.t3kpreset` files, never into `~/.config/TONE3000`).

### "More attack"

| lever | move | rough effect on pick attack | basis |
|---|---|---|---|
| `eq_3k5` | +1.5 dB | +1.5 dB at 3.5 kHz and about +0.7 dB at 2.5 and 5 kHz (bell, Q 1.4): the pick click rises against the note body. The cleanest lever. | EQ curve; not yet measured on the rig |
| `in_db` | −1 to −3 dB | On high-gain captures less drive means less of the capture's own compression, so each pick stands further above the sustain; +3 dB does the reverse (more grind, softer edge). On clean or edge-of-breakup captures, + adds bite instead. Re-level with `out_db`. | not yet measured |
| `gate_hold` / `gate_release` | 50 / 100 → 10 / 15 ms | Leaves the onset alone (picks at −30 to −50 dBFS pass with 0.0 dB change; a −60 dBFS pick loses 4.9 dB) but ends each chug sooner, so the next one starts from silence and reads punchier. | experiments D16, simulated |
| bus compressor | keep bypassed | Its −18 dB threshold, 3:1 ratio and 10 ms attack take a pick 6 dB over threshold down by about 4 dB after the first 10 ms; it flattened palm mutes ("too muffled"). It is one bus for all presets (`qtractor_rig.py` `COMPRESSOR`), not a tone.csv column. | experiments D16 |

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
Output level (+12 dB) and gate (on at −60 dB) are global (`GLOBAL_PARAMS`) and win over `params`; tone.csv's gate and tone-stack columns win over both.
`outputLevel` is a fixed per-preset level, not the volume pedal: EXP B drives the DAW's Volume
stage on top of it (0 dB at full toe and until the pedal moves, heel silent; `fcb_router.py`, D22).

IRs that aren't 48 kHz, or whose data chunk has an odd byte length, load as *silence*
in TONE3000; the builder re-encodes them
automatically (mono, first channel), so any catalog IR is usable.

TONE3000 has no modulation, wah or delay blocks (chorus/flanger/phaser/wah): use Spread/Align
for chorus-like movement and note the gap in `notes`; for a real chorus, give the preset a
`chorus` block (the DAW chorus above). Since v0.0.11 it can pitch-shift the whole
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
