# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9.26", "numpy"]
# ///
"""Audition WAVs of real presets, as the rig would play them over the owner's JP70 DI (D32).

  uv run experiments/audition.py SELECTOR ... [--prefix NAME]

SELECTOR is what rigsim.py takes ("@tag", a stem, a name). Each preset renders through rigsim
(TONE3000, then the DAW tails), then the numpy stand-in of the DAW chorus when the preset has a
`chorus` block and loads on the clean instance (its Clean Chorus is on at load; a song chorus on
the heavy instance waits for SW7, so it isn't added), then the rig bus limiter. The output goes to
~/Music/fcb-rig/audition/<prefix>-<stem>.wav at the preset's own level (no re-trim).
"""

import argparse
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import maiden_candidates as mc  # noqa: E402
import rigsim  # noqa: E402
import thd_bench as tb  # noqa: E402
import tone3000 as t3k  # noqa: E402

AUDITION = Path.home() / "Music/fcb-rig/audition"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("select", nargs="+")
    ap.add_argument("--prefix", default="aud")
    ap.add_argument("--work", default="/tmp/audition")
    a = ap.parse_args()
    work = Path(a.work)
    work.mkdir(parents=True, exist_ok=True)
    rigs = rigsim.rigs_from([json.loads(f.read_text()) | {"_file": f.name} for f in sorted(t3k.RIGS.glob("*.json"))],
                            (t3k.RIGS / "tone.csv").read_text())
    chosen = rigsim.select(a.select, rigs)
    rigsim.set_di(str(Path.home() / "Music/fcb-rig/di-ref.wav"))
    keys, res, _ = rigsim.simulate(chosen, exact=True, use_cache=False, wav=str(work), jobs=6)
    AUDITION.mkdir(parents=True, exist_ok=True)
    for r, k in zip(chosen, keys):
        y = tb.read_float_wav(work / f"{r['name'].replace('/', '_')}-{k[:8]}.wav")  # rigsim's limited render
        if r.get("chorus") and (r.get("instance") == "clean" or r.get("scene") in ("clean", "acoustic")):
            y = rigsim.limiter(mc.apply_chorus(y, {"rate_hz": 0.6, "depth_ms": 4.0, "mix": 0.5} | r["chorus"]))
        out = AUDITION / f"{a.prefix}-{Path(r['_file']).stem}.wav"
        mc.write_wav(out, y)
        print(out)


if __name__ == "__main__":
    main()
