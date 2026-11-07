# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy", "soundfile", "python-rtmidi"]
# ///
"""Live proof for the Maiden bank's clean chorus and levels (experiments D31), against a Qtractor
that `qtractor_rig.up` started. The speakers and the guitar stay unlinked throughout.

  uv run experiments/clean_live.py [--out DIR] [--csv F]

Sends SW3 and SW4's exact messages (rig.layout), then records the Rig Print bus (after the limiter):
  chorus   a steady 220 Hz sine: envelope swing in dB of the left channel. A chorus beats against
           the dry note, so the swing is large with it on and near zero with it off.
  program  the owner's DI: integrated loudness and sample peak.
"""

import argparse
import csv
import math
import shutil
import subprocess
import sys
import time
from pathlib import Path

import numpy as np
import soundfile as sf

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
import rig as fcb  # noqa: E402
import xrun_bench as xb  # noqa: E402

RATE = 48000
REPO = Path(__file__).resolve().parent.parent
SESSION = Path.home() / "Music/fcb-rig"
DI = Path.home() / "Music/fcb-rig/di-ref.wav"
SWITCHES = {"SW3 clean": 3, "SW4 acoustic": 4, "SW3 clean again": 3}


def tone(path, seconds=60.0):
    t = np.arange(int(seconds * RATE)) / RATE
    sf.write(str(path), 10 ** (-18 / 20) * np.sin(2 * np.pi * 220 * t), RATE, subtype="FLOAT")


def read_wav(path):
    y, _ = sf.read(str(path), dtype="float64", always_2d=True)
    return y


def record(out, seconds):
    """A take of the 'Rig Print' bus through the session's own recorder (pw-record's stream never
    ran on this graph). The take moves to `out`, and the DI take and peak files go away."""
    subprocess.run(["uv", "run", "--quiet", str(REPO / "qtractor_rig.py"), "record"], cwd=REPO, check=True, capture_output=True)
    time.sleep(seconds)
    subprocess.run(["uv", "run", "--quiet", str(REPO / "qtractor_rig.py"), "stop"], cwd=REPO, check=True, capture_output=True)
    time.sleep(0.5)
    take = max(SESSION.glob("rig-Rig_Print-*.wav"), key=lambda f: f.stat().st_mtime)
    shutil.move(str(take), out)
    for f in list(SESSION.glob("rig-DI-*.wav*")) + list(SESSION.glob("rig-Rig_Print-*.peak")):
        f.unlink(missing_ok=True)


def swing(y):
    x = y[int(1.0 * RATE):, 0]
    n = int(0.05 * RATE)
    env = np.array([np.sqrt((x[i:i + n] ** 2).mean()) for i in range(0, len(x) - n, n)])
    env = env[env > 0]
    db = 20 * np.log10(env)
    return float(np.percentile(db, 95) - np.percentile(db, 5))


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default="/tmp/clean-live")
    ap.add_argument("--csv")
    a = ap.parse_args()
    out = Path(a.out)
    out.mkdir(parents=True, exist_ok=True)
    if xb.owner_driving():
        sys.exit("GuitarMood is driving the rig: leaving it alone")
    if not xb.qtractor_pid():
        sys.exit("Qtractor isn't running")
    layout = {(b, s): m for b, s, m in fcb.layout()}
    port = xb.qtractor_seq_port()
    tone_wav, di_wav = out / "tone.wav", out / "di-loop.wav"
    tone(tone_wav)
    xb.looped_wav(DI, 60, di_wav)
    speakers, guitar = xb.speaker_links(), xb.guitar_links()
    rows, player = [], None
    try:
        xb.unlink(speakers)
        xb.unlink(guitar)
        for kind, wav in (("chorus", tone_wav), ("program", di_wav)):
            player = subprocess.Popen(["pw-play", "-P", "{ node.name = d31-player node.autoconnect = false }", str(wav)],
                                      stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            for _ in range(50):
                outs = [p for p in xb.sh("pw-link", "-o").split() if p.startswith("d31-player:")]
                if outs:
                    break
                time.sleep(0.1)
            for i, dst in enumerate(("Qtractor:Guitar/in_1", "Qtractor:Guitar/in_2")):
                xb.sh("pw-link", outs[min(i, len(outs) - 1)], dst)
            for name, sw in (SWITCHES.items() if kind == "chorus" else list(SWITCHES.items())[:2]):
                xb.send(port, layout[0, sw])
                time.sleep(4.0)
                if s := xb.speaker_links():
                    xb.unlink(s)
                wav = out / f"{kind}-{name.replace(' ', '-')}.wav"
                record(wav, 8.0 if kind == "chorus" else 16.0)
                y = read_wav(wav)
                row = dict(kind=kind, switch=name, samples=len(y))
                if kind == "chorus":
                    row["swing_db"] = round(swing(y), 2)
                else:
                    row["peak_dbfs"] = round(20 * math.log10(float(np.abs(y).max()) + 1e-12), 2)
                    row["rms_db"] = round(20 * math.log10(float(np.sqrt((y ** 2).mean())) + 1e-12), 2)
                rows.append(row)
                print(row, flush=True)
            player.terminate()
            player.wait()
            player = None
    finally:
        if player:
            player.terminate()
        xb.relink(guitar)
        xb.relink(speakers)
    if a.csv:
        keys = sorted({k for r in rows for k in r})
        with open(a.csv, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=keys)
            w.writeheader()
            w.writerows(rows)


if __name__ == "__main__":
    main()
