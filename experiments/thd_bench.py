# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9.26", "numpy"]
# ///
"""Where in a preset's chain does a clean sound distort? (experiments D31)

Plays stepped sine tones at guitar levels through a preset, then through copies with one stage
removed, in the real TONE3000 engine (rigsim), and prints THD per input level.

  uv run experiments/thd_bench.py "Maiden Acoustic" [--strip] [--out DIR] [--csv F]

--strip: also render each variant with one capture or the EQ removed, to name the culprit.
THD = harmonics 2-8 over the fundamental, on the left output, in dB; the last 0.5 s of each step.
The reverbs are linear, so they add no harmonics, and the post-model EQ does count, because it
tilts what the ear hears. The input levels bracket the owner's JP70 DI (peak -1.3 dBFS, RMS -22).
"""

import argparse
import copy
import csv
import json
import struct
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import rigsim  # noqa: E402
import tone3000 as t3k  # noqa: E402

RATE = 48000
FREQS = (110.0, 330.0)
LEVELS = (-36, -30, -24, -18, -12, -9, -6, -3)
STEP_S, GAP_S = 1.0, 1.5


def write_tones(path):
    parts = []
    for f in FREQS:
        for db in LEVELS:
            t = np.arange(int(STEP_S * RATE)) / RATE
            env = np.minimum(1, np.minimum(t, STEP_S - t) / 0.01)
            parts.append(10 ** (db / 20) * np.sin(2 * np.pi * f * t) * env)
        parts.append(np.zeros(int(GAP_S * RATE)))
    y = np.concatenate(parts).astype("<f4")
    raw = y.tobytes()
    path.write_bytes(struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(raw), b"WAVE", b"fmt ", 16, 3, 1, RATE,
                                 RATE * 4, 4, 32, b"data", len(raw)) + raw)


def read_float_wav(path):
    b = Path(path).read_bytes()
    return np.frombuffer(b[44:], "<f4").reshape(-1, 2).astype(np.float64)


def amp_at(x, f):
    t = np.arange(len(x)) / RATE
    w = np.hanning(len(x))
    return 2 * abs(np.sum(x * w * np.exp(-2j * np.pi * f * t))) / w.sum()


def thd_table(y):
    """{(freq, level dB): (output fundamental dB, THD dB)}."""
    out, pos = {}, 0
    for f in FREQS:
        for db in LEVELS:
            seg = y[pos + int(0.45 * RATE):pos + int(0.95 * RATE), 0]
            fund = amp_at(seg, f)
            harm = np.sqrt(sum(amp_at(seg, f * h) ** 2 for h in range(2, 9) if f * h < RATE / 2.2))
            out[f, db] = (20 * np.log10(fund + 1e-12), 20 * np.log10(harm / (fund + 1e-12) + 1e-12))
            pos += int(STEP_S * RATE)
        pos += int(GAP_S * RATE)
    return out


def strip(rig, role):
    """A copy of `rig` without the named stage on either side."""
    r = copy.deepcopy(rig)
    r["name"] = f"{rig['name']} no {role}"
    for side in ("left", "right"):
        for b in r.get(side, []):
            if role == "eq":
                b.pop("eq", None)
            elif b.get("role") == role:
                b.clear()
                b.update(role=role, type="insert")
    if role == "eq" or role == r.get("voicing_block", "amp"):
        r.pop("voicing", None)
        r.pop("voicing_block", None)
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preset", nargs="+")
    ap.add_argument("--strip", action="store_true")
    ap.add_argument("--out", default="/tmp/thd-bench")
    ap.add_argument("--csv")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    tones = out / "tones.wav"
    write_tones(tones)
    rigsim.set_di(str(tones))
    rigs = rigsim.rigs_from([json.loads(f.read_text()) | {"_file": f.name} for f in sorted(t3k.RIGS.glob("*.json"))],
                            (t3k.RIGS / "tone.csv").read_text())
    chosen = [r for r in rigs if r["name"] in a.preset]
    variants = []
    for r in chosen:
        variants.append(r)
        if a.strip:
            roles = sorted({b["role"] for s in ("left", "right") for b in r.get(s, []) if b.get("type") == "nam"})
            variants += [strip(r, role) for role in roles] + [strip(r, "eq")]
    keys, _, _ = rigsim.simulate(variants, exact=True, use_cache=False, wav=str(out))
    rows = []
    for r, k in zip(variants, keys):
        y = read_float_wav(out / f"{r['name'].replace('/', '_')}-{k[:8]}.wav")
        for (f, db), (fund, thd) in thd_table(y).items():
            rows.append(dict(preset=r["name"], freq_hz=f, in_db=db, out_db=round(fund, 1), thd_db=round(thd, 1)))
    if a.csv:
        with open(a.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    for f in FREQS:
        print(f"\n{f:.0f} Hz: THD dB (lower = cleaner) by input level")
        print(f"{'':32}" + "".join(f"{d:>7}" for d in LEVELS))
        for r in variants:
            cells = [next(x["thd_db"] for x in rows if x["preset"] == r["name"] and x["freq_hz"] == f and x["in_db"] == d)
                     for d in LEVELS]
            print(f"{r['name'][:31]:32}" + "".join(f"{c:7.1f}" for c in cells))


if __name__ == "__main__":
    main()
