# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Xrun bench for the clean/acoustic scenes (experiments D24): xruns per minute and per-thread CPU
of the running Qtractor rig, per preset, with the preset's delay on and off.

  uv run experiments/xrun_bench.py LABEL [--secs 30] [--wav ~/Music/fcb-rig/di-palm-b.wav]
                                   [--cases acoustic,clean,80s] [--out experiments/xrun-bench.csv]
                                   [--owner-ok] [--dry-run]

LABEL tags the CSV rows ("before", "after", ...). Run it once on the old session and once on the
new one; both append to the same CSV.

What it does, against a Qtractor that `qtractor_rig.py up` already started:
  1. Refuses if the owner's GuitarMood is driving the rig (pass --owner-ok when they said so),
     or if Qtractor is not running.
  2. Unlinks the speakers (every link from Qtractor's outputs to a playback port) and the
     Scarlett from the Guitar insert, and puts them back at the end, whatever happens.
  3. Plays the DI WAV, looped into one long real WAV, through ONE persistent pw-play node linked
     to Qtractor:Guitar/in_1+in_2 (graph churn makes its own xruns: experiments D10).
  4. Per case: sends the FCB switch's exact messages (fcb_router's layout) to Qtractor's ALSA
     port, waits SETTLE_S (a preset load has its own xruns, D15, counted separately), sets the
     delay, then measures for --secs: Qtractor's ERR delta and B/Q from one persistent
     `pw-top -b`, and every Qtractor/pipewire thread's CPU from /proc.

Cases: Maiden ACOUSTIC (SW4) and CLEAN (SW3) on the clean instance (MIDI ch 2), and the five 80s
cleans (bank 2, ch 1), each with the preset's echo block on and off (CC 24 toggles slot 5 + R3;
TONE3000 flips on any value >= 64, experiments M1, so the script toggles only when the preset's
own default differs).
"""

import argparse
import csv
import json
import os
import re
import signal
import statistics
import struct
import subprocess
import sys
import threading
import time
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO))

import fcb_router  # noqa: E402
import tone3000 as t3k  # noqa: E402

SETTLE_S = 4.0
DELAY_CC = 24
CLK_TCK = os.sysconf("SC_CLK_TCK")
QUANTUM_MS = 256 / 48000 * 1000


def sh(*cmd, check=False):
    return subprocess.run(cmd, capture_output=True, text=True, check=check).stdout


def rigs_by_pc():
    return {r["pc"]: r for r in t3k.load_rigs()}


def echo_default(rig):
    """True when the preset loads with its slot-5 echo on (None: no echo block)."""
    e = next((b for b in rig.get("left", []) if b.get("role") == "echo"), None)
    return None if e is None else bool(e.get("enabled", True))


def cases(which):
    """[(name, switch events, delay channel, pc of the measured preset)]."""
    table = fcb_router.load_layout()
    out = []
    if "acoustic" in which:
        out.append(("Maiden Acoustic", table[(0, 4)], 1))
    if "clean" in which:
        out.append(("Maiden Clean", table[(0, 3)], 1))
    if "80s" in which:
        for sw in range(1, 6):
            ev = table[(2, sw)]
            out.append((None, ev, 0))
    rigs = rigs_by_pc()
    result = []
    for name, events, ch in out:
        pc = next(e[2] for e in events if e[0] == "pc" and e[1] == ch)
        result.append((name or rigs[pc]["name"], events, ch, pc))
    return result


# ---------------------------------------------------------------- the graph
def qtractor_pid():
    out = sh("pgrep", "-x", "qtractor").split()
    return int(out[0]) if out else None


def qtractor_seq_port():
    m = re.search(r"client (\d+): 'Qtractor'", sh("aconnect", "-l"))
    return f"{m.group(1)}:0" if m else None


def links():
    """[(out port, in port)] from `pw-link -l`."""
    pairs, cur = [], None
    for line in sh("pw-link", "-l").splitlines():
        if not line.startswith(" "):
            cur = line.strip()
        elif "|->" in line and cur:
            pairs.append((cur, line.split("|->", 1)[1].strip()))
    return pairs


def unlink(pairs):
    for a, b in pairs:
        sh("pw-link", "-d", a, b)


def relink(pairs):
    for a, b in pairs:
        sh("pw-link", a, b)


def speaker_links():
    return [(a, b) for a, b in links() if a.startswith("Qtractor:") and ("playback" in b or "alsa_output" in b)]


def guitar_links():
    return [(a, b) for a, b in links() if b.startswith("Qtractor:Guitar/") and not a.startswith("xrun-bench")]


def looped_wav(src, seconds, dst):
    """src (a RIFF WAV, any sample format) repeated to >= seconds, as one real WAV file."""
    raw = Path(src).read_bytes()
    pos, fmt, data = 12, None, None
    while pos + 8 <= len(raw):
        cid, size = raw[pos:pos + 4], struct.unpack("<I", raw[pos + 4:pos + 8])[0]
        body = raw[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = body
        elif cid == b"data":
            data = body
        pos += 8 + size + (size & 1)
    byte_rate = struct.unpack("<I", fmt[8:12])[0]
    reps = max(1, int(seconds * byte_rate / len(data)) + 1)
    data = data * reps
    fmt_chunk = b"fmt " + struct.pack("<I", len(fmt)) + fmt + (b"\0" if len(fmt) & 1 else b"")
    body = b"WAVE" + fmt_chunk + b"data" + struct.pack("<I", len(data)) + data
    Path(dst).write_bytes(b"RIFF" + struct.pack("<I", len(body)) + body)
    return len(data) / byte_rate


class PwTop(threading.Thread):
    """One persistent `pw-top -b`; keeps every Qtractor* node's latest (B/Q, ERR) and a B/Q log."""

    def __init__(self):
        super().__init__(daemon=True)
        self.proc = subprocess.Popen(["pw-top", "-b"], stdout=subprocess.PIPE, text=True)
        self.err, self.bq = {}, {}
        self.lock = threading.Lock()

    def run(self):
        for line in self.proc.stdout:
            parts = line.split()
            if len(parts) < 10 or not parts[1].isdigit():
                continue
            name = parts[-1]
            if not name.startswith("Qtractor"):
                continue
            try:
                err = int(parts[8])
            except ValueError:
                continue
            with self.lock:
                self.err[name] = err
                try:
                    self.bq.setdefault(name, []).append(float(parts[7]))
                except ValueError:
                    pass

    def snapshot(self):
        with self.lock:
            return dict(self.err), {k: len(v) for k, v in self.bq.items()}

    def bq_since(self, marks):
        with self.lock:
            return {k: v[marks.get(k, 0):] for k, v in self.bq.items()}

    def stop(self):
        self.proc.terminate()


def threads(pid):
    """{tid: (comm, policy, rtprio, cpu ticks)} for every thread of pid."""
    out = {}
    for task in Path(f"/proc/{pid}/task").iterdir():
        try:
            stat = (task / "stat").read_text()
        except OSError:
            continue
        comm = stat[stat.index("(") + 1:stat.rindex(")")]
        f = stat[stat.rindex(")") + 2:].split()
        # fields after comm: state=0 ... utime=11 stime=12 ... rt_priority=37 policy=38
        out[int(task.name)] = (comm, int(f[38]), int(f[37]), int(f[11]) + int(f[12]))
    return out


POLICY = {0: "TS", 1: "FF", 2: "RR", 3: "B", 5: "IDL"}


def thread_cpu(before, after, secs):
    rows = []
    for tid, (comm, pol, prio, ticks) in after.items():
        t0 = before.get(tid, (comm, pol, prio, ticks))[3]
        pct = 100.0 * (ticks - t0) / CLK_TCK / secs
        rows.append({"tid": tid, "comm": comm, "cls": POLICY.get(pol, pol), "rtprio": prio, "pct": round(pct, 1)})
    return sorted(rows, key=lambda r: -r["pct"])


def send(port, events):
    for e in events:
        if e[0] == "pc":
            sh("aseqsend", "-p", port, f"{0xC0 | e[1]:02X}", f"{e[2]:02X}")
        else:
            sh("aseqsend", "-p", port, f"{0xB0 | e[1]:02X}", f"{e[2]:02X}", f"{e[3]:02X}")


def owner_driving():
    return [l for l in sh("pgrep", "-af", "[g]uitarmood").splitlines() if "xrun_bench" not in l]


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("label")
    ap.add_argument("--secs", type=float, default=30.0)
    ap.add_argument("--wav", default=str(Path.home() / "Music/fcb-rig/di-palm-b.wav"))
    ap.add_argument("--cases", default="acoustic,clean,80s")
    ap.add_argument("--out", default=str(REPO / "experiments/xrun-bench.csv"))
    ap.add_argument("--owner-ok", action="store_true", help="run even though GuitarMood is driving the rig")
    ap.add_argument("--dry-run", action="store_true", help="print the plan, touch nothing")
    a = ap.parse_args()

    plan = cases(set(a.cases.split(",")))
    rigs = rigs_by_pc()
    runs = []
    for name, events, ch, pc in plan:
        default = echo_default(rigs[pc])
        for delay in ([True, False] if default is not None else [None]):
            runs.append((name, events, ch, pc, default, delay))
    total = len(runs) * (a.secs + SETTLE_S + 1.5) + 5
    print(f"{len(runs)} runs x {a.secs:g} s (+{SETTLE_S:g} s settle) ~ {total / 60:.1f} min")
    for name, events, ch, pc, default, delay in runs:
        print(f"  {name:<24} PC {pc} ch{ch + 1}  delay {'on' if delay else 'off' if delay is False else '-'}"
              f"  (preset default {'on' if default else 'off'})")
    if a.dry_run:
        return
    if (gm := owner_driving()) and not a.owner_ok:
        sys.exit("GuitarMood is driving the rig (the owner is playing):\n  " + "\n  ".join(gm)
                 + "\nAsk first, then re-run with --owner-ok.")
    pid = qtractor_pid()
    port = qtractor_seq_port()
    if not pid or not port:
        sys.exit("Qtractor is not running: start the rig (`uv run qtractor_rig.py up`) first.")
    pw_pid = int(sh("pgrep", "-x", "pipewire").split()[0])

    # SIGTERM/SIGHUP (a killed task, a closed terminal) still runs the finally: the links come back.
    for sig in (signal.SIGTERM, signal.SIGHUP):
        signal.signal(sig, lambda *_: sys.exit("stopped"))
    loop = Path("/tmp/xrun-bench-di.wav")
    looped_wav(a.wav, total + 10, loop)
    speakers, guitar = speaker_links(), guitar_links()
    player = top = None
    rows = []
    try:
        unlink(speakers)
        unlink(guitar)
        player = subprocess.Popen(["pw-play", "-P", "{ node.name = xrun-bench node.autoconnect = false }", str(loop)],
                                  stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        for _ in range(50):
            outs = [p for p in sh("pw-link", "-o").split() if p.startswith("xrun-bench:")]
            if outs:
                break
            time.sleep(0.1)
        else:
            sys.exit("pw-play node did not appear")
        for i, dst in enumerate(("Qtractor:Guitar/in_1", "Qtractor:Guitar/in_2")):
            sh("pw-link", outs[min(i, len(outs) - 1)], dst)
        top = PwTop()
        top.start()
        time.sleep(2.0)
        for name, events, ch, pc, default, delay in runs:
            err0, _ = top.snapshot()
            send(port, events)
            time.sleep(SETTLE_S)
            if delay is not None and delay != default:
                send(port, [("cc", ch, DELAY_CC, 127)])
            time.sleep(1.0)
            if s := speaker_links():  # Qtractor re-patches saved connections (experiments D13)
                unlink(s)
            err1, marks = top.snapshot()
            q0, p0, t_start = threads(pid), threads(pw_pid), time.monotonic()
            time.sleep(a.secs)
            secs = time.monotonic() - t_start
            q1, p1 = threads(pid), threads(pw_pid)
            err2, _ = top.snapshot()
            bq = top.bq_since(marks)
            qt = thread_cpu(q0, q1, secs)
            pw = thread_cpu(p0, p1, secs)
            for node in sorted(err2):
                xr = err2[node] - err1.get(node, err2[node])
                vals = bq.get(node, []) or [0.0]
                row = {
                    "label": a.label, "preset": name, "pc": pc, "midi_ch": ch + 1,
                    "delay": "on" if delay else "off" if delay is False else "-", "node": node,
                    "secs": round(secs, 1), "xruns": xr, "xruns_per_min": round(xr * 60 / secs, 1),
                    "load_xruns": err1.get(node, 0) - err0.get(node, err1.get(node, 0)),
                    "bq_median": round(statistics.median(vals), 2), "bq_max": round(max(vals), 2),
                    "qtractor_total_pct": round(sum(r["pct"] for r in qt), 1),
                    "top_thread": f"{qt[0]['comm']}/{qt[0]['tid']} {qt[0]['cls']}{qt[0]['rtprio']}",
                    "top_thread_pct": qt[0]["pct"],
                    "top_thread_ms_per_quantum": round(qt[0]["pct"] / 100 * QUANTUM_MS, 2),
                    "busy_threads": sum(1 for r in qt if r["pct"] >= 5),
                    "pipewire_data_loop_pct": max((r["pct"] for r in pw if "data-loop" in r["comm"]), default=0),
                    "threads": json.dumps([[r["comm"], r["tid"], r["cls"], r["rtprio"], r["pct"]] for r in qt if r["pct"] >= 1]),
                }
                rows.append(row)
                print(f"{name:<24} delay {row['delay']:<3} {node:<14} {row['xruns_per_min']:>6}/min "
                      f"(load {row['load_xruns']})  B/Q {row['bq_median']}/{row['bq_max']}  "
                      f"top {row['top_thread']} {row['top_thread_pct']}%  all {row['qtractor_total_pct']}%  "
                      f"threads>=5% {row['busy_threads']}", flush=True)
    finally:
        if top:
            top.stop()
        if player:
            player.send_signal(signal.SIGINT)
            try:
                player.wait(3)
            except subprocess.TimeoutExpired:
                player.kill()
        unlink(speaker_links())  # whatever Qtractor re-patched, then the owner's own links
        relink(guitar)
        relink(speakers)
        loop.unlink(missing_ok=True)
    if rows:
        out = Path(a.out)
        new = not out.exists()
        with out.open("a", newline="") as f:
            w = csv.DictWriter(f, fieldnames=list(rows[0]))
            if new:
                w.writeheader()
            w.writerows(rows)
        print(f"appended {len(rows)} rows to {out}")


if __name__ == "__main__":
    main()
