# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9.26", "numpy"]
# ///
"""Where does a generated echo's first repeat land in TONE3000? (experiments D26)

JUCE's Trim::yes drops an IR's leading samples under -80 dBFS. A delay-only echo IR starts
with one delay time of zeros, so TONE3000 would play its first repeat on the dry note.

For every echo block in rigs/, rendered offline in the real TONE3000 VST3 (rigsim):
  first_repeat_ms  one lane alone, the echo at mix 1 (wet only), every other echo block off,
                   DAW tails off (so the IR goes through TONE3000's own loader), a single
                   synthetic pluck DI: the peak of the kernel that maps the same lane's dry render
                   (echo off) onto the wet one, i.e. the first repeat. Should equal delay_ms.
  lufs_on          the real preset with the echo's footswitch on (every block of its role, real
                   mix, DAW tails as built), on the bench DI (di-ref, exact window).
  ir_sha1          the generated IR file, to show which echoes the fix leaves byte-identical.
  daw_first_ms     where tone3000.tone3000_ir (the DAW-tail kernel, same trim) starts the
                   first repeat.

Usage: uv run experiments/echo_onset.py TAG [--out DIR]       -> DIR/TAG.csv
       uv run experiments/echo_onset.py --compare BEFORE AFTER [--out DIR] [--csv F]
"""

import argparse
import copy
import csv
import hashlib
import math
import os
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO))
import rigsim  # noqa: E402
import tone3000 as t3k  # noqa: E402

RATE = 48000
PLUCK_AT = 1.0  # s


def pluck_di(seconds=15.0, f0=110.0, seed=7):
    """One muted Karplus-Strong pluck on the A string at PLUCK_AT, peak -3 dBFS, then silence."""
    rng = np.random.default_rng(seed)
    n, p = int(0.25 * RATE), int(RATE / f0)
    buf, y = rng.uniform(-1, 1, p), np.empty(n)
    for i in range(n):
        y[i] = buf[i % p]
        buf[i % p] = 0.97 * 0.5 * (buf[i % p] + buf[(i + 1) % p])
    y *= np.minimum(1.0, np.linspace(4, 0, n))  # 60 ms fade at the end: no click
    out = np.zeros(int(seconds * RATE), np.float32)
    i0 = int(PLUCK_AT * RATE)
    out[i0:i0 + n] = y / np.abs(y).max() * 10 ** (-3 / 20)
    return out


def write_wav(path, x):
    path.write_bytes(t3k.wav_write(list(map(float, x)), RATE))


def kernel(wet, dry):
    """The kernel TONE3000 applied between two renders (both LTI after the amp), by regularised
    deconvolution of the pluck; index = lag in samples, negative lags wrap to the end."""
    s = int((PLUCK_AT - 0.1) * RATE)
    w, d = wet[s:s + 3 * RATE].sum(1), dry[s:s + 3 * RATE].sum(1)
    n = 1 << (len(w) + len(d)).bit_length()
    W, D = np.fft.rfft(w, n), np.fft.rfft(d, n)
    P = np.abs(D) ** 2
    return np.fft.irfft(W * np.conj(D) / (P + 1e-3 * P.max()), n)


def repeat_lag(wet, dry):
    """Lag (samples) of the first repeat: the peak of the wet-only kernel (the first repeat has
    unit gain, feedback < 1, and a summed reverb sits >= 6 dB under it)."""
    h = kernel(wet, dry)
    n = len(h)
    lags = np.r_[np.arange(0, int(1.2 * RATE)), np.arange(n - int(0.05 * RATE), n)]
    k = lags[np.argmax(np.abs(h[lags]))]
    return k - n if k > n // 2 else k


def echo_cases(rigs):
    """(rig, side, index, block) for every echo block."""
    for r in rigs:
        for side in ("left", "right"):
            for i, b in enumerate(r.get(side, [])):
                if b.get("type") == "echo":
                    yield r, side, i, b


def lane_rig(r, side, i, enabled, name):
    """One lane as a mono rig: the target echo at mix 1 (or off), every other echo block off."""
    lane = r["left"] if side == "left" else r["left"][:r.get("split_after") or 0] + r["right"]
    j = i if side == "left" else (r.get("split_after") or 0) + i
    blocks = []
    for k, b in enumerate(copy.deepcopy(lane)):
        if b.get("type") == "echo":
            b["enabled"] = enabled and k == j
            if k == j:
                b["mix"] = 1.0
        blocks.append(b)
    out = {k: v for k, v in copy.deepcopy(r).items() if k not in ("right", "split_after")}
    out.update(left=blocks, name=name)
    return out


def preset_on(r, role, name):
    """The real preset with this echo's footswitch on: every block of that role enabled."""
    out = copy.deepcopy(r)
    for side in ("left", "right"):
        for b in out.get(side, []):
            if b.get("type") == "echo" and b.get("role") == role:
                b["enabled"] = True
    out["name"] = name
    return out


def render(rigs, wav_dir, exact=True):
    keys, results, _ = rigsim.simulate(rigs, exact=exact, wav=str(wav_dir), use_cache=False)
    ys = []
    for r, k in zip(rigs, keys):
        path = wav_dir / f"{r['name'].replace('/', '_')}-{k[:8]}.wav"
        if not path.exists():
            path = next(wav_dir.glob(f"*-{k[:8]}.wav"))
        raw = path.read_bytes()
        ys.append(np.frombuffer(raw[44:], "<f4").reshape(-1, 2).astype(np.float64))
    return ys, [results[k] for k in keys]


def measure(tag, out_dir):
    wav_dir = out_dir / f"wav-{tag}"
    wav_dir.mkdir(parents=True, exist_ok=True)
    pluck = out_dir / "pluck-di.wav"
    write_wav(pluck, pluck_di())
    rigs = t3k.load_rigs()
    cases = list(echo_cases(rigs))

    # 1. First repeat, lane alone, echo wet only, through TONE3000's own IR loader.
    rigsim.set_di(str(pluck))
    t3k.DAW_TAILS = False
    lanes = []
    for n, (r, side, i, b) in enumerate(cases):
        lanes += [lane_rig(r, side, i, True, f"c{n}-wet"), lane_rig(r, side, i, False, f"c{n}-dry")]
    lane_y, _ = render(lanes, wav_dir)

    # 2. The real preset (DAW tails as built) with the echo footswitch on, on the bench DI.
    t3k.DAW_TAILS = True
    roles = {}
    for r, side, i, b in cases:
        roles.setdefault((r["name"], b.get("role")), r)
    rigsim.set_di(str(rigsim.DI_DEFAULT))
    _, ref = render([preset_on(r, role, f"p{n}-on") for n, ((name, role), r) in enumerate(roles.items())], wav_dir)

    role_idx = {k: n for n, k in enumerate(roles)}
    rows = []
    for n, (r, side, i, b) in enumerate(cases):
        wet, dry = lane_y[2 * n], lane_y[2 * n + 1]
        lag = repeat_lag(wet, dry)
        p = role_idx[(r["name"], b.get("role"))]
        m, d = float(b.get("mix", 1.0)), int(b.get("delay_ms", 0))
        node = t3k.build_block(b)
        ir = t3k.block_ir(node)
        raw = next(c for c in node.children if c.type == "ModelCache").children[0].get("data")
        thr = 10 ** (-80 / 20)
        lead = next(j for j, v in enumerate(ir) if abs(v) >= thr)
        rep = int(d * RATE / 1000)  # where the generator puts the first repeat in the file
        daw_first = (rep - lead) / RATE * 1000 if d else 0.0
        rows.append(dict(pc=r["pc"], preset=r["name"], block=f"{side}[{i}]", role=b.get("role"),
                         delay_ms=d, reverb=int(bool(b.get("reverb"))), mix=m,
                         footswitched=int(("block{}Power" if side == "left" else "rightBlock{}Power").format(i + 1)
                                          in t3k.footswitch_targets(r)),
                         first_repeat_ms=round(lag / RATE * 1000, 2), 
                         lufs_on=ref[p]["lufs"], peak_on=ref[p]["peak_dbfs"],
                         lead_trim_ms=round(lead / RATE * 1000, 2), daw_first_ms=round(daw_first, 2),
                         ir_first_dbfs=round(20 * math.log10(abs(ir[0]) + 1e-20), 1),
                         ir_sha1=hashlib.sha1(bytes(raw)).hexdigest()[:12]))
    path = out_dir / f"{tag}.csv"
    with open(path, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {path} ({len(rows)} echo blocks)")


def compare(before, after, out_csv):
    b = list(csv.DictReader(open(before)))
    a = list(csv.DictReader(open(after)))
    rows = []
    print(f"{'preset':24} {'block':9} {'ms':>4} rev | first repeat ms  before -> after"
          f" | LUFS on before -> after (d) | IR same")
    for x, y in zip(b, a):
        assert (x["preset"], x["block"]) == (y["preset"], y["block"])
        dl = float(y["lufs_on"]) - float(x["lufs_on"])
        same = x["ir_sha1"] == y["ir_sha1"]
        print(f"{x['preset'][:24]:24} {x['block']:9} {x['delay_ms']:>4} {'Y' if x['reverb'] == '1' else '-':>3} | "
              f"{float(x['first_repeat_ms']):8.2f} -> {float(y['first_repeat_ms']):8.2f} | "
              f"{float(x['lufs_on']):6.2f} -> {float(y['lufs_on']):6.2f} ({dl:+.2f}) | {'yes' if same else 'no'}")
        rows.append(dict(pc=x["pc"], preset=x["preset"], block=x["block"], role=x["role"], delay_ms=x["delay_ms"],
                         reverb=x["reverb"], mix=x["mix"],
                         first_repeat_ms_before=x["first_repeat_ms"], first_repeat_ms_after=y["first_repeat_ms"],
                         lufs_on_before=x["lufs_on"], lufs_on_after=y["lufs_on"], lufs_delta=round(dl, 2),
                         peak_on_before=x["peak_on"], peak_on_after=y["peak_on"],
                         lead_trim_ms_before=x["lead_trim_ms"], lead_trim_ms_after=y["lead_trim_ms"],
                         daw_first_ms_before=x["daw_first_ms"], daw_first_ms_after=y["daw_first_ms"],
                         ir_first_dbfs_after=y["ir_first_dbfs"], ir_identical=int(same)))
    if out_csv:
        with open(out_csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
        print(f"wrote {out_csv}")


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("tag", nargs="?")
    ap.add_argument("--out", default=os.environ.get("TMPDIR", "/tmp") + "/echo-onset")
    ap.add_argument("--compare", nargs=2, metavar=("BEFORE", "AFTER"))
    ap.add_argument("--csv")
    args = ap.parse_args()
    out = Path(args.out)
    out.mkdir(parents=True, exist_ok=True)
    if args.compare:
        compare(out / f"{args.compare[0]}.csv", out / f"{args.compare[1]}.csv", args.csv)
    else:
        measure(args.tag, out)


if __name__ == "__main__":
    main()
