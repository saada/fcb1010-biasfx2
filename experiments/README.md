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
