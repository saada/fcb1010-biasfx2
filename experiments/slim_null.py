# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9.26", "numpy"]
# ///
"""Does TONE3000's LITE A2 size change the sound? (experiments D31)

Renders a preset's NAM blocks at slim 1 (FULL), 0.5 and 0 (LITE), wobble off so the renders can
null, then prints each one's difference from FULL over the JP70 DI, its tone bands and its THD.

  uv run experiments/slim_null.py "Maiden Clean" [--out DIR] [--csv F]
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
import rigsim  # noqa: E402
import thd_bench as tb  # noqa: E402
import tone3000 as t3k  # noqa: E402

RATE = 48000
DI = Path.home() / "Music/fcb-rig/di-ref.wav"


def sized(rig, slim):
    r = copy.deepcopy(rig)
    r["name"] = f"slim {slim}"
    r["params"] = dict(r.get("params", {}), alignWobbleEnabled=0.0, alignWobble=0.0)
    for b in r["left"] + r.get("right", []):
        if b.get("type") == "nam":
            b["slim"] = slim
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preset")
    ap.add_argument("--out", default="/tmp/slim-null")
    ap.add_argument("--csv")
    a = ap.parse_args()
    out = Path(a.out)
    (out / "thd").mkdir(parents=True, exist_ok=True)
    (out / "di").mkdir(parents=True, exist_ok=True)
    rigs = rigsim.rigs_from([json.loads(f.read_text()) | {"_file": f.name} for f in sorted(t3k.RIGS.glob("*.json"))],
                            (t3k.RIGS / "tone.csv").read_text())
    base = next(r for r in rigs if r["name"] == a.preset)
    vs = [sized(base, s) for s in (1.0, 0.5, 0.0)]
    tones = out / "tones.wav"
    tb.write_tones(tones)
    rigsim.set_di(str(tones))
    keys, _, _ = rigsim.simulate(vs, exact=True, use_cache=False, wav=str(out / "thd"), jobs=3)
    thd = {r["name"]: tb.thd_table(tb.read_float_wav(out / "thd" / f"{r['name']}-{k[:8]}.wav")) for r, k in zip(vs, keys)}
    rigsim.set_di(str(DI))
    keys, res, _ = rigsim.simulate(vs, exact=True, use_cache=False, wav=str(out / "di"), jobs=3)
    a_, b_ = int(4.0 * RATE), int(14.5 * RATE)
    ys = {r["name"]: tb.read_float_wav(out / "di" / f"{r['name']}-{k[:8]}.wav")[a_:b_] for r, k in zip(vs, keys)}
    ref = ys["slim 1.0"]
    rows = []
    for r, k in zip(vs, keys):
        m, y = res[k], ys[r["name"]]
        best = min(range(-48, 49), key=lambda d: float(((np.roll(y, d, axis=0) - ref) ** 2).sum()))  # latency may differ
        diff = 10 * np.log10(((np.roll(y, best, axis=0) - ref) ** 2).sum() / (ref ** 2).sum() + 1e-15)
        t = thd[r["name"]]
        rows.append(dict(size=r["name"], null_db=round(float(diff), 1), shift=best, lufs=m["lufs"], low=m["low"], lowmid=m["lowmid"], mid=m["mid"],
                         presence=m["presence"], air=m["air"], centroid=m["centroid_hz"],
                         thd110_m12=round(t[110.0, -12][1], 1), thd330_m12=round(t[330.0, -12][1], 1), thd330_m6=round(t[330.0, -6][1], 1)))
        print(rows[-1])
    if a.csv:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


if __name__ == "__main__":
    main()
