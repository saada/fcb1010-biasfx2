# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9.26", "numpy"]
# ///
"""Wet ratio (experiments D30): how much always-on reverb each preset plays, measured the same
way for every preset.

  uv run experiments/verb_ratio.py [STEM ...] [--sheet tone.csv] [--csv OUT.csv] [--jobs N]
                                   [--wav DIR] [--no-live]

Each preset renders through rigsim's engine (the real TONE3000, then the DAW tail stages) over
~/Music/fcb-rig/di-ref.wav plus TAIL_S of silence, with no limiter and with the spread/align
wobble switched off for the measurement only (the wobble is random per run and can't null):
  dry    every always-on reverb bypassed: ambience IRs off, the `reverb` of an echo that is on
         at load removed (a reverb-only echo, delay_ms 0, switched off). Footswitched echoes and
         the delays themselves stay as they are.
  wet    as loaded (what tone3000.build + qtractor_rig.build play once D30's kernel fix is in).
  live   as loaded, with the DAW tail stages as the live rig played them before D30: the
         zeroconvolv kernel at unit energy and Wet 0 dB, so the wet was not scaled by the mix.

wet ratio (dB) = 10 log10(E(wet - dry) / E(dry)), energies summed over L and R, whole render.
rt60_s: Schroeder decay of the wet render after the DI stops (it ends mid-note: an interrupted
signal), T20 (-5 to -25 dB) times 3. tail30_s: from the DI's end until the wet render's 50 ms
level is 30 dB under the program's mean level (how long the room is heard, level included).
Nothing in rigs/ is written; --sheet renders a what-if tone.csv.
"""

import argparse
import json
import math
import multiprocessing as mp
import os
import sys
from pathlib import Path

import numpy as np

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import rigsim  # noqa: E402
import tone3000 as t3k  # noqa: E402

TAIL_S = 3.0
RATE = rigsim.RATE
TARGET = "32-back-in-black"  # the house wet ratio (the owner: "more like that Back in Black reverb level")


def dry_variant(rig):
    """The preset with every always-on reverb bypassed (see the module doc)."""
    rig = json.loads(json.dumps(rig))
    for spec in rig.get("left", []) + rig.get("right", []):
        if spec.get("type") == "ir" and spec.get("role") == "ambience":
            spec["enabled"] = False
        elif spec.get("type") == "echo" and spec.get("enabled", True) and spec.get("reverb"):
            if spec.get("delay_ms"):
                del spec["reverb"]
                spec["_had_reverb"] = True
            else:
                spec["enabled"] = False
    return rig


def verb_echoes(rig):
    """Echo blocks on at load with a delay and a summed reverb, per lane: [(lane, spec)]."""
    return [(side, s) for side in ("left", "right") for s in rig.get(side, [])
            if s.get("type") == "echo" and s.get("enabled", True) and s.get("reverb") and s.get("delay_ms")]


def thru_variant(rig):
    """dry_variant with those echo blocks off too: the signal the echo block receives."""
    rig = dry_variant(rig)
    for spec in rig.get("left", []) + rig.get("right", []):
        if spec.get("type") == "echo" and spec.get("enabled", True) and spec.get("delay_ms") and spec.get("_had_reverb"):
            spec["enabled"] = False
    return rig


def echo_k(spec):
    """How much louder the echo repeats play in the as-loaded block than in the echo-only block
    of dry_variant: TONE3000 normalises each IR file to unit energy, and in the as-loaded one the
    summed reverb carries most of the energy (tone3000._echo_scale)."""
    rev = spec["reverb"]
    key = (int(spec["delay_ms"]), float(spec.get("feedback", 0.5)), float(spec.get("cutoff_hz", 3500.0)),
           json.dumps({k: v for k, v in rev.items() if k != "level_db"}, sort_keys=True))
    e = [v / 0.9 for v in t3k.wav_read(t3k.analog_echo(*key[:2], cutoff_hz=key[2]))[0][0]]
    alone = min(1.0, 1.0 / (0.9 * math.sqrt(sum(v * v for v in e))))
    return t3k._echo_scale(*key, float(rev.get("level_db", -6.0))) / alone


def no_wobble(rig):
    rig = json.loads(json.dumps(rig))
    rig["params"] = {**rig.get("params", {}), "spreadWobbleEnabled": 0.0, "alignWobbleEnabled": 0.0}
    return rig


def has_verb(rig):
    return any((s.get("type") == "ir" and s.get("role") == "ambience" and s.get("enabled", True))
               or (s.get("type") == "echo" and s.get("enabled", True) and s.get("reverb"))
               for s in rig.get("left", []) + rig.get("right", []))


_LIVE = False


def daw_tails(y, stages):
    """rigsim.daw_tails, or (live) the pre-D30 rig: dry 1 - mix, the unit-energy kernel at 0 dB."""
    if not _LIVE:
        return _daw_tails(y, stages)
    for st in stages:
        m = st["mix"]
        for ch in range(y.shape[1]):
            h = np.asarray(st["irs"][ch], np.float64)
            n = len(y) + len(h) - 1
            nfft = 1 << (n - 1).bit_length()
            wet = np.fft.irfft(np.fft.rfft(y[:, ch], nfft) * np.fft.rfft(h, nfft), nfft)[:len(y)]
            y[:, ch] = (1 - m) * y[:, ch] + wet
    return y


_daw_tails = rigsim.daw_tails
rigsim.daw_tails = daw_tails
rigsim.limiter = lambda y: y  # energies need the linear render: the limiter would not null


def job_render(job):
    global _LIVE
    _LIVE = job["live"]
    try:
        return rigsim.render(job)
    except Exception as e:
        return job["id"], dict(error=repr(e))


def read_f32_wav(path):
    raw = Path(path).read_bytes()
    return np.frombuffer(raw[44:], "<f4").reshape(-1, 2).astype(np.float64)


def tail_stats(y, n_di):
    p = (y ** 2).sum(1)
    prog = p[:n_di].mean()
    tail = p[n_di:]
    sch = np.cumsum(tail[::-1])[::-1]
    sch_db = 10 * np.log10(sch / sch[0] + 1e-30)
    t5, t25 = np.argmax(sch_db <= -5), np.argmax(sch_db <= -25)
    rt60 = 3 * (t25 - t5) / RATE if t25 > t5 else float("nan")
    w = RATE // 20
    lv = 10 * np.log10(tail[:len(tail) // w * w].reshape(-1, w).mean(1) / prog + 1e-30)
    below = np.nonzero(lv < -30)[0]
    return round(rt60, 2), round((below[0] if len(below) else len(lv)) * w / RATE, 2)


def main(argv=None):
    global _DI_LEN
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("select", nargs="*")
    ap.add_argument("--sheet")
    ap.add_argument("--csv")
    ap.add_argument("--jobs", type=int, default=max(1, (os.cpu_count() or 2) - 2))
    ap.add_argument("--wav", help="keep the renders here (default: a temp folder)")
    ap.add_argument("--no-live", action="store_true", help="skip the pre-D30 live variant")
    ap.add_argument("--di", default=str(rigsim.DI_DEFAULT))
    args = ap.parse_args(argv)
    jsons = [json.loads(f.read_text()) | {"_file": f.name} for f in sorted(t3k.RIGS.glob("*.json"))]
    sheet = Path(args.sheet).read_text() if args.sheet else (t3k.TONE_CSV.read_text() if t3k.TONE_CSV.exists() else None)
    rigs = [r for r in rigsim.rigs_from(jsons, sheet) if r["_file"].endswith(".json")]
    if args.select:
        rigs = [r for r in rigs if any(s in (Path(r["_file"]).stem, r["name"], str(r["pc"])) or s in r["_file"] for s in args.select)]
    di, _ = rigsim.load_di(args.di)
    n_di = len(di)
    rigsim._DI = np.concatenate([di, np.zeros(int(TAIL_S * RATE), np.float32)])
    base = rigsim.base_state()
    out = Path(args.wav or f"/tmp/verb-ratio-{os.getpid()}")
    out.mkdir(parents=True, exist_ok=True)
    work, plan = [], {}
    for r in rigs:
        stem = Path(r["_file"]).stem
        r = rigsim.absolute_files(no_wobble(r))
        variants = {"wet": (r, False), "dry": (dry_variant(r), False)}
        if verb_echoes(r):
            variants["thru"] = (thru_variant(r), False)
        if not args.no_live:
            variants["live"] = (r, True)
        plan[stem] = r
        for v, (rig, live) in variants.items():
            if v != "wet" and not has_verb(r):
                continue  # no always-on reverb: wet = dry
            work.append(dict(id=f"{stem}:{v}", rig=rig, base=base, mode="exact", load_timeout=30.0, live=live,
                             wav=str(out / f"{stem}-{v}.wav")))
    rigsim.SANDBOX.mkdir(parents=True, exist_ok=True)
    with mp.get_context("fork").Pool(min(args.jobs, len(work)), initializer=rigsim._worker_init) as pool:
        for k, res in pool.imap_unordered(job_render, work):
            if "error" in res or not res.get("loaded", True):
                print(f"{k}: {res.get('error') or res.get('load_errors') or 'models never landed'}", file=sys.stderr)
    rows = []
    for stem, r in plan.items():
        wet = read_f32_wav(out / f"{stem}-wet.wav")
        row = dict(preset=stem, name=r["name"], pc=r["pc"])
        if not has_verb(r):
            row.update(wet_ratio_db="", live_ratio_db="", rt60_s="", tail30_s="")
            rows.append(row)
            continue
        dry = read_f32_wav(out / f"{stem}-dry.wav")
        if echoes := verb_echoes(r):
            # dry_variant's echo-only block plays its repeats at 1/k of the as-loaded level. The
            # chain after it is linear, so per output channel (its lane's block when the lanes are
            # split): echo-only = (1-m) thru + m (h * thru), as loaded = (1-m) thru + m k (h * thru).
            thru = read_f32_wav(out / f"{stem}-thru.wav")
            lane = {side: s for side, s in echoes}
            for ch, side in enumerate(("left", "right")):
                s = lane.get(side) if r.get("split_after") else lane.get("left")
                s = s or echoes[0][1]
                m, k = float(s.get("mix", 1.0)), echo_k(s)
                dry[:, ch] = (1 - m) * thru[:, ch] + k * (dry[:, ch] - (1 - m) * thru[:, ch])
                row[f"echo_k_{side[0]}_db"] = round(20 * math.log10(k), 2)
        e_dry = (dry ** 2).sum()
        row["wet_ratio_db"] = round(10 * math.log10(((wet - dry) ** 2).sum() / e_dry), 2)
        row["rt60_s"], row["tail30_s"] = tail_stats(wet, n_di)
        if not args.no_live:
            live = read_f32_wav(out / f"{stem}-live.wav")
            row["live_ratio_db"] = round(10 * math.log10(((live - dry) ** 2).sum() / e_dry), 2)
            row["live_tail30_s"] = tail_stats(live, n_di)[1]
        rows.append(row)
    target = next((x["wet_ratio_db"] for x in rows if x["preset"] == TARGET), None)
    cols = ["pc", "preset", "wet_ratio_db", "vs_target_db", "rt60_s", "tail30_s", "echo_k_l_db", "echo_k_r_db"] + ([] if args.no_live else ["live_ratio_db", "live_tail30_s"]) + ["target"]
    for x in rows:
        x["vs_target_db"] = round(x["wet_ratio_db"] - target, 2) if target is not None and x["wet_ratio_db"] != "" else ""
        x["target"] = "TARGET" if x["preset"] == TARGET else ""
    print("  ".join(f"{c:>14}" for c in cols))
    for x in sorted(rows, key=lambda x: x["pc"]):
        print("  ".join(f"{str(x.get(c, '')):>14}" for c in cols))
    if args.csv:
        with open(args.csv, "w") as fh:
            fh.write(",".join(cols) + "\n")
            for x in sorted(rows, key=lambda x: x["pc"]):
                fh.write(",".join(str(x.get(c, "")) for c in cols) + "\n")
    if not args.wav:
        for f in out.glob("*.wav"):
            f.unlink()
        out.rmdir()


if __name__ == "__main__":
    main()
