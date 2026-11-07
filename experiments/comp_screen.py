# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9.26", "numpy"]
# ///
"""Which compressor capture holds a clean preset's pick attacks? (experiments D31)

Swaps the Diamond in slot 1 of a preset for each candidate, renders the JP70 DI and stepped sines,
and prints distortion, loudness and how far the peaks stand above the loudness. For a given
loudness, a lower crest means the bus limiter has less to do.

  uv run experiments/comp_screen.py "Maiden Clean" [--out DIR] [--csv F]
"""

import argparse
import copy
import csv
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import rigsim  # noqa: E402
import thd_bench as tb  # noqa: E402
import tone3000 as t3k  # noqa: E402

DI = Path.home() / "Music/fcb-rig/di-ref.wav"
COMPS = {
    "none": None,
    "Diamond 5 (now)": (88975, 749582),
    "Diamond 9": (88975, 749583),
    "Lin76 4:1 fast": (86750, 736974),
    "Lin76 8:1 fast": (86750, 735537),
    "Lin76 4:1 med": (86750, 736990),
    "Dynacomp L3 S12": (84038, 716317),
    "Dynacomp L3 S3": (84038, 779444),
    "Dyna Comp 1 oclock": (103646, 855084),
    "Cali76 noon": (88720, 747903),
    "Atlas FET": (102929, 849352),
    "Atlas Optical": (102929, 849471),
    "LA-2A medium": (69103, 876438),
    "SP Comp Mid": (70193, 580601),
    "SP Comp High": (70193, 580602),
}


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("preset")
    ap.add_argument("--out", default="/tmp/comp-screen")
    ap.add_argument("--csv")
    a = ap.parse_args()
    out = Path(a.out)
    (out / "thd").mkdir(parents=True, exist_ok=True)
    rigs = rigsim.rigs_from([json.loads(f.read_text()) | {"_file": f.name} for f in sorted(t3k.RIGS.glob("*.json"))],
                            (t3k.RIGS / "tone.csv").read_text())
    base = next(r for r in rigs if r["name"] == a.preset)
    cands = []
    for name, c in COMPS.items():
        r = copy.deepcopy(base)
        r["name"] = name
        slot = next(b for b in r["left"] if b["role"] == "boost")
        slot.clear()
        slot.update(dict(role="boost", type="nam", tone_id=c[0], model_id=c[1], enabled=True, label=name) if c else
                    dict(role="boost", type="insert"))
        cands.append(r)

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
    keys, res, _ = rigsim.simulate(cands, exact=True, use_cache=False, jobs=6)
    rows = []
    for r, k in zip(cands, keys):
        m, t = res[k], thd[r["name"]]
        # pre-limiter peak over the pre-limiter level: what a -15 LUFS trim leaves for the limiter
        rows.append(dict(name=r["name"], thd110=round(t[110.0, -12][1], 1), thd330=round(t[330.0, -12][1], 1),
                         thd330_6=round(t[330.0, -6][1], 1), lufs=m["lufs"], pre_peak=m["pre_peak_dbfs"],
                         peak_over_lufs=round(m["pre_peak_dbfs"] - m["lufs"], 1), presence=m["presence"], centroid=m["centroid_hz"]))
    rows.sort(key=lambda x: x["peak_over_lufs"])
    if a.csv:
        with open(a.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    print(f"{'compressor':22}{'THD110':>8}{'THD330':>8}{'330@-6':>8}{'LUFS':>8}{'prePk':>7}{'pk-LUFS':>9}{'2-5k':>7}{'cent':>7}")
    for x in rows:
        print(f"{x['name']:22}{x['thd110']:8}{x['thd330']:8}{x['thd330_6']:8}{x['lufs']:8}{x['pre_peak']:7}{x['peak_over_lufs']:9}{x['presence']:7}{x['centroid']:7}")


if __name__ == "__main__":
    main()
