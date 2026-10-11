# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Low-B summary of palm_boom.py CSVs (experiments D29): do palm-muted B1/C#2 boom against E2-A2?

  uv run experiments/palm_lowb.py BEFORE.csv [AFTER.csv ...]

Per preset and CSV, over the owner's palm-mute take (palm events with input peak over -25 dBFS):
  low_vs_mid_lu   median 400 ms loudness of the B1/C#2 palm notes minus that of E2/F#2/G#2/A2
  f0_vs_mid_db    the same for the fundamental's share of the note (f0 band minus broadband RMS):
                  how much of a low note is boom rather than chug
  low_vs_mid_db   the same for the 60-200 Hz share (boom and flub below the chug)
  in_vs_mid_db    the same for the DI's input peak (how much harder the low notes were picked)
  ring_low_db     the ringing (open) notes' 60-200 Hz energy: the body of open low chords
"""

import csv
import sys
from collections import defaultdict

import numpy as np

LOW, MID = {"B1", "C#2"}, {"E2", "F#2", "G#2", "A2"}


def summary(path):
    by = defaultdict(list)
    for r in csv.DictReader(open(path)):
        by[r["preset"]].append(r)
    out = {}
    for preset, rows in by.items():
        for r in rows:
            r["in_peak_dbfs"] = r["in_peak_dbfs"] or "-6"  # the synthetic DI (palm_boom.py di): every note at -6 dBFS
        palm = [r for r in rows if r["kind"] == "palm" and float(r["in_peak_dbfs"]) > -25]
        g = lambda notes, key: float(np.median([float(r[key]) for r in palm if r["note"] in notes]))
        share = lambda notes, key="f0_band_db": float(np.median([float(r[key]) - float(r["rms_db"]) for r in palm if r["note"] in notes]))
        ring = [float(r["low_60_200_db"]) for r in rows if r["kind"] == "ring"]
        out[preset] = dict(low_vs_mid_lu=g(LOW, "mom_lufs") - g(MID, "mom_lufs"), f0_vs_mid_db=share(LOW) - share(MID),
                           low_vs_mid_db=share(LOW, "low_60_200_db") - share(MID, "low_60_200_db"),
                           in_vs_mid_db=g(LOW, "in_peak_dbfs") - g(MID, "in_peak_dbfs"),
                           ring_low_db=float(np.median(ring)) if ring else float("nan"),
                           palm_lufs=float(np.median([float(r["mom_lufs"]) for r in palm])))
    return out


def main(*paths):
    out = None
    if "--out" in paths:  # --out FILE: also write the summary as one CSV row per preset and input CSV
        i = paths.index("--out")
        out, paths = paths[i + 1], paths[:i] + paths[i + 2:]
    sums = [summary(p) for p in paths]
    if out:
        with open(out, "w", newline="") as fh:
            w = csv.writer(fh)
            w.writerow(["run", "preset", "low_vs_mid_lu", "f0_vs_mid_db", "low_vs_mid_db", "in_vs_mid_db", "ring_low_db"])
            for p, s in zip(paths, sums):
                for preset, m in s.items():
                    w.writerow([p.split("/")[-1].removesuffix(".csv"), preset, *(round(m[k], 2) for k in
                                ("low_vs_mid_lu", "f0_vs_mid_db", "low_vs_mid_db", "in_vs_mid_db", "ring_low_db"))])
    print(f"{'preset':28}" + "".join(f" | {p.split('/')[-1][:36]:>36}" for p in paths))
    print(f"{'':28}" + " | low-mid LU  f0 dB 60-200   in  ring" * len(paths))
    for preset in sums[0]:
        cells = []
        for s in sums:
            m = s.get(preset)
            cells.append(f"{m['low_vs_mid_lu']:+10.1f} {m['f0_vs_mid_db']:+6.1f} {m['low_vs_mid_db']:+6.1f} {m['in_vs_mid_db']:+4.1f} {m['ring_low_db']:5.1f}" if m else " " * 36)
        print(f"{preset[:28]:28} | " + " | ".join(cells))


if __name__ == "__main__":
    main(*sys.argv[1:])
