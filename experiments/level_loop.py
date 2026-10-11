# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9.26", "numpy"]
# ///
"""Level loop (experiments D29): render with rigsim --exact, trim each preset's tone.csv out_db
cell toward its D22 target, re-render, until every preset is within TOL.

  uv run experiments/level_loop.py STEM=TARGET_LUFS [...] [--csv OUT.csv]

Writes rigs/tone.csv in place (only the out_db cells of the named rows); the last pass's rigsim
numbers go to OUT.csv.
"""

import csv
import io
import subprocess
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
TONE = REPO / "rigs/tone.csv"
TOL, PASSES = 0.3, 4


def render(stems, out):
    subprocess.run(["uv", "run", str(REPO / "rigsim.py"), *stems, "--exact", "--before", "none", "--csv", out],
                   cwd=REPO, check=True, capture_output=True)
    return list(csv.DictReader(open(out)))


def main(args):
    out = "/tmp/level-loop.csv"
    if "--csv" in args:
        i = args.index("--csv")
        out = args[i + 1]
        args = args[:i] + args[i + 2:]
    targets = dict((a.split("=")[0], float(a.split("=")[1])) for a in args)
    for n in range(PASSES):
        rows = render(list(targets), out)
        by_stem = {}
        for r in rows:
            stem = next((s for s in targets if int(s[:2]) == int(r["pc"])), None)  # stems start with their PC
            if stem:
                by_stem[stem] = float(r["lufs"])
        sheet = list(csv.reader(io.StringIO(TONE.read_text())))
        col = sheet[0].index("out_db")
        moved = False
        for row in sheet:
            if row[0] in targets and row[0] in by_stem:
                delta = round(targets[row[0]] - by_stem[row[0]], 1)
                print(f"pass {n}: {row[0]:28} {by_stem[row[0]]:6.2f} LUFS (target {targets[row[0]]}), out_db {row[col] or 0} {delta:+.1f}")
                if abs(delta) > 0.1 or (abs(delta) > TOL):
                    row[col] = f"{round(float(row[col] or 0) + delta, 1):g}"
                    moved = True
        missing = set(targets) - set(by_stem)
        if missing:
            sys.exit(f"no rigsim row for {sorted(missing)}; columns: {list(rows[0]) if rows else []}")
        if not moved:
            break
        buf = io.StringIO()
        csv.writer(buf, lineterminator="\n").writerows(sheet)
        TONE.write_text(buf.getvalue())


if __name__ == "__main__":
    main(sys.argv[1:])
