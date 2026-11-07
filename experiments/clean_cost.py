# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9.26", "numpy"]
# ///
"""What makes a clean preset expensive? Callback cost of amp pairs and of TONE3000's per-block A2
size (`slimSize`, LITE/FULL since v0.0.2), measured as experiments/callback_cost.py does (D31).

  uv run experiments/clean_cost.py [--secs 4] [--csv F]

Each variant runs in its own process, one 256-frame block per process() call. On a busy machine the
medians still rank the variants, and the maxima say little.
"""

import argparse
import copy
import csv
import json
import os
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import callback_cost as cc  # noqa: E402
import rigsim  # noqa: E402
import tone3000 as t3k  # noqa: E402


def variant(base, name, left=None, right=None, comp=True, slim=None):
    r = copy.deepcopy(base)
    r["name"] = name
    for side, amp in (("left", left), ("right", right)):
        if amp:
            b = next(x for x in r[side] if x.get("role") == "amp")
            b["tone_id"], b["model_id"] = amp
    if not comp:
        slot = next(x for x in r["left"] if x.get("role") == "boost")
        slot.clear()
        slot.update(role="boost", type="insert")
    return r, slim


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--secs", type=float, default=4.0)
    ap.add_argument("--csv")
    a = ap.parse_args()
    di, _ = rigsim.load_di(str(Path.home() / "Music/fcb-rig/di-ref.wav"))
    x = di[:int(a.secs * rigsim.RATE)].astype(np.float32)
    rigs = t3k.load_rigs()
    base = next(r for r in rigs if r["name"] == "Maiden Clean")
    other = next(r for r in rigs if r["name"] == "Rule the World")
    runs = [
        (copy.deepcopy(other), None, "Rule the World (one amp, reference)"),
        (*variant(base, "now"), "Twin71 N + B, Diamond, FULL"),
        (*variant(base, "no comp", comp=False), "Twin71 N + B, no compressor, FULL"),
        (*variant(base, "t73", (88227, 744307), (88227, 774746)), "Twin73 N + N2, Diamond, FULL"),
        (*variant(base, "jc", (127, 420121), (127, 420123)), "JC Ch1 Low + Hi, Diamond, FULL"),
        (*variant(base, "deluxe", (6289, 413038), (64764, 444968)), "Deluxe65 + DeluxeRI, Diamond, FULL"),
        (*variant(base, "lite", slim=0.0), "Twin71 N + B, Diamond, LITE"),
        (*variant(base, "half", slim=0.5), "Twin71 N + B, Diamond, slim 0.5"),
    ]
    rows = []
    for rig, slim, label in runs:
        pid = os.fork()
        if pid == 0:
            if slim is not None:
                original = t3k.block

                def sized(kind, *args, **kw):
                    node = original(kind, *args, **kw)
                    if kind == "nam":
                        node.set("slimSize", slim)
                    return node
                t3k.block = sized
                t3k.build_block = rigsim._build_block  # bypass the block cache: its key doesn't see slimSize
            plugin, _ = cc.load(rigsim.absolute_files(rig))
            cc.measure(plugin, x[:rigsim.RATE])
            row = dict(variant=label, **cc.summarise(cc.measure(plugin, x)))
            Path(f"/tmp/clean-cost-{os.getpid()}.json").write_text(json.dumps(row))
            os._exit(0)
        os.waitpid(pid, 0)
        p = Path(f"/tmp/clean-cost-{pid}.json")
        if p.exists():
            row = json.loads(p.read_text())
            p.unlink()
            rows.append(row)
            print(f"{label:40} median {row['median_ms']:.3f}  p99 {row['p99_ms']:.3f}  max {row['max_ms']:.3f} ms", flush=True)
        else:
            print(f"{label}: failed", flush=True)
    if a.csv and rows:
        with open(a.csv, "w", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


if __name__ == "__main__":
    main()
