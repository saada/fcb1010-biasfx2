# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9.26", "numpy"]
# ///
"""Palm-mute boom bench (experiments D23): which presets jump in level on which palm-muted
notes, and is it the amp capture, the cab IR or the bus?

  uv run experiments/palm_boom.py di   OUT.wav          # write the palm-mute test DI
  uv run experiments/palm_boom.py detect DI.wav EVENTS.json   # onsets, pitch, palm/ring of a recorded DI
  uv run experiments/palm_boom.py run  DI.wav OUT.csv [SELECTOR ...] [--events EVENTS.json] [--variant V]
                                   [--sheet tone.csv]   # render (rigsim engine) and measure per note
                                                         # (events default: DI.json, as `di` writes)
  uv run experiments/palm_boom.py ir   [SELECTOR ...]    # cab IR magnitude around the low notes

The DI: every semitone B1 (61.7 Hz) .. B3 (246.9 Hz) as a damped palm-mute burst (fundamental +
harmonics, highs decaying faster, 70-130 ms, a 4 ms pick-noise click), each peaking at
PEAK_DBFS, which is the bench DI's (di-ref.wav) typical note-attack peak; then open-ringing
references at the same peak: single notes E2 A2 D3 G3 B3 and power chords E5 A5 B5 with 1.5 s decay.
Each event sits alone in a SLOT-long window so the gate and the amp recover between notes.

Measured per event, on the render before the bus limiter: momentary loudness (BS.1770 K-weighted,
L+R, the 400 ms after the onset), sample peak, 60-200 Hz energy, and the limiter's deepest gain
reduction on that event (the -1 dBFS stand-in for x42 dpl). Left/right columns are the two chains
of a split rig (Murray L, Smith R on Maiden). `over_median_db` = this palm note's loudness minus
the preset's median palm note.

Variants (attribution): "as-is", "no-cab" (both cab IRs disabled), "no-cab-L"/"no-cab-R",
"notch:HZ:Q:DB" (what-if narrow cut, see variant()). WAVs land in OUT/ beside OUT.csv.
"""

import csv
import json
import math
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import rigsim  # noqa: E402
import tone3000 as t3k  # noqa: E402

RATE = 48000
PEAK_DBFS = -6.0  # di-ref.wav: note attacks peak -1.3 .. -14 dBFS, median of onsets about -7.5
SLOT = 0.6
NAMES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]


def midi_name(m):
    return f"{NAMES[m % 12]}{m // 12 - 1}"


def hz(m):
    return 440.0 * 2 ** ((m - 69) / 12)


PALM = list(range(35, 60))  # B1 .. B3 (MIDI 35 = B1 61.7 Hz, 59 = B3 246.9 Hz)
RING = [("E2", [40]), ("A2", [45]), ("D3", [50]), ("G3", [55]), ("B3", [59]),
        ("E5 chord", [40, 47, 52]), ("A5 chord", [45, 52, 57]), ("B5 chord", [47, 54, 59])]


def string_note(f0, dur, tau, rng, damp=True):
    """Fundamental + 12 partials, slight inharmonicity, upper partials decay faster when palm-muted."""
    t = np.arange(int(dur * RATE)) / RATE
    y = np.zeros_like(t)
    for k in range(1, 13):
        fk = f0 * k * math.sqrt(1 + 0.00008 * k * k)
        if fk > 8000:
            break
        a = 1.0 / k ** (1.3 if damp else 0.9)
        tk = tau / (1 + (0.6 if damp else 0.15) * (k - 1))
        y += a * np.exp(-t / tk) * np.sin(2 * np.pi * fk * t + rng.uniform(0, 2 * np.pi))
    att = np.minimum(1, t / 0.002)  # 2 ms pick onset
    n = int(0.004 * RATE)
    click = np.zeros_like(t)
    noise = rng.standard_normal(n) * np.exp(-np.arange(n) / (0.001 * RATE))
    click[:n] = np.diff(np.concatenate([[0], noise])) * 0.25  # crude high-passed pick noise
    return y * att + click


def make_di(path):
    rng = np.random.default_rng(23)
    events, chunks = [], []
    lead = np.zeros(int(0.5 * RATE))
    chunks.append(lead)
    t = len(lead) / RATE
    peak = 10 ** (PEAK_DBFS / 20)
    for m in PALM:
        tau = 0.10 - 0.03 * (m - 35) / 24  # 100 ms on B1 .. 70 ms on B3 (higher notes damp faster)
        y = string_note(hz(m), SLOT, tau, rng)
        y *= peak / np.abs(y).max()
        events.append(dict(kind="palm", note=midi_name(m), f0=round(hz(m), 1), t=t, dur=SLOT))
        chunks.append(y)
        t += SLOT
    for name, notes in RING:
        dur = 2.0
        y = sum(string_note(hz(m), dur, 1.5, rng, damp=False) for m in notes)
        y *= peak / np.abs(y).max()
        events.append(dict(kind="ring", note=name, f0=round(hz(notes[0]), 1), t=t, dur=dur))
        chunks.append(y)
        t += dur
    chunks.append(np.zeros(RATE // 2))
    x = np.concatenate(chunks).astype(np.float32)
    raw = x.astype("<f4").tobytes()
    import struct
    hdr = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(raw), b"WAVE", b"fmt ", 16, 3, 1, RATE, RATE * 4, 4, 32,
                      b"data", len(raw))
    Path(path).write_bytes(hdr + raw)
    Path(path).with_suffix(".json").write_text(json.dumps(events, indent=1))
    print(f"{path}: {len(x) / RATE:.1f} s, {len(events)} events, peak {PEAK_DBFS} dBFS each")


def detect(di, out_json):
    """Onsets (10 ms energy flux), pitch (harmonic sum, 40-300 ms after the onset) and palm vs
    ring (energy 250-450 ms after the onset vs the first 100 ms) of a recorded DI."""
    x = rigsim.read_wav_mono(di).astype(float)
    hop = 480
    e = np.sqrt((x[:len(x) // hop * hop].reshape(-1, hop) ** 2).mean(1)) + 1e-9
    db = 20 * np.log10(e)
    ons, last = [], -99
    for i in range(3, len(db)):
        if db[i] - db[i - 3:i].min() > 9 and db[i] > -40 and i - last > 12:
            j = i + int(np.argmax(db[i:i + 3]))
            ons.append(i)
            last = i
    events = []
    for n, i in enumerate(ons):
        a = i * hop
        nxt = ons[n + 1] * hop if n + 1 < len(ons) else len(x)
        dur = (nxt - a) / RATE
        seg = x[a + int(0.04 * RATE):a + int(min(0.3, dur) * RATE)]
        N = 1 << 16
        X = np.abs(np.fft.rfft(seg * np.hanning(len(seg)), N))
        f = np.fft.rfftfreq(N, 1 / RATE)
        cand = np.arange(55, 330, 0.25)
        mag = lambda fr: np.interp(fr, f, X)
        score = sum(mag(cand * k) / k ** 0.5 for k in range(1, 7)) * (mag(cand) > 0.1 * X[(f > 50) & (f < 2000)].max())
        f0 = float(cand[np.argmax(score)])
        m = 69 + 12 * math.log2(f0 / 440)
        early = (x[a:a + int(0.1 * RATE)] ** 2).mean()
        late = (x[a + int(0.25 * RATE):a + int(0.45 * RATE)] ** 2).mean() if dur > 0.45 else 0.0
        decay_db = 10 * math.log10(late / early + 1e-12) if late else -99.0
        events.append(dict(kind="ring" if decay_db > -8 else "palm", note=midi_name(round(m)), f0=round(f0, 1),
                           cents=round((m - round(m)) * 100), t=round(a / RATE, 3), dur=round(min(dur, 2.0), 3),
                           in_peak_dbfs=round(20 * math.log10(np.abs(x[a:a + int(0.1 * RATE)]).max() + 1e-12), 1),
                           decay_db=round(decay_db, 1)))
    Path(out_json).write_text(json.dumps(events, indent=1))
    for ev in events:
        print(ev)


def band_db(y, lo, hi):
    n = 1 << int(math.ceil(math.log2(len(y))))
    X = (np.abs(np.fft.rfft(y * np.hanning(len(y))[:, None], n, axis=0)) ** 2).sum(1)
    f = np.fft.rfftfreq(n, 1 / RATE)
    return 10 * math.log10(X[(f >= lo) & (f < hi)].sum() / len(y) + 1e-30)


def measure_events(y, events):
    """y: (n, 2) pre-limiter render aligned with the DI."""
    kw = rigsim.k_weight(y) ** 2
    gr = -20 * np.log10(rigsim.limiter_gain(y))
    rows = []
    for e in events:
        a = int(e["t"] * RATE)
        w = a + int(min(0.4, e["dur"]) * RATE)
        mom = lambda ch: -0.691 + 10 * math.log10(kw[a:w][:, ch].sum(1).mean() + 1e-20) if isinstance(ch, list) else \
            -0.691 + 10 * math.log10(kw[a:w, ch].mean() + 1e-20)
        seg = y[a:a + int(min(e["dur"], 0.5) * RATE)]
        rows.append(dict(t=e["t"], kind=e["kind"], note=e["note"], f0=e["f0"], in_peak_dbfs=e.get("in_peak_dbfs", ""),
                         mom_lufs=round(mom([0, 1]), 2), left_lufs=round(mom(0), 2), right_lufs=round(mom(1), 2),
                         peak_dbfs=round(20 * math.log10(np.abs(seg).max() + 1e-20), 2),
                         low_60_200_db=round(band_db(seg, 60, 200), 2),
                         rms_db=round(10 * math.log10((y[a:w] ** 2).sum(1).mean() + 1e-20), 2),
                         f0_band_db=round(band_db(y[a:w], e["f0"] / 2 ** (1 / 6), e["f0"] * 2 ** (1 / 6)), 2),
                         h2_band_db=round(band_db(y[a:w], 2 * e["f0"] / 2 ** (1 / 6), 2 * e["f0"] * 2 ** (1 / 6)), 2),
                         limiter_gr_db=round(float(gr[a:a + int(e["dur"] * RATE)].max()), 2)))
    palm = [r["mom_lufs"] for r in rows if r["kind"] == "palm"]
    med = float(np.median(palm))
    ring = [r for r in rows if r["kind"] == "ring"]
    ring_low = float(np.median([r["low_60_200_db"] for r in ring]))
    for side in ("left", "right"):
        smed = float(np.median([r[f"{side}_lufs"] for r in rows if r["kind"] == "palm"]))
        for r in rows:
            r[f"{side}_over_db"] = round(r[f"{side}_lufs"] - smed, 2) if r["kind"] == "palm" else ""
    for r in rows:
        r["over_median_db"] = round(r["mom_lufs"] - med, 2) if r["kind"] == "palm" else ""
        r["low_vs_ring_db"] = round(r["low_60_200_db"] - ring_low, 2)
    return rows


def variant(rig, v):
    """"notch:HZ:Q:DB" = a what-if narrow cut: the end block's 250 Hz bell (EQ band 1) moves to HZ
    with Q and is cut by DB, on both chains. Patches t3k.EQ_BANDS for this
    process only, to test whether TONE3000 honours a band's freqHz/q (no tone.csv column yet)."""
    rig = json.loads(json.dumps(rig))
    if v.startswith("notch:"):
        _, f, q, db = v.split(":")
        t3k.EQ_BANDS[1] = ("bell", float(f), float(q))
        for side in ("left", "right"):
            if b := t3k.end_block(rig.get(side, [])):
                eq = list(b.get("eq") or [0.0] * 6)
                eq[1] = float(db)
                b["eq"] = eq
    for side in ("left", "right"):
        if v in ("no-cab", f"no-cab-{side[0].upper()}"):
            for b in rig.get(side, []):
                if b.get("role") == "cab":
                    b["enabled"] = False
    return rig


def run(di, out_csv, selectors, v="as-is", sheet=None, events_path=None):
    events = json.loads(Path(events_path or Path(di).with_suffix(".json")).read_text())
    rigsim.set_di(di)
    rigsim.limiter = lambda y: y  # keep the render pre-limiter; the limiter is measured per event
    if sheet:
        rigs = rigsim.rigs_from([json.loads(f.read_text()) | {"_file": f.name} for f in sorted(t3k.RIGS.glob("*.json"))],
                                Path(sheet).read_text())
    else:
        rigs = t3k.load_rigs()
    chosen = [r for r in rigsim.select(selectors or ["@heavy", "@crunch", "@lead", "@modern"], rigs)
              if "harmony" not in r["name"].lower()]
    chosen = [variant(r, v) for r in chosen]
    wavdir = Path(out_csv).with_suffix("")
    wavdir.mkdir(parents=True, exist_ok=True)
    keys, results, _ = rigsim.simulate(chosen, exact=True, wav=str(wavdir), use_cache=False)
    allrows = []
    for r, k in zip(chosen, keys):
        wav = wavdir / f"{r['name'].replace('/', '_')}-{k[:8]}.wav"
        if not wav.exists():
            print(f"!! {r['name']}: {results[k].get('error')}")
            continue
        chans, _ = t3k.wav_read(wav.read_bytes())
        y = np.stack([np.asarray(c, float) for c in chans], 1)
        rows = measure_events(y, events)
        for row in rows:
            allrows.append(dict(pc=r["pc"], preset=r["name"], variant=v, **row))
        palm = [x for x in rows if x["kind"] == "palm"]
        ring = [x for x in rows if x["kind"] == "ring"]
        worst = sorted(palm, key=lambda x: -x["over_median_db"])[:3]
        print(f"{r['pc']:>3} {r['name'][:26]:<26} palm med {np.median([x['mom_lufs'] for x in palm]):6.1f}  "
              f"ring med {np.median([x['mom_lufs'] for x in ring]):6.1f}  span {max(x['over_median_db'] for x in palm) - min(x['over_median_db'] for x in palm):4.1f}  worst "
              + ", ".join(f"{x['note']} {x['over_median_db']:+.1f} (L{x['left_over_db']:+.1f}/R{x['right_over_db']:+.1f})" for x in worst)
              + f"  maxGR {max(x['limiter_gr_db'] for x in rows):.1f}")
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(allrows[0]))
        w.writeheader()
        w.writerows(allrows)


def ir_response(selectors):
    rigs = t3k.load_rigs()
    for r in rigsim.select(selectors or ["@heavy", "@crunch", "@lead", "@modern"], rigs):
        if "harmony" in r["name"].lower():
            continue
        for side in ("left", "right"):
            for b in r.get(side, []):
                if b.get("role") != "cab" or b.get("type") != "ir" or not b.get("enabled", True):
                    continue
                raw = t3k.local_path(b).read_bytes() if b.get("file") else \
                    t3k.model_file(t3k.model_of(b["tone_id"], b["model_id"]))
                chans, rate = t3k.wav_read(raw)
                h = np.asarray(chans[0], float)
                n = 1 << 18
                H = 20 * np.log10(np.abs(np.fft.rfft(h, n)) + 1e-12)
                f = np.fft.rfftfreq(n, 1 / rate)
                ref = H[(f > 500) & (f < 1500)].mean()
                pts = [62, 73, 82, 92, 98, 104, 110, 117, 123, 131, 139, 147, 165, 185, 208, 247, 300]
                vals = " ".join(f"{H[np.argmin(abs(f - p))] - ref:+5.1f}" for p in pts)
                lo = (f > 55) & (f < 300)
                pk = f[lo][np.argmax(H[lo])]
                print(f"{r['pc']:>3} {r['name'][:22]:<22} {side[0].upper()} {b['label'][:40]:<40} peak {pk:5.0f} Hz "
                      f"{H[lo].max() - ref:+5.1f} dB | " + vals)
    print("Hz: 62 73 82 92 98 104 110 117 123 131 139 147 165 185 208 247 300 (dB re 500-1500 Hz mean)")


if __name__ == "__main__":
    cmd = sys.argv[1]
    if cmd == "di":
        make_di(sys.argv[2])
    elif cmd == "run":
        args = sys.argv[2:]
        v = "as-is"
        sheet = None
        if "--variant" in args:
            i = args.index("--variant"); v = args[i + 1]; del args[i:i + 2]
        if "--sheet" in args:
            i = args.index("--sheet"); sheet = args[i + 1]; del args[i:i + 2]
        ev = None
        if "--events" in args:
            i = args.index("--events"); ev = args[i + 1]; del args[i:i + 2]
        run(args[0], args[1], args[2:], v, sheet, ev)
    elif cmd == "detect":
        detect(sys.argv[2], sys.argv[3])
    elif cmd == "ir":
        ir_response(sys.argv[2:])
