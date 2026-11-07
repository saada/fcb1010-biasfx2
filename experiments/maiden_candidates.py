# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9.26", "numpy"]
# ///
"""Render full stereo candidates for the Maiden bank's clean and acoustic (experiments D31).

  uv run experiments/maiden_candidates.py [--out DIR]

Writes one WAV per candidate at the D22 target of -15 LUFS and prints distortion and tone.
TONE3000 runs for real (rigsim). The DAW chorus isn't in the simulator, so a numpy stand-in of
qtractor_rig.CHORUS is applied for the audition: two delay voices around 7 ms on a triangle LFO,
channels 180 degrees apart, equal dry and wet at mix 0.5.
"""

import argparse
import copy
import json
import struct
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
TARGET = -15.0
STRAT = list(t3k.VOICINGS["single-coil"])
STRAT_NECK = [-1.0, -1.0, -1.0, 0.0, 2.0, 2.0]  # inferred: rounder than the bridge-leaning single-coil curve (Infinite Dreams: neck pickup)
PIEZO = [-6.0, -4.0, -1.0, 3.0, 6.0, 6.0]
PLATE = dict(tone_id=84558, model_id=719211)
WOOD = dict(tone_id=88464, model_id=745823)
DIAMOND = dict(tone_id=88975, model_id=749582)


def nam(role, tone, model, in_db=0.0, eq=None, label=""):
    b = dict(role=role, type="nam", tone_id=tone, model_id=model, enabled=True, in_db=float(in_db), label=label or role)
    if eq:
        b.update(eq=list(eq), eq_pre=True)
    return b


def ir(tone, model, eq=None, label="body"):
    b = dict(role="cab", type="ir", tone_id=tone, model_id=model, enabled=True, label=label)
    if eq:
        b["eq"] = list(eq)
    return b


def echo(ms, fb, mix, cutoff, verb, trim, level, label="echo"):
    return dict(role="echo", type="echo", delay_ms=ms, feedback=fb, mix=mix, cutoff_hz=cutoff, enabled=True, label=label,
                reverb=dict(verb, trim_seconds=trim, level_db=level))


def ins(role):
    return dict(role=role, type="insert")


def rig_from(base, name, boost, left_amp, left_cab, right_amp, right_cab, wet, wobble):
    r = copy.deepcopy(base)
    r.update(name=name, split_after=2)
    r["left"] = [boost, ins("drive"), left_amp or ins("amp"), left_cab, copy.deepcopy(wet)]
    r["right"] = [right_amp or ins("amp"), right_cab, copy.deepcopy(wet)]
    r["params"] = dict(chainPanLeft=0.0, chainPanRight=1.0, chainPanLinked=1.0, alignEnabled=1.0,
                       alignWobbleEnabled=1.0, alignWobble=wobble, spreadEnabled=0.0)
    r.pop("voicing", None)
    r.pop("voicing_block", None)
    return r


def candidates(base):
    delay = echo(380, 0.18, 0.12, 4500.0, PLATE, 1.6, -8.0, "380 ms repeat + plate")
    room = echo(0, 0.0, 0.08, 8000.0, WOOD, 1.0, 0.0, "small wood room")
    room["delay_ms"] = 0
    chorus = dict(rate_hz=0.6, depth_ms=4.0, mix=0.5)
    tilt = [-6.0, -5.0, -2.0, 2.0, 10.0, 10.0]
    return [
        ("clean-A-strat-twin", rig_from(base, "clean A", nam("boost", **{"tone": DIAMOND["tone_id"], "model": DIAMOND["model_id"]}),
            nam("amp", 6106, 418996, -12, STRAT), ir(33532, 156871),
            nam("amp", 6106, 418994, -12, STRAT), ir(33532, 156870), delay, 0.2), chorus),
        ("clean-B-strat-deluxe", rig_from(base, "clean B", nam("boost", **{"tone": DIAMOND["tone_id"], "model": DIAMOND["model_id"]}),
            nam("amp", 6289, 413038, -10, STRAT), ir(27465, 220917),
            nam("amp", 64764, 444968, -10, STRAT), ir(27465, 220918), delay, 0.2), chorus),
        ("clean-C-marshall-rolled-back", rig_from(base, "clean C", nam("boost", **{"tone": DIAMOND["tone_id"], "model": DIAMOND["model_id"]}),
            nam("amp", 1894, 427818, -10, STRAT_NECK), ir(69659, 563625),
            nam("amp", 1894, 427819, -10, STRAT_NECK), ir(69659, 563624), delay, 0.2), chorus),
        ("acoustic-A-taylor-collings", rig_from(base, "acoustic A", nam("boost", **{"tone": DIAMOND["tone_id"], "model": DIAMOND["model_id"]}),
            None, ir(36381, 174634, tilt), None, ir(36381, 174631, tilt), room, 0.15), None),
        ("acoustic-B-taylor816", rig_from(base, "acoustic B", nam("boost", **{"tone": DIAMOND["tone_id"], "model": DIAMOND["model_id"]}),
            None, ir(43190, 218645, tilt), None, ir(43190, 218646, tilt), room, 0.15), None),
        ("acoustic-C-acoustasonic", rig_from(base, "acoustic C", nam("boost", **{"tone": DIAMOND["tone_id"], "model": DIAMOND["model_id"]}),
            None, ir(31184, 140910, tilt), None, ir(31184, 140912, tilt), room, 0.15), None),
    ]


def tri(phase):
    return 2 * np.abs(2 * (phase % 1.0) - 1) - 1


def chorus_voice(x, center_ms, depth_ms, phase):
    n = np.arange(len(x))
    d = (center_ms + depth_ms * 0.5 * (tri(phase) + 1)) * RATE / 1000
    pos = np.clip(n - d, 0, len(x) - 1)
    i = pos.astype(int)
    f = pos - i
    return x[i] * (1 - f) + x[np.minimum(i + 1, len(x) - 1)] * f


def apply_chorus(y, c):
    t = np.arange(len(y)) / RATE
    out = np.zeros_like(y)
    for ch, offset in ((0, 0.0), (1, 0.5)):
        lfo = c["rate_hz"] * t + offset
        wet = 0.5 * (chorus_voice(y[:, ch], 7.0, c["depth_ms"], lfo) + chorus_voice(y[:, ch], 7.0, c["depth_ms"], lfo + 0.25))
        out[:, ch] = (1 - c["mix"]) * y[:, ch] + c["mix"] * wet
    return out


def write_wav(path, y):
    raw = y.astype("<f4").tobytes()
    path.write_bytes(struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(raw), b"WAVE", b"fmt ", 16, 3, 2, RATE,
                                 RATE * 8, 8, 32, b"data", len(raw)) + raw)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=str(Path.home() / "Music/fcb-rig/audition"))
    ap.add_argument("--work", default="/tmp/maiden-candidates")
    a = ap.parse_args()
    out, work = Path(a.out), Path(a.work)
    out.mkdir(parents=True, exist_ok=True)
    work.mkdir(parents=True, exist_ok=True)
    rigs = rigsim.rigs_from([json.loads(f.read_text()) | {"_file": f.name} for f in sorted(t3k.RIGS.glob("*.json"))],
                            (t3k.RIGS / "tone.csv").read_text())
    base = next(r for r in rigs if r["name"] == "Maiden Clean")
    cands = candidates(base)
    rl = [c[1] for c in cands]

    tones = work / "tones.wav"
    tb.write_tones(tones)
    rigsim.set_di(str(tones))
    (work / "thd").mkdir(exist_ok=True)
    keys, res, _ = rigsim.simulate(rl, exact=True, use_cache=False, wav=str(work / "thd"), jobs=6)
    thd = {}
    for r, k in zip(rl, keys):
        if "error" in res[k]:
            sys.exit(f"{r['name']}: {res[k]['error']}")
        thd[r["name"]] = tb.thd_table(tb.read_float_wav(work / "thd" / f"{r['name']}-{k[:8]}.wav"))

    rigsim.set_di(str(DI))
    (work / "di").mkdir(exist_ok=True)
    keys, res, _ = rigsim.simulate(rl, exact=True, use_cache=False, wav=str(work / "di"), jobs=6)
    a_, b_ = int(4.0 * RATE), int(14.5 * RATE)
    print(f"{'candidate':32}{'THD110 -12/-6':>16}{'THD330 -12/-6':>16}{'gain':>7}{'peak':>7}{'low':>7}{'mid':>7}{'2-5k':>7}{'cent':>7}{'crest':>7}")
    for (fname, r, chorus), k in zip(cands, keys):
        y = tb.read_float_wav(work / "di" / f"{r['name']}-{k[:8]}.wav")
        if chorus:
            y = apply_chorus(y, chorus)
        before = rigsim.measure(y[a_:b_])
        side = (before["left_lufs"] - before["right_lufs"]) / 2  # the final preset balances with each cab's out_db
        y = y * np.array([10 ** (-side / 20), 10 ** (side / 20)])
        gain = TARGET - rigsim.measure(y[a_:b_])["lufs"]
        y = y * 10 ** (gain / 20)
        gr = 20 * np.log10(np.maximum(rigsim.limiter_gain(y), 1e-9))
        over = float((gr[a_:b_] < -0.5).mean() * 100)
        worst = float(gr[a_:b_].min())
        y = rigsim.limiter(y)
        m = rigsim.measure(y[a_:b_])
        print(f"   limiter: {over:.2f}% of time over 0.5 dB, max {worst:.1f} dB")
        write_wav(out / f"d31-{fname}.wav", y)
        t = thd[r["name"]]
        print(f"{fname:32}{t[110.0, -12][1]:8.1f}{t[110.0, -6][1]:8.1f}{t[330.0, -12][1]:8.1f}{t[330.0, -6][1]:8.1f}"
              f"{gain:7.1f}{m['peak_dbfs']:7.1f}{m.get('low', 0):7.1f}{m.get('lowmid', m.get('mid', 0)):7.1f}"
              f"{m.get('presence', 0):7.1f}{m['centroid_hz']:7d}{m['crest_db']:7.1f}")
        print("   bands:", {k2: v for k2, v in m.items() if k2 not in ("lufs", "peak_dbfs", "left_lufs", "right_lufs", "crest_db", "centroid_hz")},
              "L/R", m["left_lufs"], m["right_lufs"])


if __name__ == "__main__":
    main()
