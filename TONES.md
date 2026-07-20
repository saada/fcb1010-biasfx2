# FCB1010 + BIAS FX 2 — Complete Rig Configuration

## Why it doesn't work out of the box

The FCB1010's factory presets blast **5 Program Changes + 2 CCs simultaneously, on multiple MIDI channels**, every time you press a switch. BIAS FX 2's MIDI Learn grabs whichever message arrives first, so mappings land on random garbage. The expression pedals also ship uncalibrated. The fix: reprogram each FCB1010 preset to send exactly one message, on one channel, and calibrate the pedals.

---

## 1. Hardware + Mac setup

1. FCB1010 has only 5-pin MIDI DIN — connect **FCB1010 MIDI OUT → MIDI IN of a USB MIDI interface** → Mac.
2. Verify the interface shows up: **Audio MIDI Setup.app → Window → Show MIDI Studio**.
3. BIAS FX 2 Standalone: **Settings (gear icon) → MIDI** → enable your MIDI input device. Leave channel at Omni or set to **1**.

## 2. FCB1010 configuration (automated)

All FCB1010 programming — MIDI channels, direct-select off, every preset — is
uploaded in one shot by `rig.py send` (see README.md). No front-panel
programming. Receive-mode navigation gotcha: in global config, **tap** UP with
short presses until the green **CONFIGURATION** LED lights (holding UP does
nothing, and the first taps land on the MIDI FUNCTION page), then tap
footswitch 7 (SYSEX RCV) before sending.

The only manual step is **calibrating the expression pedals, after every
upload** (the dump resets calibration; uncalibrated pedals are why wah feels
dead):
- Firmware 2.4+: hold **footswitches 1 + 5** while powering on.
- Older firmware: hold **1 + 3**.
- Then: pedal A to heel, press UP → pedal A to toe, press UP → same for pedal B. Settings store automatically.

(Firmware version flashes on the display at power-up.)

## 3. The layout

Three FCB banks. Switches 1–5 pick songs, switches 6–10 are an identical effect-toggle row in every bank. Both expression pedals active in **every** preset.

```
Every bank:  [6 WAH ] [7 OCT/HARM] [8 DLY] [9 DIST] [10 TUNER]

BANK 00: [1 Numb ] [2 Rain ] [3 Torn ] [4 DT  ] [5 Slip]
BANK 01: [1 Djent] [2 Radio] [3 Oasis] [4 Nirv] [5 Foo ]
BANK 02: [1 Maiden] [2–5 free for future tones]

EXP A = Wah sweep        EXP B = Volume
```

### MIDI map

| FCB control | Message | BIAS FX 2 target |
|---|---|---|
| Bank 00, SW 1–5 | PC 0–4 | Presets 1–5 |
| Bank 01, SW 1–5 | PC 5–9 | Presets 6–10 |
| Bank 02, SW 1 | PC 10 | Preset 11 (Iron Maiden) |
| Bank 02, SW 2–5 | PC 11–14 | Cleans: My Clean, Petrucci Clean, Acoustic, Glassy Clean |
| SW 6 (every bank) | CC 20, val 127 | Wah on/off |
| SW 7 (every bank) | CC 21, val 127 | Octaver/harmonizer on/off |
| SW 8 (every bank) | CC 22, val 127 | Delay on/off |
| SW 9 (every bank) | CC 23, val 127 | Distortion/boost on/off |
| SW 10 (every bank) | CC 24, val 127 | Tuner (or lead scene — your call) |
| EXP A | CC 27, 0–127 | Wah pedal position |
| EXP B | CC 7, 0–127 | Volume pedal |

BIAS FX 2's switch assignments run in **toggle mode**, so a stock FCB1010 sending the same CC value each press still toggles effects on/off correctly.

## 4. Programming the FCB1010

Fully automated: the layout above lives in `rig.py` as the `SONGS` and
`TOGGLES` tables, and `uv run rig.py send` uploads all 26 presets over sysex.
Verify with `uv run rig.py monitor` (per-press messages) or
`uv run rig.py pull` (reads device memory and diffs against the rig).
Procedures in README.md.

## 5. BIAS FX 2 side

For each of the 11 presets below:

1. Build the signal chain, save the preset. Keep **all 11 in the same BIAS FX 2 bank** — PC only reaches the selected bank.
2. Preset list → hover the preset → **Edit** → set its **PC number** (0–10 per the table).
3. Right-click each mapped pedal → **MIDI Assign**:
   - Wah on/off → CC 20; wah **position** → CC 27 (rock EXP A with Learn, or type it)
   - Octaver on/off → CC 21
   - Delay on/off → CC 22
   - Distortion/boost on/off → CC 23
   - Volume pedal position → CC 7
4. **Re-save the preset** — pedal MIDI assignments store with the preset. Using the same CCs everywhere means the floor layout never changes.

Tuner: Settings → MIDI (global mappings) → assign Tuner to CC 24 if available in your version; otherwise make SW10 a spare toggle (e.g., modulation).

## 6. The 11 presets

Every chain includes, in this base order:
**Wah → Compressor → Octaver → [drives] → Amp → EQ → [modulation] → Delay → Reverb → Volume pedal**
(Volume at the end = swells keep delay/reverb tails. Move it before delay if you prefer cutting dry only.)

Exact module names vary by BIAS FX 2 version/expansion packs — pick the closest match in each category.

### PC 0 — Comfortably Numb (Gilmour solo)
- Amp: Hiwatt-style big clean (a loud Fender-style clean works too), master high, barely breaking up
- Comp (light, sustain), **Big Muff-style fuzz** (sustain ~70%, tone ~40%), TS-style OD after it as a driver (low drive, high level) — map the Muff to **CC 23**
- Flanger/chorus (Electric Mistress vibe, slow, subtle), Delay ~440 ms, feedback 5–6 repeats, mix ~25% (**CC 22**), big plate reverb
- Neck pickup, wah parked but mapped

### PC 1 — Purple Rain (Prince)
- Amp: bright clean (Twin-style), touch of compression
- **Deep stereo chorus** + long hall reverb = the cleans
- Distortion pedal (raunchy, mid-heavy) mapped to **CC 23** + delay (~380 ms, mix 20%) on **CC 22** = the solo
- One preset, two songs' worth: verse clean by default, stomp SW9 (+SW8) for the solo

### PC 2 — Tornado of Souls (Megadeth)
- Amp: JCM800/Mesa Mark-style high gain, tight
- Noise gate (fast), **TS in front**: drive 0–10%, level max (tightens palm mutes) — always on
- EQ: slight low-mid cut, keep upper mids (Friedman leads sing, not scoop)
- Delay ~330 ms mix 15% on **CC 22** for the solo, small room reverb
- Octaver on **CC 21** for harmonized-feel accents

### PC 3 — Dream Theater (Petrucci)
- Amp: Mesa Mark-style (tight, mid-focused high gain), gate, TS boost always on
- EQ: the classic Mark "V" but keep 750 Hz alive
- Chorus (subtle) for cleaner passages, Delay 380 ms dotted feel mix 20% (**CC 22**)
- Octaver (**CC 21**) for unison-octave lines; boost pedal on **CC 23** for leads

### PC 4 — Slipknot
- Amp: Rectifier/5150-style, gain high but gate aggressive (threshold high, fast release)
- TS boost (drive 0, level max), EQ: tight low cut below ~80 Hz, push 2–4 kHz bite
- No reverb, no delay by default (delay on **CC 22** ready for the odd wet part)
- Octaver (one octave down, blend ~40%) on **CC 21** for the extra-heavy passages

### PC 5 — Djent
- Amp: 5150-style, gain *lower* than you think + TS (drive 0, level max), brutal gate
- EQ: high-pass ~90 Hz, presence up, notch mud ~250 Hz; light compressor **after** the amp for that clacky attack
- Ambient scene gear: shimmer-ish long reverb + delay on **CC 22** for clean interludes
- Octaver on **CC 21** (blend low, thickener)

### PC 6 — Radiohead
- Amp: AC30-style, edge-of-breakup
- Tremolo + spacious reverb, analog delay ~300 ms (**CC 22**)
- **Fuzz** (velcro-ish, Paranoid Android freakout) on **CC 23**
- Wah mapped — doubles as a filter-sweep texture tool

### PC 7 — Oasis
- Amp: AC30-style pushed hard (Champagne Supernova wall), or Marshall crunch
- Light compressor, plate reverb, subtle slap delay
- OD pedal on **CC 23** to jump from strummy verse to full wall
- Roll EXP B volume for the dynamics instead of switching

### PC 8 — Nirvana
- Amp: Twin-style clean, LOUD
- **DS-1-style distortion** on **CC 23** (this IS the quiet-verse/loud-chorus switch)
- **Small Clone-style chorus** on… wire it inverted to taste: chorus on for clean verses (Come As You Are), off when distortion hits — map chorus to **CC 21** slot this preset (octaver unused; same footswitch, song-appropriate job)
- Spring reverb, no delay

### PC 9 — Foo Fighters
- Amp: Marshall-style big crunch (Everlong), punchy mids, gain moderate — chords must stay defined
- Boost/OD on **CC 23** for choruses/leads, light delay on **CC 22**, room reverb
- Compressor light, always on

### PC 10 — Iron Maiden (Seventh Son / Brave New World)
- Amp: Marshall JCM-style, gain moderate-high but articulate — the gallop (The Evil That Men Do, The Wicker Man) needs every note defined, so less gain than the metal presets
- Light compressor in front, mids pushed (never scoop Maiden), presence up
- **Harmonizer/pitch pedal on CC 21** (the octaver slot): diatonic 3rds up, key of the song — instant Smith/Murray dual leads. If your version only has a fixed pitch shifter, +4 semitones gets you major-3rd harmonies
- Subtle chorus (the Seventh Son-era polish; also carries the Moonchild clean intro with EXP B swells), delay ~350 ms mix 15% on **CC 22**, plate reverb
- Boost on **CC 23** for solos

## 6b. Guitar Match targets (in-app, ~30s per preset)

Source is always the **jp70** profile. Open each FCB1010-bank preset → input
stage → Guitar Match → pick the target → save. These can't be set from files
(the block only exists once saved in-app), and saved picks survive
`biasfx2.py wire` re-runs.

| Preset | Target | Why |
|---|---|---|
| Comfortably Numb | Strat (neck) | Gilmour's Black Strat |
| Purple Rain | Tele | Prince's Hohner Madcat |
| Tornado of Souls | off | shred humbuckers — jp70 native |
| Dream Theater / Petrucci Clean | off | jp70 literally is the JP guitar |
| Slipknot / Djent | off | needs the low 7th string, Match can't fake it |
| Radiohead | Tele | Greenwood/O'Brien |
| Oasis | Les Paul (or ES-335) | Noel's Epiphones/Gibsons |
| Nirvana | Jaguar/Mustang if offered, else Strat (bridge) | Cobain offsets |
| Foo Fighters | ES-335 / Les Paul | Grohl's Trini Lopez |
| Iron Maiden | Strat | Murray & Smith |
| My Clean / Glassy Clean | Strat (neck) | glassy single-coil sparkle |
| Acoustic | off | acoustic sim in chain already does the job |

## 7. Optional upgrades (later, not needed now)

- **UnO firmware chip (~$30)**: adds true stompbox mode — toggle LEDs track effect state, and switch behavior stops being preset-only. The single best FCB1010 upgrade.
- **FCB/UnO Control Center** (Mac app, ~$20): program the whole board from the desktop instead of the foot-dance above. Works with stock firmware too. Worth it for 20 presets.

## 8. Troubleshooting

| Symptom | Fix |
|---|---|
| MIDI Learn grabs wrong thing | That FCB preset still has extra slots enabled — disable everything but the one message |
| Wah sweep partial/dead | Recalibrate pedals (power-up holding 1+5) |
| Presets don't change | BIAS bank mismatch — PC only reaches the currently selected BIAS FX 2 bank; keep all 10 presets in one bank |
| Toggles work in one preset, not another | MIDI assigns save per preset — re-do step 5.3 in the broken preset and re-save |
| Nothing at all | Audio MIDI Setup: interface present? BIAS Settings → MIDI: device enabled? |
