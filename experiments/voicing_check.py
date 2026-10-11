# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9.26", "numpy"]
# ///
"""Does each pickup voicing move the sound the intended way, and what does it do to loudness?
(experiments D29)

  uv run experiments/voicing_check.py OUT.csv [STEM ...]

Renders the bench DI (~/Music/fcb-rig/di-ref.wav, the owner's JP70) through each preset in
rigsim's engine (--exact window) once per voicing: the preset's own pre-model EQ is removed and
VOICINGS[name] (or the pre-D29 hand curve, "legacy-single-coil") put on its voicing block,
tone.csv as it stands otherwise. Reports loudness, bands and centroid relative to "humbucker"
(no pre-EQ) on the same preset.
"""

import copy
import csv
import json
import sys
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))
import rigsim  # noqa: E402
import tone3000 as t3k  # noqa: E402

DEFAULT = ["19-rule-the-world", "08-comfortably-numb", "14-satan-50-modern", "02-maiden-acoustic"]


def variants(stem, sheet):
    raw = json.loads((t3k.RIGS / f"{stem}.json").read_text()) | {"_file": f"{stem}.json"}
    block = raw.get("voicing_block", "drive" if stem == "02-maiden-acoustic" else "amp")
    names = ["humbucker", "single-coil", "p90", "filtertron", "piezo", "legacy-single-coil"]
    out = []
    for name in names:
        rig = copy.deepcopy(raw)
        for side in ("left", "right"):
            for b in rig.get(side, []):
                if b.get("role") == block and b.get("eq_pre"):
                    b.pop("eq_pre"), b.pop("eq", None)
        rig.pop("voicing", None)
        rig["voicing_block"] = block
        if name == "legacy-single-coil":
            for side in ("left", "right"):
                for b in rig.get(side, []):
                    if b.get("role") == block and b.get("type") == "nam":
                        b["eq"], b["eq_pre"] = list(t3k.LEGACY_SINGLE_COIL), True
        else:
            rig["voicing"] = name
        rig = t3k.apply_tone(rig, sheet)
        rig["name"] = f"{rig['name']} [{name}]"
        out.append((stem, name, rig))
    return out


def main(out_csv, *stems):
    rigsim.set_di(rigsim.DI_DEFAULT)
    sheet = t3k.tone_sheet()
    jobs = [v for s in (stems or DEFAULT) for v in variants(s, sheet)]
    keys, results, _ = rigsim.simulate([r for _, _, r in jobs], exact=True)
    rows, ref = [], {}
    for (stem, name, rig), k in zip(jobs, keys):
        m = results[k]
        if "lufs" not in m:
            print("!!", rig["name"], m.get("error"))
            continue
        if name == "humbucker":
            ref[stem] = m
        h = ref[stem]
        row = dict(preset=stem, voicing=name, lufs=round(m["lufs"], 2), d_lufs=round(m["lufs"] - h["lufs"], 2),
                   **{f"d_{b}": round(m[b] - h[b], 2) for b in ("low", "lowmid", "mid", "presence", "air") if b in m},
                   centroid_hz=round(m["centroid_hz"]), d_centroid_hz=round(m["centroid_hz"] - h["centroid_hz"]))
        rows.append(row)
        print("  ".join(f"{k}={v}" for k, v in row.items()))
    with open(out_csv, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=list(rows[0]))
        w.writeheader()
        w.writerows(rows)


if __name__ == "__main__":
    main(*sys.argv[1:])
