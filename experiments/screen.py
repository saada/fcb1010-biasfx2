# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9.26", "numpy"]
# ///
"""Screen candidate chains from a JSON spec: distortion, tone, level-matched auditions (D32).

  uv run experiments/screen.py SPEC.json [--only NAME ...] [--out DIR] [--csv F] [--no-wav]

SPEC = {"base": "<preset name the candidates start from>", "target_lufs": -14,
        "candidates": {"name": {"left": [...], "right": [...], "split_after": 2, "params": {...},
                                "chorus": {...}}, ...}}
Each candidate replaces the base preset's chains (rigs/README.md block format; `voicing` works as
in a preset). Every candidate renders through the real TONE3000 engine twice: stepped sines for
THD (experiments/thd_bench.py) and the owner's JP70 DI for tone. A candidate with a `chorus`
block gets the numpy chorus stand-in from experiments/maiden_candidates.py, because rigsim has
no DAW chorus. Each audition WAV is balanced L/R, trimmed to the target and limited like the rig
bus, then written to ~/Music/fcb-rig/audition/<prefix>-<name>.wav (prefix = the spec's stem).
"""

import argparse
import copy
import csv
import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import maiden_candidates as mc  # noqa: E402
import rigsim  # noqa: E402
import thd_bench as tb  # noqa: E402
import tone3000 as t3k  # noqa: E402

RATE = 48000
DI = Path.home() / "Music/fcb-rig/di-ref.wav"
AUDITION = Path.home() / "Music/fcb-rig/audition"


def candidate(base, name, spec):
    r = copy.deepcopy(base)
    r["name"] = name
    for k in ("left", "right", "split_after", "params", "voicing", "voicing_block", "chorus"):
        r.pop(k, None)
    r.update(copy.deepcopy({k: v for k, v in spec.items() if k != "note"}))
    r.setdefault("params", {})
    errs = t3k.expand_voicing(r)
    if errs:
        sys.exit(f"{name}: {errs}")
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("spec")
    ap.add_argument("--only", nargs="*")
    ap.add_argument("--out", default="/tmp/screen")
    ap.add_argument("--csv")
    ap.add_argument("--no-wav", action="store_true")
    a = ap.parse_args()
    spec = json.loads(Path(a.spec).read_text())
    prefix = Path(a.spec).stem
    out = Path(a.out) / prefix
    (out / "thd").mkdir(parents=True, exist_ok=True)
    (out / "di").mkdir(parents=True, exist_ok=True)
    rigs = rigsim.rigs_from([json.loads(f.read_text()) | {"_file": f.name} for f in sorted(t3k.RIGS.glob("*.json"))],
                            (t3k.RIGS / "tone.csv").read_text())
    base = next(r for r in rigs if r["name"] == spec["base"])
    target = spec.get("target_lufs", -14.0)
    cands = [candidate(base, n, s) for n, s in spec["candidates"].items() if not a.only or n in a.only]

    tones = out / "tones.wav"
    tb.write_tones(tones)
    rigsim.set_di(str(tones))
    keys, res, _ = rigsim.simulate(cands, exact=True, use_cache=False, wav=str(out / "thd"), jobs=6)
    thd = {}
    for r, k in zip(cands, keys):
        if "error" in res[k]:
            print(f"skipped {r['name']}: {res[k]['error']}"[:200])
            continue
        thd[r["name"]] = tb.thd_table(tb.read_float_wav(out / "thd" / f"{r['name']}-{k[:8]}.wav"))
    cands = [r for r in cands if r["name"] in thd]

    rigsim.set_di(str(DI))
    keys, res, _ = rigsim.simulate(cands, exact=True, use_cache=False, wav=str(out / "di"), jobs=6)
    a_, b_ = int(4.0 * RATE), int(14.5 * RATE)
    rows = []
    for r, k in zip(cands, keys):
        y = tb.read_float_wav(out / "di" / f"{r['name']}-{k[:8]}.wav")
        if r.get("chorus"):
            y = mc.apply_chorus(y, {"rate_hz": 0.6, "depth_ms": 4.0, "mix": 0.5} | r["chorus"])  # qtractor_rig.CHORUS_DEFAULT
        m0 = rigsim.measure(y[a_:b_])
        side = (m0["left_lufs"] - m0["right_lufs"]) / 2 if r.get("right") else 0.0
        y = y * np.array([10 ** (-side / 20), 10 ** (side / 20)])
        gain = target - rigsim.measure(y[a_:b_])["lufs"]
        y = y * 10 ** (gain / 20)
        gr = 20 * np.log10(np.maximum(rigsim.limiter_gain(y), 1e-9))[a_:b_]
        y = rigsim.limiter(y)
        m = rigsim.measure(y[a_:b_])
        if not a.no_wav:
            AUDITION.mkdir(parents=True, exist_ok=True)
            mc.write_wav(AUDITION / f"{prefix}-{r['name']}.wav", y)
        t = thd[r["name"]]
        rows.append(dict(name=r["name"], gain_db=round(gain, 1), side_db=round(side, 1),
                         thd110_m12=round(t[110.0, -12][1], 1), thd330_m12=round(t[330.0, -12][1], 1),
                         thd330_m3=round(t[330.0, -3][1], 1), low=m["low"], lowmid=m["lowmid"], mid=m["mid"],
                         presence=m["presence"], air=m["air"], centroid=m["centroid_hz"], crest=m["crest_db"],
                         limiter_pct=round(float((gr < -0.5).mean() * 100), 2), limiter_max=round(float(gr.min()), 1)))
    if a.csv:
        with open(a.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    cols = ["gain_db", "thd110_m12", "thd330_m12", "thd330_m3", "low", "lowmid", "mid", "presence", "centroid", "limiter_pct", "limiter_max"]
    print(f"{'candidate':26}" + "".join(f"{c[:9]:>10}" for c in cols))
    for x in rows:
        print(f"{x['name'][:25]:26}" + "".join(f"{x[c]!s:>10}" for c in cols))


if __name__ == "__main__":
    main()
