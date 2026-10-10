# Experiments log

Everything we measured while building the TONE3000 rig, written up as
question → hypothesis → method → result → conclusion. Raw data sits next to this file.

**Test bench:** ThinkPad X1 Yoga Gen 8 (i7-1365U, 12 threads, performance power
profile, on AC), Omarchy/Arch, PipeWire 1.6 with pipewire-jack, Focusrite Scarlett
2i2 3rd Gen (guitar on Input 2), FCB1010 over a USB-MIDI cable. TONE3000 v0.0.9
standalone, JACK, 48 kHz.

**Metrics** come from `pw-top` sampled once per second on the TONE3000 node:
- **B/Q** — busy time ÷ quantum period, i.e. the share of each audio cycle spent processing.
- **ERR** — xruns: cycles where the node missed its deadline. Each one is an audible click or dropout.

Program changes and CCs were injected with `aseqsend` into TONE3000's ALSA
sequencer port, so every run is repeatable without touching the pedal.

---

## L1 — Buffer size and oversampling (v1 single-rig presets)

*Data:* `latency-xruns.csv`, phase A (runs 1–9).

- **Question:** how small a buffer and how much oversampling can this laptop run cleanly?
- **Hypothesis:** at ~11% DSP load there's headroom for a 64-sample buffer and 4×/8× oversampling.
- **Method:** heaviest v1 preset (Slipknot: 3 A2-Full NAM blocks). Sweep oversampling (off/2×/4×/8×) at 128 samples, and buffer size (64/128) at off/2×. 15–60 s per run.
- **Result:**

| Buffer | OS | B/Q max | Xruns/min |
|---|---|---|---|
| 128 | 2× | 0.30 | **0** |
| 128 | 4× | 0.20 | 6 |
| 128 | 8× | 0.30 | 12 |
| 64 | 2× | 0.19 | 24 |
| 64 | off | 0.30 | 4 |

- **Conclusion:** *hypothesis rejected.* Xruns don't follow average load: 4× oversampling ran at *lower* average B/Q than 2× and still dropped out. What matters is bursty per-cycle work and scheduling jitter, not the mean. v1 ran at 128 / 2×.

## L2 — Why the researched v2 rigs started crackling

*Data:* `latency-xruns.csv`, phase B (runs 10–30).

The v2 presets added dual stereo rigs, generated echo IRs of 3–5 s and reverb IRs of 2–5 s. The user heard clipping and cutting. We changed one variable at a time:

| Variable changed (128 samples) | Xruns/min (PC 0 / 12 / 10) | Verdict |
|---|---|---|
| v2 presets, 2×, multi-core, nice 5 | 8 / 6–14 / 6 | regression |
| launched at nice 0 (systemd) | 14 / 32 / 32 | not the cause |
| multi-core **off** | 104 / 284 / 352 | multi-core is essential |
| oversampling off | 16 / 16 / 18 | helps a little |
| long IRs removed (diagnostic build) | 6 / 6 / 8 | IRs add load, not the root cause |
| output 0 dB, gate −60 dB, echo tails trimmed | 40 / 40 / 42 | no DSP effect (these fixed *audible* clipping and cutting) |

- **Conclusion:** at 128 samples the graph now misses deadlines even with light chains. B/Q max stays ≤ 0.66, so this is scheduling headroom rather than CPU. Two contributing factors: the session doesn't have the `realtime` group yet (log out/in), and another app was streaming 44.1 kHz audio into the 48 kHz graph, which has to be resampled.

## L3 — Buffer sweep for the v2 rigs

*Data:* `latency-xruns.csv`, phase C (runs 31–39).

| Buffer | OS | Xruns/min (worst preset) |
|---|---|---|
| 192 (4.0 ms) | off | 26 |
| **256 (5.3 ms)** | **off** | **0** (60 s on the two heaviest dual rigs) |
| 256 | 2× | 2 |

- **Conclusion:** **256 samples, oversampling off**, multi-core on: zero xruns, with 11–29% peak DSP load. 192 is worse than 256 even though it's smaller than 256 and bigger than 128. Non-power-of-two quanta don't line up with the USB transfer size.

## L4 — Output level and gate (audible clipping and cutting)

- **Observation:** the user reported "clipping / cutting" after setting output to +24 dB and the gate to −35 dB in every preset.
- **Analysis:** TONE3000 normalizes each capture to roughly −18 dB loudness, so +24 dB puts peaks well above 0 dBFS and the interface hard-clips. A −35 dB gate threshold closes on note decays and quiet passages.
- **Fix:** gate −60 dB (hiss only); output reset, then re-balanced in L5.

## L5 — Balancing the guitar against Spotify

*Data:* `levels.csv`.

- **Goal:** guitar level with the music, or slightly above, for playing along.
- **Method:** capture TONE3000's output and the Spotify stream at the same time (separate `pw-record` nodes linked by port id) while the user plays. Compare active RMS (400 ms windows) and peaks.
- **Result:** at 0 dB the guitar sat ~15 dB under the music (−30.6 vs −16.0 dBFS RMS). Putting all 15 dB on the guitar would leave < 2 dB of headroom, so it's split: **guitar +14 dB** (all presets) and **Spotify stream −3 dB**.
- **After:** guitar −18.3 dBFS RMS, peaks −7 dBFS; the music's loud passages sit around −19 dBFS. The guitar is about 1 dB on top with 7 dB of headroom.

## L6 — Gain staging from the top (the hidden −24 dB)

- **Symptom:** after L5 everything still felt quiet, and the monitors had to sit at 50%.
- **Audit, top down:**
  - *Hardware:* Scarlett Monitor knob at 50%.
  - *Linux:* the PipeWire Scarlett sink was at **40%**. That's a cubic scale, so 0.064 linear, i.e. **−24 dB** of software attenuation on *everything*, TONE3000 included.
  - *Interface:* Input 2 Inst ✓, Direct Monitor off ✓, but **Air on** (an analog presence boost before NAM).
  - *Apps:* Spotify's stream was still at −3 dB from L5. The Spotify backend hardcodes librespot's player config, so there's no loudness normalization to turn on.
- **Fix:** turn the knob fully down first (+24 dB is coming), then sink → 100%, Air off. Apps stay at 100%. The hardware knob is the only volume control. `tone3000.py configure` now enforces the Linux side.
- **Measurement** (`levels.csv`, step 3): guitar −16.3 dBFS RMS / −5.4 peak vs a YouTube video (normalized ~−14 LUFS) at −18.9 RMS / −14.3 loudest. With unity staging, TONE3000's +14 dB output already puts the guitar **~2.5 dB over the music with 5 dB of headroom**. The L5 "fix" had been compensating for the sink.

## L7 — Leveling 15 presets (and two measurement traps)

*Data:* `preset-loudness.csv`.

- **Symptom:** "Slipknot way louder than Nirvana." Toggling pedals also jumped the volume.
- **Trap 1: replaying a DI.** I recorded a 15 s reference DI from Input 2 and injected it into `TONE3000:in_1` with `pw-play`, recording each preset's output. All 15 presets measured **−12.7 LUFS ±0.1**, and a 41 dB swing of the output level moved the reading only 12 dB. *Conclusion:* injecting into the JACK input doesn't exercise the chain the way the live device input does, so the method was abandoned. Measure live instead.
- **Method (live):** the user plays one steady riff while a script steps PC 0–14, recording 5–10 s of TONE3000's output per preset. Metric: BS.1770 K-weighted, gated integrated loudness (LUFS), per preset and per output channel, plus sample peaks.
- **Result, untrimmed:** a **39 dB spread**, from Oasis +0.9 LUFS to Acoustic −38.6 LUFS. 10/15 presets peaked above 0 dBFS (up to +10 dBFS, clipping the DAC). Some dual rigs had 15–18 dB between sides (Slipknot, Tornado). Some captures have no loudness metadata, so TONE3000 can't normalize them.
- **Fix:** a per-chain `out_db` trim on the **last NAM block** of each chain, since only linear IRs follow it. Target −14 LUFS (≈ normalized music), lowered where a preset's crest factor would push peaks over −1 dBFS: cleans go to −15.5 to −19. Dual rigs: each side at target −3 dB, so the hard-panned sides sum to target.
- **Trap 2: random channel order.** The first trims made four dual rigs *worse*. The recorder's FL/FR ports came up in random id order, so left and right were swapped in some takes. Fixed by wiring by name (`TONE3000:out_1 → input_FL`), then re-measuring and correcting.
- **Result, trimmed:** distorted presets land within ±1 dB of −14 LUFS with balanced sides and peaks ≤ −2.5 dBFS. Cleans sit a few dB lower by design.

## B1 — BIAS FX 2 under Wine: latency never felt right

- **Observation:** BIAS FX 2 (Windows build under Wine 11 staging) *reported* ~10 ms in its own UI, but playing through it felt noticeably laggy.
- **What we tried:** PipeWire quantum 1024 → 128 via `PIPEWIRE_QUANTUM`; a WineASIO build (Wine 11 has no `wine64`, so it had to be registered by hand) at a fixed 128-sample buffer; BIAS's "Low" latency mode; realtime-privileges.
- **Result:** still not tight enough to play comfortably. The app's number only covers its own buffer, not Wine's audio layers (WASAPI/ASIO shim → PulseAudio/JACK bridge → PipeWire) stacked on top.
- **Conclusion:** the native Linux TONE3000 plugin at 256 samples feels better than BIAS FX under Wine at a nominal 128. We moved the rig to TONE3000.

---

## I1 — IRs that aren't 48 kHz load as silence

*Data:* `ir-loading.csv` (E1).

- **Observation:** the Lexicon 480L plate (44.1 kHz) logged `IR prepared: 1 ch, 0.00 s`.
- **Test:** resample it to 48 kHz and reload. It then logged `IR prepared: 3.13 s`.
- **Conclusion:** TONE3000 v0.0.9 silently drops IRs that aren't 48 kHz. The builder now resamples every IR to 48 kHz.

## I2 — IRs with an odd-length data chunk load as silence

*Data:* `ir-loading.csv` (E2).

- **Observation:** even after resampling, 9 of 53 IR instances across 5 presets still logged `0.00 s`.
- **Hypothesis:** RIFF word alignment. 24-bit mono with an odd frame count gives an odd-length `data` chunk, and the plugin's "repair missing pad byte" path isn't enough.
- **Test:** tabulate data-chunk parity for all 53 instances. All 9 failures were odd, and all 44 successes were even. That includes a catalog IR used as-is (6 KB Mesa 4x12 IR, 2271-byte data chunk).
- **Fix:** always write an even frame count, and re-encode any catalog IR with an odd chunk.
- **Result:** 15/15 presets, 53/53 IRs load, 0 silent.

## M1 — How TONE3000 handles MIDI toggles (disassembly)

- **Question:** the stock FCB1010 sends CC value 127 on *every* press. Will TONE3000 toggle, or just latch on?
- **Method:** disassembled `MidiMapper::applyEvent` in the TONE3000 binary.
- **Result:** for toggle targets, any value ≥ 64 flips the state and values ≤ 63 are ignored, so repeated 127s toggle. Continuous targets map `value / 127` straight onto the parameter.
- **Verified live:** CC 23 flipped the Slipknot TS808 block off, and the release (0) was ignored.

## M2 — One CC, two targets

- **Question:** can one footswitch toggle the echo on both sides of a dual rig (`block5Power` + `rightBlock3Power`)?
- **Method:** mapped CC 24 to two targets (gate + spread), sent one CC 24, and read back the saved state.
- **Result:** both flipped, and the app kept both mappings when it saved.
- **Conclusion:** SW10 drives the left and right echoes together.

## M3 — Program Change → preset order

- **Method:** sent PC 0–14 and read `Loaded preset:` from the log.
- **Result:** 15/15 correct. PC N = the Nth entry of `Presets/order.json`.

---

# The DAW rig (Qtractor)

Levelling presets by hand (L7) kept fighting the same thing: nothing sat after the
amp to catch peaks and even out loudness. So the rig moved into a DAW, with a real
compressor and limiter after TONE3000, real wah/octaver pedals, and recording.
`qtractor_rig.py` generates the whole session. Nothing is clicked.

**Bench:** same laptop and interface. Qtractor 1.6.4 on pipewire-jack at quantum 256 / 48 kHz,
running the TONE3000 **CLAP** plugin. Test input is the dry DI loop `di-ref.wav`, played
into the rig's insert with `pw-play`; the Scarlett is unplugged from the insert for the run.
Program changes and CCs go into Qtractor's `FCB` ALSA port with `aseqsend`. Output is
recorded from bus `Rig` and measured as BS.1770 integrated loudness. Unlike the old
DI replay into the standalone (L7), this goes through the same plugin instance as the
live guitar. PC switching was verified by reading `activePresetName` back from the
saved plugin state.

## D0 — Picking a DAW that a script can drive

- **Requirement:** light, Linux-native, no Wine, free, and fully scriptable, so the user never clicks.
- **Maolan 0.3.0 (AppImage):** it has an OSC API that covers tracks, plugins and routing, and the chain built fine over OSC. It was rejected because:
  - its GUI panicked twice in `f32::clamp` (min > max) when the window was small;
  - on JACK, `track add` always fails ("Engine needs to open audio device"), because it only checks the non-JACK driver. That's still the case in engine HEAD;
  - it has no OSC command to save a session;
  - MIDI learn can't target plugin parameters.
- **Qtractor 1.6.4:** sessions are plain XML, so the generator writes the entire rig:
  - buses and exact JACK connections;
  - a MIDI track holding the plugin chain;
  - per-plugin CC bindings;
  - the TONE3000 state blob (a qCompress'd T3KB).

  Transport and recording run over MMC. Save/quit is `SIGUSR1` then `SIGTERM`.
- **Two constraints found in Qtractor's source:**
  - Plugins only receive MIDI on MIDI tracks and buses. So the guitar comes in through an Audio Insert on a MIDI track.
  - Only CLAP gets raw Program Change; the VST3 path drops it.

## D1 — Does a compressor + limiter tame preset loudness?

*Data:* `daw-dynamics.csv` (pedal_state = as loaded).

- **Hypothesis:** LSP Compressor (−18 dB threshold, 3:1, 10/120 ms, +3 dB makeup) plus the x42 true-peak limiter at −1 dBTP shrink the loudness spread across presets and remove overs.
- **Method:** full 15-preset DI sweep, once with both plugins deactivated and once with both active. Otherwise the session is identical.
- **Result:**

| | Dynamics off | Dynamics on |
|---|---|---|
| Spread, all 15 presets | 9.2 dB (−18.0 … −8.8 LUFS) | 4.6 dB (−15.5 … −10.9) |
| Spread, 10 distorted presets | 3.0 dB | 1.5 dB |
| Worst peak | **+3.0 dBFS** (Acoustic, Petrucci Clean) | −1.0 dBFS |

- **Conclusion:** yes. Two presets were clipping the output outright; now nothing passes −1 dBFS, and the distorted presets sit within a 1.5 dB window. The cleans stay lower on purpose (Purple Rain −15.5).

## D2 — Pedal on/off jumps

*Data:* `daw-dynamics.csv` (pedal_state = drive toggled; SW9 / CC 23 sent after each PC).

| | Dynamics off | Dynamics on |
|---|---|---|
| Mean jump when drive is toggled | 1.47 dB | 1.13 dB |
| Largest jump | 5.9 dB (Acoustic) | 3.7 dB (Nirvana DS-1 off) |
| Worst peak with drive toggled | +3.9 dBFS | −1.0 dBFS |

- **Conclusion:** the compressor shaves the jumps. What's left is mostly musical: Purple Rain's solo drive (+3.3 dB) and dropping Nirvana's DS-1 (−3.7 dB).

## D3 — Load and xruns with every pedal on

- **Method:** wah and octaver switched on over CC 20/21 on top of Nirvana (PC 8), 12 s of DI, `pw-top` sampled 30×.
- **Result:** Qtractor used B/Q 0.19–0.30, about 1.0–1.6 ms of the 5.3 ms cycle. ERR stayed at 1 throughout; that one xrun predates the test (probably startup), and none were added.
- **Octaver check:** with the octaver on, the 40–120 Hz band rose +2.9 dB relative to 120–1000 Hz (−10.0 → −7.1 dB) at the same LUFS, so the octave-down voice is there.

## D4 — Recording by script

- **Method:** `qtractor_rig.py record` (MMC record + play), 6 s of DI, then `qtractor_rig.py stop`.
- **Result:** `rig-Rig_Print-1.wav` (stereo, processed, peak −1.0 dBFS) and `rig-DI-1.wav` (mono dry DI, peak −1.4 dBFS), both 320,512 frames and sample-aligned.
- **Controls:** SW6 → wah on, SW7 → octaver on, EXP A 32/127 → wah position 0.252. All read back from the saved session.

## D5 — What a Program Change costs mid-song

- **Question:** can scenes within a song (rhythm → solo → clean) just be separate presets?
- **Method:** the DI loop plays continuously; `aseqsend` fires a PC every 2 s. Bus `Rig` is recorded and the 10 ms RMS envelope around each switch is inspected. `pc_gap.py`, 4 switches per pair.
- **Result:**

| Switch | Time > 20 dB below the running level | Floor |
|---|---|---|
| Iron Maiden (dual rig) ↔ My Clean | 130–160 ms | digital silence (−140 dB) |
| Iron Maiden ↔ Slipknot | 60–170 ms | digital silence |
| Re-sending the PC already loaded | ≤ 10 ms | −37 … −48 dB, the DI's own dynamics |

- **Conclusion:** a PC mutes the plugin while it rebuilds the chain, for longer on heavier rigs. That's fine between songs, but mid-song it swallows the first chord. Re-sending the current PC doesn't reload, so scene switches can carry the song's PC harmlessly.

## D6 — Instant scenes: two TONE3000s and absolute CCs

- **Design:**
  - Qtractor runs a *heavy* TONE3000 (MIDI ch 1) and a *clean* one (ch 2).
  - A **Selector** Audio Insert sits at the top of the heavy chain. CC 81 activates it (latch at 64).
    - Bypassed, it passes the guitar on to the heavy chain.
    - Active, it sends the guitar to the clean rig's insert and mutes the heavy one.

    That is one parameter, so the choice is exclusive by construction.
  - CC 80 does two jobs:
    - the heavy TONE3000 reads it through its own MIDI map as `inputLevel` = value/127;
    - Qtractor switches a lead delay on above 63 (later the Solo block, D8).
  - Every FCB scene switch sends absolute values: both PCs plus CC 80 and CC 81. So a scene always lands in the same state, whatever came before.
- **Constraint found:** Qtractor allows one observer per (type, channel, CC). And a PC reaches controllers as a trigger with value 127 (key = program number), so a PC can only switch a parameter one way. Hence the Selector trick instead of binding CC 81 to two parameters.
- **Checks** (stand-ins: heavy = Iron Maiden, clean = My Clean):
  - Selector send with CC 81 = 0: −180 dBFS, so the clean rig hears nothing. With 127: −21.6 dBFS, the DI level.
  - CC 80 = 72 set heavy `inputLevel` to 0.567 and turned the lead delay on; the clean instance was untouched. Both read back from the saved session.
  - Scene walk rhythm → solo → clean → rhythm → crunch → clean: sound throughout. The minimum 10 ms level within 300 ms of each switch was −17 … −29 dB, against −16 … −19 dB for the same DI with no switch. No PC-style hole anywhere.

## D7 — Cost of the second instance

- `pw-top` during the scene walk: Qtractor B/Q 0.43–0.58 with both instances loaded (Iron Maiden dual rig + My Clean), against 0.19–0.30 with one. Zero xruns at quantum 256.
- The clean rig's input goes through a JACK self-connection (Selector send → Clean In). That should add one period (5.3 ms) on the clean and acoustic scenes only; this is expected from how JACK handles feedback connections, not measured.

# The Maiden banks

Seven research agents, one per album era (1982, 1983, 1984, 1986, 1988, 1992, 2000s), each
wrote a heavy, a clean and an acoustic preset from sourced gear and TONE3000 captures (rigs
15–35, with sources in each file). Everything below was measured on the DI loop through the DAW rig,
using the exact messages the FCB sends. *Data:* `maiden-levels.csv`.

## D8 — Two traps in scene switching

- **A Program Change wipes CC 80.**
  - Method: send the loaded preset's PC and CC 80 = 72 in one burst, then read the heavy `inputLevel` back from the saved session.
  - Result: 0.496, the preset's value. CC 80 on its own gives 0.567, and it also works when sent 100 ms after the PC.
  - Cause: TONE3000 re-applies a preset's parameters asynchronously after a PC, even for the preset already loaded.
  - Fix: SOLO and CRUNCH send only CCs. RHYTHM, CLEAN and ACOUSTIC send PCs, but at CC 80 = 63, which is exactly the preset's own drive, so the race can't change anything. `verify()` in rig.py, run by `rig.py syx` and `send`, enforces this.
- **Pushing a cranked amp's input barely raises the level.**
  - With dynamics bypassed, SOLO's input push (0.496 → 0.567, about +3.4 dB if the parameter spans ±24 dB like the block gains) gave only +0.4 … +1.1 LUFS.
  - Raising the Guitarix delay's GAIN from 0 to 120 moved the level by ≤ 0.2 dB, because it only scales the echoes.
  - Fix: the **Solo** block, an LSP Slap-back Delay with dry amount +2 dB and one 380 ms tap (feedback 0.3, 250 Hz – 4.5 kHz, −10 dB). It is switched on above CC 80 = 63 and is unity when bypassed.
  - Result: solo now sits +2.9 … +3.1 dB above rhythm before dynamics, and +1.4 … +2.1 dB after them.

## D9 — Leveling 21 presets and balancing the two guitarists

- **As delivered** (dynamics bypassed, where trims are linear):
  - Heavy rhythm ranged −9.1 … −13.7 LUFS.
  - Acoustic ranged from −15.5 up to **+10.9 LUFS with +25.9 dBFS peaks**.
  - Measured per side from the second pass onwards, the Murray | Smith sides differed by up to **13.1 dB** (83 Heavy: −9.3 / −22.4) and 11.3 dB (86 Heavy). With dynamics on, the limiter had hidden all of this.
- **Gain-staging audit:**
  - Two presets drove the Boss AC-3 capture far hotter than the validated Acoustic preset (PC 13) does: 92 Acoustic by +20 dB from a compressor, 86 Acoustic by +9 dB on the input. Both were set back to unity.
  - The trim script now skips partial-mix blocks, where a gain change only scales the wet part.
- **Trims:**
  - Each chain gets its trim on the last level-setting block before the echo and ambience, so the reverbs follow.
  - Each chain is shifted to the average of the two sides, balancing them.
  - Two linear passes toward heavy −14 and clean/acoustic −15.5 LUFS, dynamics bypassed.
- **Result:**

| | Before | After (no dynamics) | Final (with dynamics) |
|---|---|---|---|
| Heavy rhythm | −9.1 … −13.7 | −13.9 … −14.1 | −11.5 … −12.5 |
| Clean | −12.6 … −15.9 | −15.3 … −15.6 | −12.8 … −13.7 |
| Acoustic | −15.5 … +10.9 | −15.4 … −15.5 | −13.2 … −14.0 |
| L/R side mismatch, heavy | up to 13.1 dB | ≤ 0.1 dB | ≤ 0.1 dB |
| Solo over rhythm | +0.4 … +1.1 dB | +2.9 … +3.1 dB | +1.4 … +2.1 dB |
| Crunch under rhythm | | | 0.1 … 1.1 dB |

## D10 — Xruns: scenes vs. song changes

- **Method:** one continuous 3-minute DI stream, so no audio nodes are added or wired during the run. Count Qtractor's ERR from `pw-top`.
  - Phase A: 30 scene switches in 60 s, with PCs exactly as the FCB sends them.
  - Phase B: 10 bank changes in 60 s, each loading two presets.
- **Result:** A, 0 xruns. B, 1 xrun, so about 1 in 20 preset loads glitches, and only at a song change. The DSP median was 38 % of the cycle, with a peak of 87 %, reached during preset loads.
- **Correction (D11):** this run's loop was a headerless `.raw` file. `pw-play` can't read those, so the rig was processing silence. Re-measured with real audio, the two-instance rig at steady state gave B/Q 0.32 median and 0 xruns in 30 s. D11 has the scene and bank numbers for the final rig.
- **Side note:** an earlier sweep counted 9 xruns because its recorder and player joined the audio graph for every reading. Graph churn causes xruns too, so measure with persistent nodes.

## D11 — Twin-guitar harmony, and what it cost

The Maiden signature is two guitars a third apart. HARMONY (SW7 in the scene banks) adds the
second guitarist live: a diatonic third above whatever you play, in the song's key, through
his own amp.

- **Objective test:** a synthetic plucked melody runs up the scale an octave (E4 … E5 for E minor). The harmony voice is recorded on its own and pitch-tracked with YIN, 10 ms hops.
- **v1: Rubber Band +3.5 semitones, then x42 autotune snapping to the scale, all after the amp.** Right on 63.4 % of frames.
  - Isolated on a clean tone:
    - the autotune alone pulled every out-of-key note down onto G major;
    - Rubber Band alone shifted exactly +3.50;
    - together they gave 8/8 thirds, each within 0.02 semitones.
  - So the failure was pitch tracking on a distorted signal.
- **v2: harmonise the clean DI, then a third TONE3000** on its own track, loaded with the same era's rig. 8/8 notes, 100 % of frames, through the distorted Maiden 83 rig.
  - Cost, steady state with real audio: B/Q 0.53 median against 0.32 for two instances, and **80–85 xruns in 30 s**. Removing parts one at a time showed no single culprit; it was the sum:

| Parts running | Median | Max | Xruns / 30 s |
|---|---|---|---|
| heavy + clean (two instances) | 0.32 | 0.44 | 0 |
| + the input loop only | 0.33 | 0.50 | 0 |
| + Rubber Band and autotune only | 0.38 | 0.71 | 1 |
| + the third TONE3000 only | 0.45 | 0.68 | 0 |
| all of it | 0.52 | 0.70 | 85 |

- **v3: three autotune stages instead of Rubber Band.**
  - The autotune resolves a tie between two scale notes downward. So "shift +2 semitones, then snap" is always one scale step: a whole step that leaves the scale is pulled back to the half step.
  - Two steps plus a final snap make a diatonic third. The harmony TONE3000 loads a generated one-chain preset: the partner guitarist's amp and cab, without reverb IRs.
  - Result: **8/8 notes, 100 % of frames**. Steady state B/Q 0.48 median, 0.60 max, **0 xruns** in the first 30 s run.
- **Reverb tails:** with three instances, bank changes gave 71 xruns over 7 changes.
  - Without the long echo and reverb IRs: 0, and B/Q 0.40.
  - Capping every reverb tail at 2.0 s (`IR_TAIL_MAX_S`): **0 xruns over 7 bank changes**, 3 over 20 scene switches.
- **The real cause of the remaining randomness is scheduling.** Repeat steady runs gave 31, 4 and 12 xruns at a median of only 0.43–0.55, so the load itself isn't the limit.
  - No audio thread runs real-time: Qtractor's and PipeWire's data loops are `SCHED_OTHER`, `ulimit -r` = 0, and PipeWire logs "RTKit error: ServiceUnknown".
  - The user is in the `realtime` group (`realtime-privileges` is installed), but the group was added after this login, so no running process has it.
  - Fix: log out and back in, or install `rtkit`. Then re-measure.
- **Level:** the harmony voice is trimmed per era to 3 dB under the main pair, i.e. the same as one guitarist. Before trimming it measured −1.3 … −2.7 dB.

## D12 — Per-song settings from a helper

The FCB can send two CCs per switch, and both are taken. So `qtractor_rig.py helper`, a user
service started by `up`, watches the FCB. When a scene bank's heavy Program Change arrives, it
sends two values to Qtractor:
- the song's solo echo time, on CC 85, to the Solo block's `Delay 1 time`;
- its harmony scale, on CC 86 to 88, to the three autotune stages.

- **Settings** (the `song` block in rigs 15/18/…/33). The key and tempo of each era's signature harmony song were researched from Musicnotes official transcriptions and from note data extracted from Songsterr:

| Bank | Song | Scale | Solo echo |
|---|---|---|---|
| 03 | Hallowed Be Thy Name | E natural minor | 571 ms (105 BPM) |
| 04 | The Trooper | E natural minor | 375 ms (160) |
| 05 | Rime of the Ancient Mariner | E natural minor | 536 ms (112) |
| 06 | Wasted Years | E natural minor | 390 ms (154) |
| 07 | The Evil That Men Do | E natural minor | 375 ms (160) |
| 08 | Fear of the Dark | D Dorian | 750 ms (80, the intro melody) |
| 09 | Blood Brothers | E natural minor | 339 ms (177) |

- **Test:** a replayed FCB Program Change 30 set the Solo delay to 748 ms (one CC step is 7.9 ms) and all three autotune stages to scale 1 (C major, the note set of D Dorian). Both were read back from the saved session.

## D13 — A tuner on SW10, and muting in Qtractor

- **Tuner:** Chromatic (GTK4, Flathub `io.github.nate_xyz.Chromatic`).
  - On a CC 28 press the helper runs it with `PIPEWIRE_NODE` set to the guitar source. Its ALSA-on-PipeWire capture then hears only Input 2; by default it took Input 1. On the next press `flatpak kill` closes it; the sandboxed app outlives `flatpak run`.
  - A Hyprland rule makes it float, centred, 1100×760 and opaque. At the default tile (466×508) the gauge was clipped, and Omarchy's default opacity let the desktop show through.
  - Replay test, two presses: after the first, Tuner Mute = 1 and one window is open; after the second, Tuner Mute = 0 and no windows are left.
  - How it got there:
    - A hand-rolled Python tuner was dropped in favour of existing software.
    - x42 Tuna via `jalv` needed XWayland: x42 UIs embed as X11 and jalv's GtkPlug crashes under Wayland. Rejected: "ugly".
    - FMIT (Qt) had fixed layouts that tiling crushed; its note readout was cut off. Rejected.
    - TONE3000's own tuner is a click-only UI button (`setTunerEnabled` from its web UI, with no MIDI target), so no footswitch can reach it.
- **Muting while tuning:** four ways, tested with the DI loop, measuring bus `Rig`:

| Mute | Engaged | Released |
|---|---|---|
| Insert on bus `Rig`, toggle mode | −240 dBFS | **stays silent** |
| Insert at the head of the heavy track, toggle mode | −66.7 dBFS | **stays silent**, and the Guitar insert's input links are gone |
| `wpctl set-mute` on Qtractor's node | "does not support mute" (JACK node) | — |
| Insert at the head of the heavy track, latch mode (like the Selector), CC 29 = 127/0 from the helper | −42.5 dBFS within 0.5 s, reverb tails fading | −16.4 dBFS, input links intact |

- **Conclusion:** the helper turns each SW10 press into an absolute CC 29 value, and the mute copies the Selector exactly. It sits before the Selector and the harmony tap, so it silences all three rigs. Unplugging the guitar from the rig doesn't work either: Qtractor re-patches its saved insert connections within seconds.
- **Real-time audio:** `rtkit` gave PipeWire RR priority 20, but PipeWire's rtkit path also caps a real-time process at 200 ms of CPU. Qtractor, even with an empty session, was killed at 0.47 s every time.
  - Fix: `~/.config/pipewire/jack.conf` sets `rtkit.enabled` / `rtportal.enabled = false`. JACK clients then take real-time only from the `realtime` group's rlimits, with no cap, once that group is active in the login session.
  - TONE3000's multiCore stays on: steady xruns in 30 s were 56 with it off, 9 and 7 with it on.

## D14 — The tuner that listened to the mic, and the dropouts it caused

- **Symptom:** during a practice session the sound kept cutting up, and the tuner read the user's voice.
- **Measured:** Qtractor ERR went up about 250 in 12 s (6620 → 6870). Two Chromatic instances were running at about 96 % CPU combined, and both were recording from **Scarlett Input 1** (the mic), not Input 2 (the guitar).
- **Cause:**
  - Chromatic records through ALSA-on-PipeWire and takes the default source. `flatpak run --env=PIPEWIRE_NODE=…` didn't stick when the helper launched it.
  - A second instance had been left open by an earlier toggle.
- **Fix:**
  - The helper kills any running instance before opening one.
  - Once Chromatic's `ALSA plug-in [chromatic]:input_MONO` port appears, the helper replaces whatever is connected to it with the guitar source.
- **Result:** one window, fed only from Input 2, and closed cleanly by the second press. With the tuner closed, ERR went up by 1 in about 15 s at B/Q 0.43–0.58.
- **Still open:** Qtractor's audio thread runs `SCHED_OTHER` until the `realtime` group is active in the login session (see D13), which needs a re-login.

## D15 — TONE3000 v0.0.9 → v0.0.12: acoustic glitches and compatibility

- **Symptom:** acoustic presets hiccup, worse with delay on.
- **Cause:** Qtractor promises the CLAP a 2048-frame maximum block (`prepareToPlay … samplesPerBlock=2048`
  in TONE3000.log) while running 256-frame cycles. v0.0.9 prepared every IR convolver at that
  maximum, so each 256-frame callback ran a 2048-partition FFT per IR (upstream issue #146).
  v0.0.12 caps the convolver block at 256 (PR #223, `kIrConvolverMaxBlockSize`, ChainBlock.h).
- **Method:** DAW rig, dynamics bypassed, clean instance (PC ch 2, CC 81 = 127), continuous DI loop,
  Qtractor ERR from `pw-top -b` once a second; delay = CC 24 on ch 2 (slot 5 + R3).

| Preset | v0.0.9 delay on | v0.0.9 delay off | v0.0.12 delay on | v0.0.12 delay off |
|---|---|---|---|---|
| 13 Acoustic | 360/min | 64/min | 0 | 0 |
| 17 Maiden 82 Acoustic | 1401/min | 162/min | — | — |
| 20 Maiden 83 Acoustic | 0 | 0 | — | — |
| 23 Maiden 84 Acoustic | 62/min | 875/min | 5/min (1 in 12 s) | 0 |
| 26 Maiden 86 Acoustic | 4/min | 0 | — | — |
| 29 Maiden 88 Acoustic | 4/min | 80/min | — | — |
| 32 Maiden 92 Acoustic | 67/min | 381/min | — | — |
| 35 Maiden 2000s Acoustic | 2/min | 2611/min | 400/min right after the PC, **0** re-measured | 0 |

v0.0.9 runs were 30 s (B/Q up to 1.24), v0.0.12 runs 15 s. On v0.0.12 the only xruns left came in
the first seconds after a Program Change, while the new chain's convolvers were still loading; the
same preset settled read 0 with delay on and off. Generated echo IR lengths on these presets:
1.77–2.95 s (echo + reverb tail, `ir_lengths`), ambience 1.0–2.0 s.
- **Compatibility (v0.0.12):** all 43 presets loaded in PC order on the heavy instance (log
  `Loaded preset:` = expected name, 43/43, v0.0.9 sweep); the new builder's presets are identical
  to v0.0.9's in params and chains (only `id`, the `T3KH` header and the 0.0.11 gate/pitch params
  are added). Not yet re-run on v0.0.12: the full 43-preset level sweep and the CC 22/23/24
  audio toggle test.

## D16 — Palm mutes "too muffled": gate or compressor?

- **Symptom (owner):** "palm muting sounds too muffled like almost nothing gets through."
- **Gate:** cleared. An offline port of the v0.0.12 gate at the rig's settings (−60 dB threshold,
  hold 50 ms, release 100 ms, range 80 dB) passes 80 ms plucked bursts at −30, −40 and −50 dBFS peak
  with 0.0 dB change; only a −60 dBFS burst loses 4.9 dB of its pick. Simulation, not a rig run.
- **Compressor:** the bus compressor (−18 dB, 3:1, 10 ms attack, +3 dB makeup) sits on every heavy
  preset at the rig's +14 dB output, so it clamps each chug's pick transient. During the D15 runs
  (dynamics bypassed) the owner said the rig "sounds way better".
- **Change:** the compressor is bypassed by default; the −1 dBTP limiter stays. Not yet measured:
  a palm-mute level/transient A/B with the compressor on vs off on the rig.

## D17 — Bank 3 (Modern) levels, and does TONE3000 play A1 `.nam` files?

- **Question:** where do the five new Randall Satan presets (PC 13–17) sit against the −14 LUFS
  target, and can a local A1 `.nam` (the format of Ola Englund's 2023 free models) replace a
  catalog A2 model?
- **Hypothesis:** the full-rig capture runs hot like Djent's (−9.8 dB trim). TONE3000 is built on
  NAM A2, so A1 might not load.
- **Method:** bench, owner not playing. The DI loop (`pw-play`, persistent node) went into
  `Qtractor:Guitar/in_*` and each PC went into the FCB port with `aseqsend` (CC 80 = 63,
  CC 81 = 0 first). After 3 s to load, 5.2 s of `Qtractor:Rig/out_*` was recorded on a persistent
  `pw-record` node. Speakers and the live guitar input were unlinked during the run and relinked
  after. Measured with BS.1770 integrated loudness and sample peak (ffmpeg ebur128), per side
  too. Compressor bypassed (default since D16), limiter on. The A1 test was a temporary PC 18:
  PC 14 with its amp replaced by `"file"` = Jacovino's A1 Satan 50 Modern (model 81534).
- **Result** (`bank3-levels.csv`, four passes):

| PC | Preset | untrimmed | `out_db` | final LUFS | peak dBFS |
|---|---|---|---|---|---|
| 13 | Satan Full Rig | −6.0 | −12.5 | −14.1 | −3.3 |
| 14 | Satan 50 Modern | −15.0 | +1.0 | −14.1 | −4.4 |
| 15 | Satan 50 Low Tuned | −13.8 | −0.2 | −14.0 | −8.1 |
| 16 | Satan 50 Lead | −14.0 | 0 | −14.1 | −1.0 (limiter) |
| 17 | Satan Wall (L/R) | −13.4 (−15.3 / −17.9) | −1.7 / +0.9 | −13.5 (−16.3 / −16.7) | −1.0 (limiter) |

  - The full rig's trim isn't linear: −8 dB of `out_db` bought only 3.8 dB, because the limiter
    was holding its untrimmed −1 dBFS peaks. The second step was linear.
  - **A1:** TONE3000 0.0.12 loaded the A1 file (log: `Preparing NAM model … (407762 bytes)`,
    `NAM model reports -1 Hz; the chain runs at 48000 Hz regardless`, `Loaded preset: A1 Test`)
    and played it: −20.7 LUFS untrimmed, 5.7 dB under its A2 twin. Peak-to-loudness was 8.0 dB
    against the A2's 9.6 dB, so it was distorting rather than passing the DI through.
  - All five presets logged `Loaded preset:` with their own names and produced sound. Qtractor
    ran with B/Q 0.44 and 0 ERR in a closing `pw-top` sample.
- **Conclusion:** bank 3 sits at −14 ± 0.5 LUFS. A1 `.nam` files play, so a block's local
  `file` can take Ola's own free models once they're downloaded. Re-level with `out_db` after a
  swap, because an A1 file may carry no loudness metadata.
- **Not measured:** a live riff from the owner (L7: the DI bench can read differently), the CC 23
  and CC 26 toggles on these presets, and xruns over a long run. "Sounds best" among the
  @nillmtd models was not judged by ear: SW1 uses the author's "My EQ" model.

## D18 — Distinct cabs for bank 3, and Glenn Fricker's Stormblade A2 on SW5

- **Question:** (1) do local 96 kHz IRs load through a block's `"file"`? (2) Which of three free
  Lancaster Audio PLAP cab IRs (Cameron Webb Ubershall SM57, Warren Huart Marshall 4x12 V25,
  Ulrich Wild Albion 4x12 Audix D4) gives each Satan 50 preset its own voice, measured against the
  Res New Old Dude IR they all shared? (3) Does TONE3000 0.0.12 play the free Stormblade A2
  (`SlimmableContainer`, trainer 0.7.0) as a dual rig with its two UK V30 IRs?
- **Hypothesis:** Ubershall tightens Low Tuned, Marsh V25 smooths Lead's upper mids, and Albion
  suits Modern. Stormblade loads like any A2 file.
- **Method:** offline, each IR went through `build_block` and its embedded WAV was read back.
  Then the D17 bench, with one change: one persistent `pw-record` on `Qtractor:Rig/out_*` and one
  persistent `pw-play` of the DI repeated every 15 s, with each PC sent (CC 80 = 63, CC 81 = 0
  first) at a loop boundary and 4.0–14.5 s of each loop measured. Every preset hears the same
  10.5 s of DI. Measures: BS.1770 loudness and sample peak (ffmpeg ebur128), per side too; the
  energy share per band relative to 40 Hz–10 kHz; the spectral centroid; and a low-end tightness
  figure: the p90 − p10 spread of the 20 ms RMS envelope of the 60–150 Hz band (bigger = the low
  end stops between hits). Twelve temporary presets (PC 18–29: Modern, Low Tuned and Lead amps ×
  four cabs, otherwise identical) and a temporary single-chain Stormblade (PC 18) were removed
  after. Speakers and the live guitar input were unlinked during the runs. Compressor bypassed,
  limiter on.
- **Result** (`bank3-cabs.csv`, `bank3-levels.csv` passes 5–6):
  - **Local IR path:** no fix needed. `build_block` sends local files through the same
    `wav_loads_in_tone3000` gate as catalog files, so the 96 kHz files (odd 72003-byte data
    chunks) became 48 kHz mono 24-bit with even data chunks (36000 bytes). Band shares matched the
    96 kHz originals within 0.1 dB, and the sources hold only −42 dB above 24 kHz, so the linear
    resampler aliases nothing audible. TONE3000 logged `Preparing IR model: …` for each, and the
    presets measured −14 LUFS with cab-shaped spectra.
  - **A2 detection bug:** `local_tone` looked for `"SlimmableContainer"` in the file's first
    200 bytes. Trainer 0.7.0 writes `metadata` first (the key sits at byte 727 in Stormblade), so
    an A2 file was tagged A1. It now parses the JSON's `architecture`.
  - **Cabs** (band shares in dB; same amp, same DI; New Old Dude → chosen):

| Preset | Cab | 40–100 | 100–250 | 250–800 | 5–10 k | centroid | low spread |
|---|---|---|---|---|---|---|---|
| Modern | New Old Dude → **Marsh V25** | −13.4 → −18.3 | −6.6 → −9.2 | −6.8 → −5.3 | −15.2 → −12.4 | 1473 → 1755 Hz | 19.2 → 18.5 |
| Low Tuned | New Old Dude → **Ubershall** | −17.3 → −22.0 | −9.0 → −10.2 | −9.8 → −9.2 | −13.0 → −12.7 | 2456 → 2423 Hz | 18.9 → 19.3 |
| Lead | New Old Dude → **Albion** | −16.5 → −29.3 | −6.5 → −6.2 | −8.1 → −6.1 | −15.6 → −18.8 | 1828 → 1685 Hz | 19.7 → 20.9 |

  - The Marsh V25 was the *brightest* IR on every amp (+2.7 to +3.0 dB at 5–10 kHz against New Old
    Dude), so the "smoother upper mid" hypothesis for Lead is refuted. The Albion was the
    smoothest top (−3.1 to −3.2 dB at 5–10 kHz), the most midrange (+2 dB at 250–800 Hz) and the
    leanest bottom (−11.9 to −12.9 dB under 100 Hz). Its lean bottom suits a lead, not drop-tuned
    rhythm, where it also lowered the tightness figure (18.7). The Ubershall cut 4.7 dB of boom
    under 100 Hz while leaving the top within 0.3 dB, the closest to New Old Dude and the tightest
    on Low Tuned. The Albion was also the darkest on Modern (centroid 1303 Hz), so Modern took the
    Marsh: it cuts 4.9 dB under 100 Hz and 2.6 dB at 100–250 Hz, for a brighter, more aggressive
    rhythm, at a 0.7 dB lower tightness figure.
  - **Stormblade:** TONE3000 logged `Preparing NAM model: Stormblade A2 … (310620 bytes)` and
    `Loaded preset: Stormblade`. Against the dry DI it was distorted and cab-filtered:
    peak-to-loudness 10.5 dB against the DI's 18.1, and 2.5–5 kHz at −7.8 dB against −25.3. Dual
    against single SM57 chain: equal tightness (22.0 vs 22.2), more body (40–100 Hz −19.6 vs
    −23.2), and stereo width. Untrimmed it played −16.6 LUFS, with the SM57 side 3.4 dB under the
    MD 440 side (−21.6 / −18.2).
  - **Levels, final pass:**

| PC | Preset | `out_db` | LUFS | peak dBFS | L / R LUFS |
|---|---|---|---|---|---|
| 13 | Satan Full Rig (unchanged) | −12.5 | −14.3 | −1.0 | −17.3 / −17.3 |
| 14 | Satan 50 Modern (Marsh V25) | +2.4 | −14.2 | −5.2 | −17.2 / −17.2 |
| 15 | Satan 50 Low Tuned (Ubershall) | −0.9 | −14.0 | −3.8 | −17.0 / −17.0 |
| 16 | Satan 50 Lead (Albion) | 0 | −13.9 | −5.0 | −17.0 / −16.9 |
| 17 | Stormblade (57 L / 440 R) | +4.6 / +1.2 | −14.0 | −5.4 | −16.9 / −17.0 |

  - All five logged `Loaded preset:` with their own names. Closing `pw-top`: Qtractor B/Q
    0.43–0.45, and ERR held at 169 across the idle samples. That count accumulated over three
    launches' worth of PC loads and bench graph changes, not while playing.
- **Conclusion:** bank 3 now has five cab voices: the Full Rig's baked-in New Old Dude, Marsh V25
  (Modern), Ubershall (Low Tuned), Albion (Lead), and the Stormblade's own UK V30 pair. All sit at
  −14 ± 0.3 LUFS. Local 96 kHz IRs need no special handling. A2 files from newer trainers load
  once the architecture is read from the JSON.
- **Not measured / caveats:** nothing was judged by ear. The 10.5 s window read the unchanged
  New Old Dude Modern 1 dB quieter than D17's 5.2 s window (−15.1 vs −14.1), so these levels are
  internally consistent but not directly comparable with D17. Sample peaks moved by up to 4.6 dB
  between passes of the same preset (PC 15: −8.4 / −3.8). Lancaster's description of the Albion
  cab wasn't found online, so its identity comes from the file name only. The CC 23 and CC 26
  toggles on Stormblade (Fortin and CHUG into an unknown amp) were not measured.

## D19 — An offline simulator: the real TONE3000 engine, faster than real time

- **Question:** can presets be measured without the live rig, fast enough for a tune loop
  ("make the cleans fatter" in about 2 s, edit plus verify), and does it agree with the DI bench?
- **Hypothesis:** the native TONE3000 VST3, hosted offline by Spotify's pedalboard and handed the
  same state blob `qtractor_rig.py` gives the live instance, renders the same audio as the rig.
- **Method:** `rigsim.py` builds each preset in memory (`tone3000.build_preset`, so tone.csv
  applies), loads it into a fresh TONE3000 per process (sandbox HOME; ~/.config/TONE3000 only
  read), waits until the plugin's log shows every queued model prepared and its load mute lifts,
  renders the bench DI (`di-ref.wav`) on both inputs at a 256-sample block, applies a -1 dBFS
  lookahead limiter, and measures BS.1770 loudness and bands. `rigsim_validate.py` rebuilt every
  bank-3 sound the bench measured (D17 passes 1-4, D18 passes 5-6, the 12 cab candidates and both
  Stormblade variants) from git at 71e630c / fb43aed and compared (`sim-validation.csv`).
  Owner's Qtractor rig was running throughout; renders ran at nice 19, no audio device.
- **Result:**

| Set | n | Δ LUFS sim − live: mean | sd | range |
|---|---|---|---|---|
| D18 levels (passes 5-6), same 4.0-14.5 s window | 10 | +0.01 | 0.08 | -0.1 … +0.2 |
| D18 cab candidates + Stormblade, same window | 14 | +0.02 | 0.05 | -0.0 … +0.2 |
| D17 levels (passes 1-4), 5.2 s window at an unrecorded loop phase | 20 | -0.16 | 0.60 | -1.1 … +0.8 |

  - D18 rows match to the tenth in loudness. Sample peak matches in 17 of 24; live peaks of the
    same preset moved by up to 4.6 dB between passes (D18). Band shares match within 0.1 dB and
    the centroid within 13 Hz for PC 18-24 and Stormblade single.
  - PC 25-29 (Low Tuned/Albion and the four Lead cabs) match in loudness but read 135-270 Hz
    darker than the bench. Re-measuring the simulated audio with the window shifted reproduces
    the bench at +0.7 s (PC 25) and +1.0 to +1.2 s (PC 26-29), loudness within 0.1 dB. So the
    bench's window drifted about 1 s late over the second half of that 12-preset run. A
    distorted amp's loudness barely depends on which bar of the riff is measured, its spectrum
    does. The cab conclusions compare cabs on the same amp, which were measured in the same
    run, so they stand. The absolute Lead centroids (1828 → 1685 Hz) are about 250 Hz high.
  - D17's scatter is per preset and constant across passes (Modern -1.0 to -1.1 every pass, Lead
    -0.5, Low Tuned +0.2): the unknown window, as D18 already noted for Modern. The sim tracks the
    trims: Modern +1 dB of out_db = +1.0 dB live and simulated. The only within-preset miss is
    the untrimmed Full Rig (pass 1, limiter holding peaks): +0.8 dB, as the stand-in limiter is
    gentler than x42's true-peak one.
  - **Speed** (i7-1365U, rig live, nice 19): whole rig (19 presets) on the full 15 s DI in
    11.1-11.6 s wall (16-26 s under other load); on the 2.5 s clip, 3.9 s. Five presets on the
    clip, nothing cached: 1.3-1.5 s end to end with `uv run`; an `@clean` A/B (3 presets,
    before and after, 6 renders): 1.6 s cold, 0.3 s cached. Per preset: plugin load 0.09 s,
    state 0.32 s, models land 0.05-0.15 s, render 0.25-0.6 s.
  - **Clip vs exact:** the 2.5 s clip reads +0.2 ± 0.6 dB LUFS and bands within about ±1 dB of
    the 10.5 s window across all 19 presets, a per-preset offset. Deltas track: an `@clean`
    eq_100 +3 / eq_250 +2 what-if moved LUFS +1.2/+1.0/+0.3 (clip) vs +1.0/+1.0/+0.1 (exact) and
    2-5 kHz -1.4/-1.1/-0.4 vs -1.1/-1.1/-0.2.
- **Pitfalls found:** (1) the plugin lifts its load mute after 2 s with no model landing, so on a
  busy machine output can start with blocks still dry; output alone isn't proof that a preset
  loaded, and the sim reads each process's TONE3000.log. (2) Stereo rigs with spread/align
  wobble give a different mono-sum spectrum on every run (±0.6 dB, ±40 Hz), so bands are
  measured on L+R power.
- **Conclusion:** the offline engine is the rig, to 0.1 dB. Tune with `rigsim.py` and confirm
  on the bench only what the sim can't do.
- **Not simulated:** wah, octaver, Solo slap-delay and harmony tracks, the bypassed compressor,
  true-peak limiting, Program Change gaps, and CC moves after load. The older banks (0-2) were
  levelled live with a riff and the compressor on, so their sim levels (e.g. Hysteria -6.6 LUFS)
  are not comparable with those notes yet. That needs a bench pass.

## D20 — Bank 2 (80s Clean) levels, and a real chorus in the DAW

*Data:* `80s-clean-levels.csv` (rigsim `--exact`, after the trims).

- **Question:** what `out_db` puts the five new cleans (PC 18-22) at the rig's clean level, and
  can the rig get a real chorus, which TONE3000 can't do?
- **Hypothesis:** every capture carries loudness metadata (-10.9 to -24.8), so TONE3000
  normalizes them and the trims should be small. The DI captures into a cab IR would sit lower.
- **Method:** `uv run rigsim.py @80s-clean --exact --before none` (D19's DI bench, offline),
  untrimmed, then with tone.csv `out_db` trims, aiming at -16 LUFS: the cleans sit 2 dB under the
  -14 heavies, so the crest (19 dB) keeps pick peaks near the -1 dBFS limiter.
- **Result, untrimmed:** a 19 dB spread. Is This Love -23.2, Rule the World -25.8, This Charming
  Man -27.0 LUFS (all DI + cab IR), Every Breath -14.8 (JC-120 amp+cab capture), Hysteria Clean
  -7.9 (the Rockman's cab-simulated output, limited at -1 dBFS). Normalization doesn't cover the
  cab IR's loss.
- **Trims:** +7.2, +9.8, -1, -11.3, +11 dB. Hysteria needed -11.3, not -8, because the limiter
  had been hiding 3 dB.
- **Result, trimmed:** -15.7 to -16.2 LUFS, peaks -1.0 to -1.9 dBFS.
- **Chorus:** LSP Chorus Stereo (installed; Guitarix's `gx_chorus_stereo` is the alternative),
  after the heavy TONE3000, bypassed, toggled by CC 31 (SW7 in the 80s banks), with each preset's
  rate/depth/mix as CC 89-91 (`qtractor_rig.chorus_ccs`).
- **Not measured:** the chorus. Qtractor was running the owner's session, so there was no live
  `build` or smoke test, and rigsim has no DAW plugins. The CC-to-port mapping is assumed linear
  (`logarithmic=0` in the session, as for the Solo time). The rate port is marked logarithmic in
  its TTL, so check it on the first live run. Until whatever reads the FCB sends `chorus_ccs`,
  the session default plays: 0.6 Hz, 4 ms, 50 %.

## D21 — A universal flash and a software router: what the extra hop costs

- **Question:** if the FCB1010 only sends fixed addresses (channel 16) and a Python ALSA client
  (`fcb_router.py`) maps them to the layout, how much latency does the hop add?
- **Hypothesis:** well under 1 ms; Python dispatch is tens of µs, kernel sequencer hops are cheap.
- **Method:** `uv run fcb_router.py bench 2000` on test ports (no FCB, no Qtractor). A bench
  client sends each mapped address (50 switches, round robin) from its own port and stamps it,
  and receives the router's output on another of its ports (same process, same monotonic clock).
  Baseline: the same send with a direct link (bench out → bench in), no router. Laptop busy
  (Steam, desktop); router unprivileged, no RT priority. Raw data: router-latency.csv.
- **Result (µs):** direct link median 66, p99 254, max 554. Via the router, first message:
  median 114, p99 385, max 575; whole burst (up to 8 messages): median 153, p99 433, max 661.
  Added by the router: about 0.05 ms median, under 0.2 ms at p99. Earlier runs on the same
  machine gave a 0.05–0.09 ms median and a rare max of 0.8–1.3 ms (scheduler wake-up).
- **Conclusion:** the router is free in practice. It also shortens the wire: the FCB now sends
  one 2–3 byte message per press instead of a burst of up to 12 bytes over 31.25 kbaud DIN
  (320 µs per byte), so a scene switch's last message reaches the rig about 3 ms sooner
  (computed, not measured on the pedal).
- **Not measured:** the FCB's own DIN/USB path (it is not changed by the router), and RT priority.
- **Live smoke (same day, rig up through `qtractor_rig.py up`, router in the helper unit):**
  the wiring came up as USB Midi → FCB Router → Qtractor:FCB, with no direct link. Addresses
  injected with `aseqsend` into the router loaded the right presets (TONE3000 log: Van Halen I
  Brown, Rule the World, Satan Full Rig, then Maiden Clean/Heavy/Harmony), and the helper sent
  the Maiden song CCs. Bank 1 SW7 (CC 103 = 1) switched the Chorus on. The saved session showed
  Rate 0.6396 Hz, Depth 4.017 ms and Mix 50.39 % after the song's CC 89/90/91. So Qtractor maps
  CC 89 linearly (logarithmic=0), and `chorus_ccs` is right. Touching rig.py reloaded the layout
  within a second. A separate probe process timed 300 round trips through the live router,
  probe → router → probe (bank 0 SW2, two CCs): first message median 0.28 ms, p99 0.60 ms,
  max 0.64 ms; whole burst max 0.67 ms. That is two sequencer hops plus the probe, under audio load.

## D22 — EXP B: from a preset param to a DAW volume stage with a taper

- **Question:** the owner reported three things: toe is "super loud", a sound change resets the
  volume, and the default is too quiet next to what the pedal can reach. Why, and what fixes all three?
- **Hypothesis:** EXP B sent CC 7 straight into TONE3000's `outputLevel` (±24 dB span), so
  toe = +24 dB, 12 dB over the presets' own +12 dB and deep into the limiter. Every Program
  Change re-applies the preset's params (D8), outputLevel included, which resets the pedal. A
  gain stage in the DAW that no preset owns, with a taper and a toe only a little above the
  default, fixes all three.
- **Method:** CC 7 leaves TONE3000's MIDI map. A new "Volume" stage sits on Qtractor's Rig bus
  right before the x42 dpl limiter, so it covers heavy, clean and harmony and the limiter still
  guards it. dpl's own Input Gain only reaches −10 dB, so the stage is an LSP Slap-back Delay,
  dry only (proven here as the Solo block): "Dry amount" (0–10, linear) bound to CC 7 (hook,
  `logarithmic=0`, which D21 measured as linear), and a fixed "Output gain" of 0.25, so CC 127 =
  +8 dB, CC 1 ≈ −34 dB and CC 0 = silence. The router maps EXP B's raw 0–127 onto the CC that
  gives a dB-linear (audio) taper: −40 dB just off the heel, `VOLUME_NOMINAL_DB` = +3 dB at 78 %
  of the travel, `VOLUME_TOE_DB` = +6 dB at the toe (constants at the top of `fcb_router.py`;
  `uv run fcb_router.py volume` prints the curve). The session starts the stage at CC 72
  (+3.0 dB). Headroom: `uv run rigsim.py all --exact --before none --volume`, which now also keeps
  the pre-limiter render and runs the limiter at 0, +3 and +6 dB of stage gain.
  Raw data: volume-taper.csv, volume-headroom.csv.
- **Result, the curve (dB over the presets' own level):**

  | pedal | 0 % | 10 % | 20 % | 30 % | 40 % | 50 % | 60 % | 70 % | 75 % | 80 % | 90 % | 100 % |
  |---|---|---|---|---|---|---|---|---|---|---|---|---|
  | CC 7 | 0 | 1 | 2 | 3 | 6 | 12 | 23 | 43 | 59 | 74 | 86 | 101 |
  | dB | silent | −34.1 | −28.1 | −24.6 | −18.6 | −12.5 | −6.9 | −1.4 | +1.3 | +3.3 | +4.6 | +6.0 |

  Steps are 0.1–0.2 dB around nominal; the bottom fifth of the travel only has CC 1–2 to work
  with (≈ −34 / −28 dB), which a linear 0–10 port can't refine.
- **Result, headroom (sim, 24 presets, % of time the limiter holds > 0.5 dB / its deepest dB):**
  - Pre-limiter peaks at 0 dB: heavy presets −3.7 to −8.4 dBFS (Satan Full Rig −0.3); the cleans
    and 80s/Variety presets −2.6 to +4.1 dBFS; Def Leppard Hysteria +11.0 (it is −6.6 LUFS, 8 dB
    over the target).
  - At +3 (nominal): 15 presets limit < 1 % of the time (Maiden Heavy, Acoustic and Harmony, both Van Halens,
    Pyromania, Nirvana, Djent, the four Satans, Stormblade, Is This Love, Hysteria Clean);
    4 limit 3–6 % (Maiden Clean 5.0, Every Breath 6.4, Rule the World 3.8, Charming Man 3.0);
    4 limit 10–38 % (U2 Streets 38.4, Radiohead 35.0, Comfortably Numb 10.8, Purple Rain 10.3);
    Hysteria 98 %.
  - At +6 (toe): heavy presets 0–6 % (Nirvana 5.8), cleans 9–28 %, U2 Streets 85 %, Radiohead 63 %,
    Comfortably Numb 66 %. Old toe (+12 dB over default) was 6 dB beyond this.
- **Conclusion:** the pedal no longer resets on a preset change (CC 7 reaches only the DAW, and no
  switch burst carries CC 7: router tests), toe is +6 dB instead of +24, and the default is 3 dB
  louder. +3 is the right nominal for the heavy banks. The presets that over-limit at +3 already
  limit at 0 dB: their trims are too hot for their crest factor (17–19 dB), so the fix is per
  preset in tone.csv (Hysteria about −10 dB, U2 Streets and Radiohead −4 to −5, Comfortably Numb
  and Purple Rain −2 to −3), not a lower global nominal: even at 0 dB, Radiohead limits 10 % and
  Hysteria 98 %.
- **Not measured:** live. The owner's rig was running, so the stage hasn't been heard or
  metered in Qtractor; the sim's limiter is a sample-peak stand-in for dpl's true peak. The
  LSP stage's response to a fast sweep (zipper) is unmeasured. The FCB's EXP B may not reach
  raw 0 or 127; if heel isn't silent or toe isn't +6 dB, recalibrate it (README).

- **Revised the same day.** The owner asked "how do i quickly reset the pedal to 0db? and why is
  max at +6db?", then "whatever we should set for youtube play along". Full toe is now exactly
  0 dB (the stage's CC 127 = unity) and the rig starts there; heel is silent; the curve is
  dB-linear from -40 dB just off the heel to 0 dB at the toe. Like a real volume pedal it only
  turns down, so rocking it fully forward resets it. 0 dB is the presets' own level, which every
  preset is levelled to offline (rigsim --exact): -14 LUFS heavies, -15 cleans, matching YouTube's
  -14 LUFS normalization, so a YouTube backing track and the rig play balanced and the monitor
  knob sets both. The +3 dB baseline and the +6 dB toe are gone.

## D23 — "Palm-muted B is wildly louder": rig or room?

*Data:* `palm-boom.csv` (one row per note event: preset, variant, DI, loudness, peak, bands,
limiter), made by `palm_boom.py`.

- **Symptom (owner):** "the sound seems wildly louder when i play specific palm muted notes vs
  the rest of the sound … seems like the B note … im on maiden switch 1" (bank 0 RHYTHM =
  Maiden Heavy: JMP-1 → 1960BV V30 IR left, JCM2000 DSL → 1960A G12T-75 IR right).
- **Question:** which presets put out a louder palm mute on which pitches, and is it the amp
  capture, the cab IR or the bus? The owner's guitar turns out to be B-tuned on its low string
  (the recording runs B1 C#2 D#2 E2 F#2 G#2 A2 B2 C#3 on one string), so "the B" is B1
  (61.7 Hz) or B2 (123.5 Hz).
- **Hypothesis:** a cab IR's low resonance (the 4x12 bump at 120-180 Hz) lifts the B2
  fundamental, and the D16 bypass of the bus compressor left nothing to even it out.
- **Method:** all offline, in rigsim's engine (D19), rendered pre-limiter, with the −1 dBFS
  limiter applied per event to read its gain reduction. Two DIs:
  1. `~/Music/fcb-rig/di-palm-b.wav`, the owner's own recording of the problem (30 s, Scarlett
     Input 2, peak −3.2 dBFS; not committed). `palm_boom.py detect` finds 76 onsets with a pitch
     each (harmonic sum, within ±25 cents of a semitone on all real notes) and splits palm vs
     ringing by decay. Ghost notes under −25 dBFS input are left out of the summary.
  2. A synthetic sweep (`palm_boom.py di`): every semitone B1-B3 as a damped palm-mute burst
     (70-100 ms decay, pick click) at −6 dBFS peak, the bench DI's typical attack, then ringing
     notes and power chords at the same peak. Equal input per note, so it isolates the pitch.
  Per note: momentary loudness (BS.1770, 400 ms from the onset, L+R and per side), peak,
  60-200 Hz and fundamental-band energy, limiter GR. Attribution: the same render with both cab
  IRs disabled (`--variant no-cab`), and each cab IR's magnitude response (`palm_boom.py ir`).
  Presets: every heavy, crunch, lead and bank 4 Modern one (14).
- **Result, Maiden Heavy on the owner's DI (dB over the median palm note; n = events):**

  | note | n | input peak | out LUFS (400 ms) | over median | L (Murray) | R (Smith) | limiter GR |
  |---|---|---|---|---|---|---|---|
  | B1 61.7 Hz | 15 | −9.6 | −8.3 | −0.3 | −0.2 | +0.3 | 1.5 |
  | C#2 | 7 | −6.1 | −6.7 | +1.4 | +1.7 | +0.5 | 1.9 |
  | D#2 | 6 | −8.0 | −7.8 | +0.3 | +0.4 | +0.2 | 1.5 |
  | E2 | 7 | −11.6 | −9.5 | −1.4 | −1.7 | −0.4 | 0.8 |
  | F#2 | 6 | −12.4 | −10.2 | −2.1 | −2.3 | −1.0 | 0.0 |
  | G#2 | 5 | −10.8 | −7.4 | +0.7 | +0.2 | +2.2 | 2.1 |
  | A2 | 6 | −14.2 | −10.0 | −1.9 | −2.1 | −0.8 | 0.6 |
  | B2 123.5 Hz | 9 | −13.3 | −7.0 | +1.0 | +1.2 | +0.4 | 0.7 |
  | C#3 | 6 | −14.1 | −7.0 | +1.1 | +1.5 | −0.2 | 1.2 |

  Every note lands within −2.1 … +1.4 dB of the median; the B notes sit at −0.3 and +1.0, and
  unweighted RMS agrees (−0.2, +1.2). The limiter never holds more than 2.1 dB, and not on a B.
  Palm mutes are not louder than ringing notes here either (median palm −8.1 LUFS, ringing B1
  −8.8). On the synthetic sweep (equal input), Maiden's largest neighbour-relative bump is E3
  +1.5 dB; B1 and B2 are not outliers. The V30 IR (left) does carry the classic 4x12 bump,
  +10.3 dB at 123 Hz and +11.6 dB at its 149 Hz peak against 500-1500 Hz, so B2's fundamental
  comes out 12 dB stronger than E2's (fundamental band 33.2 vs 21.1 dB), but G#2, A2 and C#3
  get the same lift, so no note stands alone. With both cabs removed the span stays −2.6 … +1.7.
- **Result, other presets, owner's DI (notes > +2 dB over their median):**

  | preset | worst notes (dB over median) | cause | attribution (no-cab render) |
  |---|---|---|---|
  | Van Halen 1984 | B2 +5.4 (R +6.1), B1 +2.8 | ~120 Hz | amp capture: B2 still +4.5 with no cab |
  | Satan 50 Lead | B2 +4.0, C#2 +2.3 | 104-123 Hz edge of the Albion IR (−2.3 dB at 98 Hz → +11.7 at 123) | cab IR: B2 +0.3 with no cab |
  | Comfortably Numb | B1 +3.2, B2 +2.4 | ~62/123 Hz | amp (a full-rig capture, no IR) |
  | Radiohead | G#2 +4.9 (R +7.6), C#2 +3.7, B1 +3.0 | ~104 Hz, right chain | not split |
  | Stormblade | G#2 +4.7 | ~104 Hz | not split |
  | Satan 50 Modern / Low Tuned | G#2 +3.6 / C#2 +3.1 | 70-104 Hz | not split |

  Input levels vary per note in a real take (C#2 picked 7 dB harder than B2), so the synthetic
  sweep is the equal-input check: it agrees on Van Halen 1984 B2 (+3.3 dB over its neighbours)
  and Comfortably Numb B2 (+2.7). The synthetic sweep also shows Satan Full Rig and Satan 50
  Lead palm mutes 6.2-6.6 dB louder than ringing chords at the same peak (Maiden: 1.3 dB).
- **A narrow cut is possible, but needs a new column.** No tone.csv band fits a single
  semitone: eq_100 is a Q 0.71 shelf that cuts B1 and E2 harder than B2, and the eq_250 bell
  (Q 1) moves 123 Hz by about a quarter of its gain. A what-if that moved the end block's 250 Hz
  bell to 123.5 Hz at Q 4, −5 dB (`--variant notch:123.5:4:-5`) showed TONE3000 honours a
  band's freqHz and q: B2's fundamental band fell 5 dB and B2 went from +5.4 to +2.4 dB
  (Van Halen 1984) and +4.0 to +1.2 dB (Satan 50 Lead), with E2 within 0.9 dB. Band types are
  positional, though: the same move on band 0 (the low shelf) made a resonant shelf that cut
  G#2/A2 by 5-6 dB and lifted C#2 4 dB. So a notch belongs on the bell, as its own
  `notch_hz`/`notch_q`/`notch_db` columns rather than a reused eq_250.
- **Conclusion:** the rig does not make palm-muted B louder on Maiden Heavy. On the owner's
  own take, through the real engine, B1 and B2 sit within 1 dB of the other notes on both
  chains. So the jump the owner hears is downstream of the rig: the speakers or the room. A
  room dimension of about 2.8 m (a typical ceiling height) has axial modes at 61.7 and
  123.4 Hz, which are exactly B1 and B2. Neither the PC nor PipeWire adds any EQ between the
  rig and the Scarlett's outputs. No preset changed. The owner tests it in
  `~/Music/fcb-rig/d23/`:
  `maiden-heavy-render-of-di-palm-b.wav` (their own take through Maiden Heavy, limiter on) on
  headphones vs the speakers, and `sine-bursts-A1-to-D3.wav` (equal-level sine bursts, one per
  semitone 55-147 Hz; the 3rd and 15th are B1 and B2). A burst that jumps out on the speakers
  but not on headphones is the room or the speakers. The remedy then goes on the monitor path
  (speaker placement, or a notch on the master output), not in the presets. Van Halen 1984 and
  Satan 50 Lead do have a real B2 bump in the rig, which a notch column would fix (numbers above).
- **Not measured:** the room and speakers (that needs a mic at the playing position).
  Not done live: the owner was playing. The synthetic palm mute is a model (additive partials).
  Its absolute spread across the sweep (low notes quieter at equal peak) is a property of the
  model, so only neighbour-relative bumps and the owner's DI are read as outliers.
