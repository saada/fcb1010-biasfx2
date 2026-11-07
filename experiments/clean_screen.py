# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9.26", "numpy"]
# ///
"""Screen catalog captures for clean and acoustic presets (experiments D31).

Each candidate is a full mono-to-stereo chain with no reverb or echo, rendered in the real
TONE3000 engine. It prints THD at guitar levels (stepped sines, experiments/thd_bench.py),
then loudness and tone from the owner's JP70 DI, so distortion rules a capture out first.

  uv run experiments/clean_screen.py clean|acoustic [--out DIR] [--csv F]
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
JC_CAB = dict(tone_id=1644, model_id=81294)
G1265 = dict(tone_id=69659, model_id=563625)

# name: (amp capture, cab IR or None for amp-cab captures, amp input dB)
CLEAN = {
    "JC Ch1 Low": (dict(tone_id=127, model_id=420121), JC_CAB, 0),
    "JC Ch1 Hi": (dict(tone_id=127, model_id=420123), JC_CAB, 0),
    "JC Ch1 Low G12-65": (dict(tone_id=127, model_id=420121), G1265, 0),
    "JC120B BrOff SM57+Royer": (dict(tone_id=10912, model_id=379735), None, 0),
    "JC120B BrOff Royer": (dict(tone_id=10912, model_id=379737), None, 0),
    "JC120B BrOn SM57+Royer": (dict(tone_id=10912, model_id=379738), None, 0),
    "JC120 C414": (dict(tone_id=6530, model_id=397610), None, 0),
    "Deluxe CLEANEST": (dict(tone_id=6289, model_id=413039), G1265, 0),
    "Deluxe CLEAN": (dict(tone_id=6289, model_id=413038), G1265, 0),
    "SuperRev V4 T4": (dict(tone_id=68651, model_id=428013), G1265, 0),
    "Marshall 6100 clean": (dict(tone_id=58653, model_id=422046), G1265, 0),
    "Marshall 6100 clean mid": (dict(tone_id=58653, model_id=422044), G1265, 0),
    "JMP 1987 pad -12": (dict(tone_id=48523, model_id=387672), G1265, -12),
    "JMP 2203 pad -12": (dict(tone_id=1061, model_id=436416), G1265, -12),
    "JMP 2203 pad -18": (dict(tone_id=1061, model_id=436416), G1265, -18),
}

# Round 2, after the owner said they love Strat cleans: Fender amps, the single-coil voicing, and
# an input pad, because a Strat pickup puts out less than the JP70's ceramics (pad size is a guess
# screened at two values; no measured Strat DI exists here).
TWIN_CAB = dict(tone_id=33532, model_id=156871)
DELUXE_CAB = dict(tone_id=27465, model_id=220917)
FENDERS = {
    "Twin71 BigClean": (dict(tone_id=6106, model_id=418996), TWIN_CAB),
    "Twin73 Normal": (dict(tone_id=88227, model_id=744307), TWIN_CAB),
    "Twin92 V5": (dict(tone_id=106387, model_id=872421), TWIN_CAB),
    "DeluxeRI Clean": (dict(tone_id=64764, model_id=444968), DELUXE_CAB),
    "Deluxe67 Normal": (dict(tone_id=70554, model_id=579773), DELUXE_CAB),
    "Deluxe65 CLEAN": (dict(tone_id=6289, model_id=413038), DELUXE_CAB),
    "Super64 Norm V3": (dict(tone_id=57914, model_id=394642), DELUXE_CAB),
}
STRAT = {f"{n} pad{p}": (amp, cab, p) for n, (amp, cab) in FENDERS.items() for p in (-6, -10)}

# name: (NAM capture in the drive slot or None, IR or None, Diamond compressor on)
TAYLOR = dict(tone_id=43190, model_id=218645)
ACOUSTIC = {
    "Taylor816 Pz1": (None, TAYLOR, False),
    "Taylor816 SB1": (None, dict(tone_id=43190, model_id=218646), False),
    "Taylor816 EXP1": (None, dict(tone_id=43190, model_id=218650), False),
    "Acoustasonic Mex1": (None, dict(tone_id=31184, model_id=140910), False),
    "Acoustasonic Mex3": (None, dict(tone_id=31184, model_id=140912), False),
    "Martin (pack)": (None, dict(tone_id=36381, model_id=174633), False),
    "Gibson J45": (None, dict(tone_id=36381, model_id=174632), False),
    "Collings D2H": (None, dict(tone_id=36381, model_id=174631), False),
    "Taylor 314": (None, dict(tone_id=36381, model_id=174634), False),
    "Martin 017": (None, dict(tone_id=89300, model_id=752589), False),
    "Fender King": (None, dict(tone_id=55141, model_id=314373), False),
    "NAM Acoustic Sim V1": (dict(tone_id=1964, model_id=419789), None, False),
    "NAM Boss AD-2": (dict(tone_id=28230, model_id=379992), None, False),
    "NAM body bone-conduction": (dict(tone_id=27688, model_id=420426), None, False),
    "NAM Al1 clean": (dict(tone_id=6714, model_id=418641), None, False),
    "Martin (pack) + Diamond": (None, dict(tone_id=36381, model_id=174633), True),
    "Gibson J45 + Diamond": (None, dict(tone_id=36381, model_id=174632), True),
}
DIAMOND = dict(tone_id=88975, model_id=749582)


def ins(role):
    return dict(role=role, type="insert")


def nam(role, c, in_db=0, label=""):
    return dict(role=role, type="nam", enabled=True, in_db=float(in_db), label=label or role, **c)


def ir(role, c, label=""):
    return dict(role=role, type="ir", enabled=True, label=label or role, **c)


def chain_rig(base, name, chain):
    r = copy.deepcopy(base)
    r["name"] = name
    r["left"] = chain
    r["split_after"] = 0
    r["right"] = []
    r.pop("voicing", None)
    r.pop("voicing_block", None)
    r["params"] = {}
    return r


def clean_rig(base, name, spec):
    amp, cab, in_db = spec
    return chain_rig(base, name, [ins("boost"), ins("drive"), nam("amp", amp, in_db), ir("cab", cab) if cab else ins("cab")])


def strat_rig(base, name, spec):
    r = clean_rig(base, name, spec)
    amp = next(b for b in r["left"] if b["role"] == "amp")
    amp["eq"], amp["eq_pre"] = list(t3k.VOICINGS["single-coil"]), True
    return r


def acoustic_rig(base, name, spec):
    capture, body, comp = spec
    return chain_rig(base, name, [nam("boost", DIAMOND) if comp else ins("boost"),
                                  nam("drive", capture) if capture else ins("drive"), ins("amp"),
                                  ir("cab", body) if body else ins("cab")])


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("which", choices=["clean", "strat", "acoustic"])
    ap.add_argument("--out", default="/tmp/clean-screen")
    ap.add_argument("--csv")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    rigs = rigsim.rigs_from([json.loads(f.read_text()) | {"_file": f.name} for f in sorted(t3k.RIGS.glob("*.json"))],
                            (t3k.RIGS / "tone.csv").read_text())
    base = next(r for r in rigs if r["name"] == "Maiden Clean")
    make, table = {"clean": (clean_rig, CLEAN), "strat": (strat_rig, STRAT), "acoustic": (acoustic_rig, ACOUSTIC)}[a.which]
    cands = [make(base, n, s) for n, s in table.items()]

    tones = out / "tones.wav"
    tb.write_tones(tones)
    rigsim.set_di(str(tones))
    tdir = out / "thd"
    tdir.mkdir(exist_ok=True)
    keys, res, _ = rigsim.simulate(cands, exact=True, use_cache=False, wav=str(tdir), jobs=6)
    for r, k in zip(cands, keys):
        if "error" in res[k]:
            print(f"skipped {r['name']}: {res[k]['error']}"[:300])
    cands = [r for r, k in zip(cands, keys) if "error" not in res[k]]
    keys = [k for k in keys if "error" not in res[k]]
    thd = {r["name"]: tb.thd_table(tb.read_float_wav(tdir / f"{r['name']}-{k[:8]}.wav")) for r, k in zip(cands, keys)}

    rigsim.set_di(str(DI))
    ddir = out / "di"
    ddir.mkdir(exist_ok=True)
    keys, res, _ = rigsim.simulate(cands, exact=True, use_cache=False, wav=str(ddir), jobs=6)

    rows = []
    for r, k in zip(cands, keys):
        m, t = res[k], thd[r["name"]]
        rows.append(dict(name=r["name"],
                         thd110_m18=round(t[110.0, -18][1], 1), thd110_m12=round(t[110.0, -12][1], 1), thd110_m6=round(t[110.0, -6][1], 1),
                         thd330_m18=round(t[330.0, -18][1], 1), thd330_m12=round(t[330.0, -12][1], 1), thd330_m6=round(t[330.0, -6][1], 1),
                         lufs=m.get("lufs"), low=m.get("low_db", m.get("low")), lowmid=m.get("lowmid_db", m.get("mid")),
                         presence=m.get("presence_db", m.get("presence")), centroid=m.get("centroid_hz", m.get("centroid")),
                         crest=m.get("crest_db", m.get("crest")), err=m.get("error", "")))
    rows.sort(key=lambda x: max(x["thd330_m12"], x["thd110_m12"]))
    if a.csv:
        with open(a.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)
    print(f"{'candidate':28} {'110Hz THD -18/-12/-6':>22} {'330Hz THD -18/-12/-6':>22} {'LUFS':>6} {'low':>6} {'2-5k':>6} {'cent':>6} {'crest':>6}")
    for x in rows:
        print(f"{x['name'][:27]:28} {x['thd110_m18']:7}{x['thd110_m12']:7}{x['thd110_m6']:7}   {x['thd330_m18']:7}{x['thd330_m12']:7}{x['thd330_m6']:7}"
              f" {x['lufs']!s:>6} {x['low']!s:>6} {x['presence']!s:>6} {x['centroid']!s:>6} {x['crest']!s:>6} {x['err']}")


if __name__ == "__main__":
    main()
