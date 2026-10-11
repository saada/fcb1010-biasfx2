# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9.26", "numpy"]
# ///
"""Per-callback cost of a preset in the real TONE3000 engine (experiments D24).

  uv run experiments/callback_cost.py [SELECTOR ...] [--secs 6] [--csv OUT.csv] [--variant V ...]

Loads each preset into the native TONE3000 VST3 exactly as rigsim.py does (same state blob as
the live instance, sandbox HOME), then feeds the bench DI one 256-frame block per process()
call, like Qtractor's JACK callback, and times every call. The rig's deadline is one quantum,
256 / 48000 = 5.33 ms, and every plugin on every track shares it, because Qtractor runs them all
in one thread. A row says how much of that one preset eats: median, p99 and max per callback,
and how the worst callbacks repeat (every 32nd block = 8192 frames = JUCE's non-uniform
convolver tail partition).

Variants (several allowed): "as-is" (every block inside TONE3000, as before D25); "daw-tails"
(tone3000.DAW_TAILS: the long tails moved to the DAW's x42 zeroconvolv, so this times only what
stays in TONE3000; the convolver's own audio-thread share is its first 64-sample partitions,
the rest runs on its threads); "no-long-ir" (T3K_NO_LONG_IR: echo and ambience blocks
dropped); "tail:S" (IR_TAIL_MAX_S = S seconds); "short-ir" (every echo/ambience IR at most 1.0 s,
which TONE3000 convolves uniformly on every callback instead).

Not the rig: no other tracks, no JACK, pedalboard's per-call overhead is in every number, and
the process runs at normal priority on a busy laptop, so look at the shape and the ratios.
"""

import argparse
import csv
import os
import sys
import time
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import rigsim  # noqa: E402
import tone3000 as t3k  # noqa: E402

QUANTUM_MS = rigsim.BLOCK / rigsim.RATE * 1000


def load(rig):
    """A loaded plugin, as rigsim.render does (models prepared, load mute lifted)."""
    import re
    stages = []
    blob = rigsim.state_for(rig, rigsim.base_state(), stages)
    home = rigsim.SANDBOX / f"cost{os.getpid()}"
    (home / ".config").mkdir(parents=True, exist_ok=True)
    os.environ["HOME"], os.environ["XDG_CONFIG_HOME"] = str(home), str(home / ".config")
    import pedalboard
    plugin = pedalboard.load_plugin(str(rigsim.VST3))
    os.environ["HOME"] = rigsim.REAL_HOME
    B = rigsim.BLOCK
    plugin.process(np.zeros((2, B), np.float32), rigsim.RATE, buffer_size=B, reset=False)
    log = home / ".config/TONE3000/TONE3000.log"
    mark = log.stat().st_size if log.exists() else 0
    plugin.raw_state = rigsim.vst3_state(blob, plugin.raw_state)
    expected = sum(b.get("type") != "insert" for b in rig.get("left", []) + rig.get("right", [])) - sum(len(st["labels"]) for st in stages)
    t1, irs = time.monotonic(), []
    while time.monotonic() - t1 < 30:
        text = log.read_bytes()[mark:].decode(errors="replace") if log.exists() else ""
        done = len(re.findall(r"\] (NAM model|IR) prepared", text))
        if done >= max(text.count("queued for load"), expected):
            irs = re.findall(r"IR prepared: (\d+) ch, ([\d.]+) s \((long|short)", text)
            break
        time.sleep(0.01)
    plugin.process(np.zeros((2, rigsim.RATE), np.float32), rigsim.RATE, buffer_size=B, reset=False)
    return plugin, irs


def measure(plugin, x):
    B = rigsim.BLOCK
    n = len(x) // B
    t = np.empty(n)
    blocks = [np.ascontiguousarray(np.stack([x[i * B:(i + 1) * B]] * 2)) for i in range(n)]
    for i, blk in enumerate(blocks):
        t0 = time.perf_counter_ns()
        plugin.process(blk, rigsim.RATE, buffer_size=B, reset=False)
        t[i] = (time.perf_counter_ns() - t0) / 1e6
    return t


def summarise(t):
    med = float(np.median(t))
    spikes = np.flatnonzero(t > max(3 * med, med + 0.5))
    period = int(np.median(np.diff(spikes))) if len(spikes) > 2 else 0
    return dict(median_ms=round(med, 3), p99_ms=round(float(np.percentile(t, 99)), 3), max_ms=round(float(t.max()), 3),
                mean_ms=round(float(t.mean()), 3), spikes=len(spikes), spike_period_blocks=period,
                spike_median_ms=round(float(np.median(t[spikes])), 3) if len(spikes) else 0.0,
                max_share_of_quantum=round(float(t.max()) / QUANTUM_MS, 2))


def variant_rig(rig, v):
    rig = rigsim.absolute_files(rig)
    if v == "short-ir":
        for b in rig.get("left", []) + rig.get("right", []):
            if b.get("type") == "echo" and b.get("reverb"):
                b["reverb"]["trim_seconds"] = 0.9
            if b.get("role") == "ambience" and b.get("type") == "ir":
                b["trim_seconds"] = 0.9
    return rig


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("selectors", nargs="*", default=["Maiden Acoustic"])
    ap.add_argument("--secs", type=float, default=6.0)
    ap.add_argument("--variant", action="append", default=None)
    ap.add_argument("--csv")
    ap.add_argument("--rev", help="rigs/ at this revision (e.g. another branch's presets)")
    ap.add_argument("--di", default=str(Path.home() / "Music/fcb-rig/di-palm-b.wav"))
    a = ap.parse_args()
    di, _ = rigsim.load_di(a.di)
    x = di[:int(a.secs * rigsim.RATE)].astype(np.float32)
    pool = list(rigsim.before_rigs(a.rev)[1].values()) if a.rev else t3k.load_rigs()
    rigs = rigsim.select(a.selectors, pool)
    rows = []
    for v in a.variant or ["as-is"]:
        for rig in rigs:
            # Each variant/preset in a fresh process: the builder's env knobs are read at import.
            env = dict(os.environ)
            env["T3K_DAW_TAILS"] = "1" if v == "daw-tails" else "0"
            if v == "no-long-ir":
                env["T3K_NO_LONG_IR"] = "1"
            if v.startswith("tail:"):
                env["T3K_IR_TAIL_MAX_S"] = v.split(":", 1)[1]
            pid = os.fork()
            if pid == 0:
                os.environ.clear()
                os.environ.update(env)
                import importlib
                importlib.reload(t3k)
                rigsim.t3k = t3k
                t3k.build_block, rigsim._build_block = rigsim.cached_build_block, t3k.build_block
                plugin, irs = load(variant_rig(rig, v))
                measure(plugin, x[:rigsim.RATE])  # warm-up
                t = measure(plugin, x)
                row = dict(preset=rig["name"], rigs=a.rev or "worktree", variant=v, blocks=len(t),
                           irs=" ".join(f"{n}ch/{s}s/{k}" for n, s, k in irs), **summarise(t))
                out = Path(f"/tmp/callback-cost-{os.getpid()}.json")
                import json
                out.write_text(json.dumps(row))
                os._exit(0)
            os.waitpid(pid, 0)
            import json
            p = Path(f"/tmp/callback-cost-{pid}.json")
            if p.exists():
                row = json.loads(p.read_text())
                p.unlink()
                rows.append(row)
                print(f"{row['preset']:<24} {v:<11} median {row['median_ms']:.3f}  p99 {row['p99_ms']:.3f}  "
                      f"max {row['max_ms']:.3f} ms ({row['max_share_of_quantum']:.0%} of 5.33)  "
                      f"spikes {row['spikes']} every {row['spike_period_blocks']} blocks at {row['spike_median_ms']:.3f} ms  "
                      f"[{row['irs']}]", flush=True)
            else:
                print(f"{rig['name']}: {v}: failed", flush=True)
    if a.csv and rows:
        new = not Path(a.csv).exists()
        with open(a.csv, "a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            if new:
                w.writeheader()
            w.writerows(rows)


if __name__ == "__main__":
    main()
