# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9.26", "numpy"]
# ///
"""Check rigsim against the live DI bench: rebuild every bank-3 preset the bench measured
(experiments D17/D18, bank3-levels.csv and bank3-cabs.csv) from git, render it, and print
simulated vs live loudness, bands and centroid.

  uv run rigsim_validate.py [--di DI.wav] [--jobs N] [--csv OUT.csv]

D18 rows (passes 5-6, cab candidates, Stormblade) used the same 4.0-14.5 s window as
`rigsim.py --exact`. D17 rows (passes 1-4) measured 5.2 s at an unrecorded point of the DI
loop, so expect an offset there; their scatter still says something.
"""
import argparse
import csv
import json
import math
import os
import subprocess
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
import rigsim as rs  # noqa: E402

t3k = rs.t3k
EXP = rs.REPO / "experiments"
D17, D18 = "71e630c", "fb43aed"  # rigs/ as levelled in D17, and after D18
LANC = "~/Music/fcb-rig/packs/lancaster/"
CABS = {"Res New Old Dude (catalog 5996)": None,
        "Lancaster PLAP Cameron Webb Ubershall [57]": LANC + "Cameron Webb Ubershall [57].wav",
        "Lancaster PLAP Warren Huart Marsh v25": LANC + "Warren Huart Marsh v25 1.wav",
        "Lancaster PLAP Ulrich Wild Albion [D4]": LANC + "Ulrich Wild Albion [D4].wav"}
FILES = {13: "13-satan-full-rig.json", 14: "14-satan-50-modern.json", 15: "15-satan-50-low-tuned.json",
         16: "16-satan-50-lead.json", 17: "17-satan-wall.json"}


def git_rig(rev, name):
    out = subprocess.run(["git", "-C", str(rs.REPO), "show", f"{rev}:rigs/{name}"], capture_output=True, check=True)
    return json.loads(out.stdout) | {"_file": name}


def amp(chain):
    return next(b for b in chain if b.get("role") == "amp")


def set_out(rig, spec):
    """out_db as bank3-levels.csv writes it: '1', or 'L/R' for a dual rig, on each chain's amp."""
    vals = [float(v) for v in str(spec).split("/")]
    for chain, v in zip([rig["left"], rig.get("right", [])], vals):
        if v:
            amp(chain)["out_db"] = v
        else:
            amp(chain).pop("out_db", None)
    return rig


def cases():
    """(set, label, rig, live row) for every bench measurement we can rebuild."""
    out = []
    levels = list(csv.DictReader(open(EXP / "bank3-levels.csv")))
    for row in levels:
        p, pc = int(row["pass"]), int(row["pc"])
        if pc == 18:
            continue  # the A1 test file isn't in the cache
        if p <= 4:  # D17: the Satan Wall era, 5.2 s window
            rig = set_out(git_rig(D17, FILES[pc]), row["out_db"])
            out.append(("D17 levels", f"pass {p} PC {pc} {row['preset']}", rig, row))
        else:  # D18 passes 5-6
            name = "17-stormblade.json" if pc == 17 else FILES[pc]
            rig = set_out(git_rig(D18, name), row["out_db"])
            out.append(("D18 levels", f"pass {p} PC {pc} {row['preset']}", rig, row))
    for row in csv.DictReader(open(EXP / "bank3-cabs.csv")):
        if row["run"] == "cab candidates":
            src = {"Satan 50 Modern": 14, "Satan 50 Low Tuned": 15, "Satan 50 Lead": 16}[row["amp_preset"]]
            rig = set_out(git_rig(D17, FILES[src]), row["out_db"])
            cab = next(b for b in rig["left"] if b["role"] == "cab")
            if CABS[row["cab"]]:
                cab["file"] = CABS[row["cab"]]
            out.append(("D18 cabs", f"{row['amp_preset']} / {row['cab'][:28]}", rig, row))
        elif row["run"] == "stormblade":
            rig = set_out(git_rig(D18, "17-stormblade.json"), row["out_db"])
            if "single" in row["amp_preset"]:
                rig = dict(rig, left=rig["left"], right=[], split_after=None)
                rig.pop("right")
                rig.pop("split_after")
            out.append(("D18 cabs", f"Stormblade {row['amp_preset'].split()[-1]}", rig, row))
    for i, (_, label, rig, _) in enumerate(out):
        rig["name"] = f"{rig['name']} [{label}]"  # distinct names, distinct keys
    return out


def mono_bands(r):
    """D18's bands from ours: 40-250 = 40-100 + 100-250 (live), compare as energy shares."""
    return r


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--di", default=os.environ.get("RIGSIM_DI", rs.DI_DEFAULT))
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--csv")
    args = ap.parse_args()
    rs.set_di(args.di)
    cs = cases()
    keys, results, rendered = rs.simulate([c[2] for c in cs], exact=True, jobs=args.jobs)
    lines, deltas = [], {}
    for (group, label, rig, live), k in zip(cs, keys):
        r = results[k]
        if "error" in r:
            lines.append((group, label, "ERROR " + r["error"]))
            continue
        d = r["lufs"] - float(live["lufs"])
        deltas.setdefault(group, []).append(d)
        extra = ""
        if "b40_100" in live:
            low = 10 * math.log10(10 ** (float(live["b40_100"]) / 10) + 10 ** (float(live["b100_250"]) / 10))
            extra = (f"  low {r['low']:6.1f} vs {low:6.1f}  250-800 {r['lowmid']:6.1f} vs {float(live['b250_800']):6.1f}"
                     f"  5-10k {r['air']:6.1f} vs {float(live['b5000_10000']):6.1f}"
                     f"  centroid {r['centroid_hz']:5d} vs {int(live['centroid_hz']):5d}")
        lines.append((group, label, f"sim {r['lufs']:6.1f}  live {float(live['lufs']):6.1f}  Δ {d:+5.1f}   "
                                    f"peak {r['peak_dbfs']:5.1f} vs {float(live['peak_dbfs']):5.1f}{extra}"))
    for g in dict.fromkeys(x[0] for x in lines):
        print(f"\n== {g}")
        for _, label, text in [x for x in lines if x[0] == g]:
            print(f"  {label:<58} {text}")
        d = np.array(deltas.get(g, []))
        if len(d):
            print(f"  Δ LUFS (sim − live): mean {d.mean():+.2f}, sd {d.std(ddof=1):.2f}, range {d.min():+.1f} … {d.max():+.1f}, n={len(d)}")
    print(f"\n{len(cs)} bench rows, {len(set(keys))} distinct sounds, {len(rendered)} rendered")
    if args.csv:
        with open(args.csv, "w") as fh:
            w = csv.writer(fh)
            w.writerow(["set", "case", "sim_lufs", "live_lufs", "delta", "sim_peak", "live_peak", "sim_centroid", "live_centroid"])
            for (group, label, rig, live), k in zip(cs, keys):
                r = results[k]
                if "error" not in r:
                    w.writerow([group, label, r["lufs"], live["lufs"], round(r["lufs"] - float(live["lufs"]), 2),
                                r["peak_dbfs"], live["peak_dbfs"], r["centroid_hz"], live.get("centroid_hz", "")])


if __name__ == "__main__":
    main()
