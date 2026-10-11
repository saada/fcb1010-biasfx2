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
        palm = [r for r in rows if r["kind"] == "palm" and float(r["in_peak_dbfs"]) > -25]
        g = lambda notes, key: float(np.median([float(r[key]) for r in palm if r["note"] in notes]))
        share = lambda notes: float(np.median([float(r["f0_band_db"]) - float(r["rms_db"]) for r in palm if r["note"] in notes]))
        ring = [float(r["low_60_200_db"]) for r in rows if r["kind"] == "ring"]
        out[preset] = dict(low_vs_mid_lu=g(LOW, "mom_lufs") - g(MID, "mom_lufs"), f0_vs_mid_db=share(LOW) - share(MID),
                           in_vs_mid_db=g(LOW, "in_peak_dbfs") - g(MID, "in_peak_dbfs"),
                           ring_low_db=float(np.median(ring)) if ring else float("nan"),
                           palm_lufs=float(np.median([float(r["mom_lufs"]) for r in palm])))
    return out


def main(*paths):
    sums = [summary(p) for p in paths]
    print(f"{'preset':28}" + "".join(f" | {p.split('/')[-1][:22]:>22}" for p in paths))
    print(f"{'':28}" + " | low-mid LU  f0 dB  ring" * len(paths))
    for preset in sums[0]:
        cells = []
        for s in sums:
            m = s.get(preset)
            cells.append(f"{m['low_vs_mid_lu']:+9.1f} {m['f0_vs_mid_db']:+6.1f} {m['ring_low_db']:5.1f}" if m else " " * 22)
        print(f"{preset[:28]:28} | " + " | ".join(cells))


if __name__ == "__main__":
    main(*sys.argv[1:])
