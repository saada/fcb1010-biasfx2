# /// script
# requires-python = ">=3.10"
# dependencies = ["pedalboard>=0.9.26", "numpy"]
# ///
"""Offline rig simulator: render a DI through rigs/*.json presets in the real TONE3000
engine, faster than real time, with no audio device, no Qtractor and no live config.

Usage:
  uv run rigsim.py [SELECTOR ...] [--exact] [--before auto|undo|REV|worktree|last|none]
                   [--sheet tone.csv] [--jobs N] [--csv F] [--wav DIR] [--di DI.wav] [--volume]

  SELECTOR: what `tone3000.py tune` takes ("*", "@tag", a preset's stem, name or PC; through
            tone3000.affected, which adds a heavy preset's generated harmony), or "all", a
            rigs/*.json path, a scene kind ("clean", "cleans") or part of a name. Default: all.
  --exact   the full 15 s DI measured over 4.0-14.5 s, as the DI bench of experiments D18
            (default: a 2.5 s clip of the same DI after 0.75 s of pre-roll, for quick A/Bs).
  --before  what each preset is compared with: "auto" (default) = tone.csv before the last
            `tone3000.py tune` if there is one to undo, else HEAD; "undo"; a git REV; "worktree"
            (rigs/ as on disk, for --sheet what-ifs); "last" (this preset's previous run); "none".
  --sheet   render with another tone.csv (a what-if; nothing in rigs/ is written).
  --volume  also the pre-limiter peak, and how hard the EXP B Volume stage at 0 / +3 / +6 dB
            drives the limiter (experiments D22).

Each preset is built in memory exactly as `tone3000.py build` builds it and loaded into the
native TONE3000 VST3 (Spotify's pedalboard as the host) through the plugin state blob
`qtractor_rig.py` gives the live instance: the standalone's saved parameters, the preset's
chain and params, the heavy scene's input drive. The plugin runs in a sandbox HOME, so
~/.config/TONE3000 is only read, never written. One process per preset, idle priority.

After the plugin comes the rig bus as qtractor_rig.py builds it: compressor bypassed, then a
-1 dBFS limiter (a sample-peak lookahead stand-in for x42 dpl at -1 dBTP). The DI goes to both
plugin inputs, like Qtractor's Guitar insert. Loudness is BS.1770 integrated (gated), bands are
energy shares relative to 40 Hz-10 kHz, crest = sample peak over RMS.

Caches (~/.cache/tone3000-rig/sim): built blocks keyed by spec + file stat (echo IRs and
resampled IRs are built once), the standalone's base state, and results keyed by the
preset's content, so an unchanged "before" costs nothing.

Not simulated: the wah, octaver, Solo slap-delay and harmony tracks (all off at rest), the
bypassed compressor, true-peak limiting, the Scarlett's analog stage (the DI is a recording
of it), Program Change timing, and CC moves after the preset loads.
"""

import argparse
import hashlib
import html
import json
import math
import multiprocessing as mp
import os
import re
import shutil
import struct
import subprocess
import sys
import time
import uuid
from pathlib import Path

import numpy as np

REPO = Path(os.environ.get("FCB_REPO", Path(__file__).resolve().parent))
sys.path.insert(0, str(REPO))
import tone3000 as t3k  # noqa: E402  (reads rigs/, the model cache and the saved state; writes nothing)

SIM_VERSION = 4  # bump when rendering or measuring changes, to invalidate cached results
REAL_HOME = os.environ["HOME"]
VST3 = Path.home() / ".vst3/TONE3000.vst3"
CACHE = Path(os.environ.get("RIGSIM_CACHE", t3k.CACHE / "sim"))
SANDBOX = CACHE / "home"
DI_DEFAULT = Path.home() / "Music/fcb-rig/di-ref.wav"  # the bench's dry DI loop (experiments D17/D18)
RATE = 48000
BLOCK = 256  # the rig's quantum
EXACT = dict(start=0.0, window=(4.0, 14.5))  # the whole DI, measured as experiments D18
CLIP = dict(start=float(os.environ.get("RIGSIM_CLIP_START", 4.75)), preroll=0.75, length=float(os.environ.get("RIGSIM_CLIP_LEN", 2.5)))
EDGES = {"low": (40, 250), "lowmid": (250, 800), "mid": (800, 2000), "presence": (2000, 5000), "air": (5000, 10000)}
LIMIT_DB, LIMIT_RELEASE_S, LIMIT_LOOKAHEAD_S = -1.0, 0.01, 0.0015


# --- DI ------------------------------------------------------------------------------

def read_wav_mono(path):
    chans, rate = t3k.wav_read(Path(path).read_bytes())
    assert rate == RATE, f"{path}: {rate} Hz, need {RATE}"
    return np.asarray(chans[0], dtype=np.float32)


def synth_di(seconds=15.0, seed=1):
    """Reproducible riff-like DI when no recorded one exists: palm-muted low-E chugs and
    power chords as Karplus-Strong plucks, peaks at -1.4 dBFS. Levels won't match the bench."""
    rng = np.random.default_rng(seed)
    out = np.zeros(int(seconds * RATE))
    beat = 60 / 140 / 2
    pattern = [(82.4, 1), (82.4, 1), (82.4, 1), (110.0, 0), (82.4, 1), (82.4, 1), (123.5, 0), (82.4, 1)]
    t, k = 0.0, 0
    while t < seconds - 1:
        f0, muted = pattern[k % len(pattern)]
        for f in ([f0] if muted else [f0, f0 * 1.5, f0 * 2]):
            n, p = int(RATE * (0.15 if muted else 1.2)), int(RATE / f)
            buf, y = rng.uniform(-1, 1, p), np.empty(n)
            for i in range(n):
                y[i] = buf[i % p]
                buf[i % p] = (0.94 if muted else 0.996) * 0.5 * (buf[i % p] + buf[(i + 1) % p])
            i0 = int(t * RATE)
            out[i0:i0 + n] += (y * (0.6 if muted else 0.35))[:len(out) - i0]
        t += beat
        k += 1
    return (out / np.abs(out).max() * 10 ** (-1.4 / 20)).astype(np.float32)


def load_di(path):
    if path and Path(path).exists():
        return read_wav_mono(path), str(path)
    print(f"(no DI at {path}: using a synthetic riff, so levels won't match the DI bench)", file=sys.stderr)
    cached = CACHE / "synth-di-v1.f32"
    if not cached.exists():
        _atomic(cached, synth_di().tobytes())
    return np.frombuffer(cached.read_bytes(), np.float32).copy(), "synthetic riff (seed 1)"


# --- caches -----------------------------------------------------------------------------

def _digest(*parts):
    return hashlib.sha1(json.dumps(parts, sort_keys=True, default=str).encode()).hexdigest()[:20]


def _atomic(path, data):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    tmp.write_bytes(data)
    tmp.replace(path)


def _stat(path):
    st = Path(path).stat()
    return [str(path), st.st_mtime_ns, st.st_size]


def base_state():
    """The standalone's saved plugin state (parameters such as input calibration). Only the
    filterState key is read; the tokens elsewhere in that file are never touched."""
    key = CACHE / f"base-{_digest(_stat(t3k.SETTINGS))}.t3kb"
    if key.exists():
        return key.read_bytes()
    m = re.search(r'name="filterState" val="([^"]*)"', t3k.SETTINGS.read_text())
    if not m:
        sys.exit(f"No saved TONE3000 state in {t3k.SETTINGS}: open and close the standalone once.")
    data = t3k.juce_b64decode(html.unescape(m.group(1)))
    _atomic(key, data)
    return data


_build_block = t3k.build_block


def cached_build_block(spec):
    """tone3000.build_block, memoised on disk: generated echo IRs and resampled IRs are pure
    Python and take seconds. Each use gets a fresh block id, as a real build does."""
    files = [_stat(t3k.local_path(spec))] if spec.get("file") else []
    env = [t3k.IR_TAIL_MAX_S, os.environ.get("T3K_NO_LONG_IR")]
    path = CACHE / "blocks" / f"{_digest(spec, files, env)}.bin"
    if path.exists():
        node, _ = t3k.read_tree(path.read_bytes(), 0)
    else:
        node = _build_block(spec)
        _atomic(path, t3k.write_tree(node))
    return node.set("id", uuid.uuid4().hex)


t3k.build_block = cached_build_block


def effective_params(rig):
    """The faceplate params build_preset writes (tone3000.preset_params when it exists)."""
    if hasattr(t3k, "preset_params"):
        return t3k.preset_params(rig)
    values = {**t3k.BASE_PARAMS, "gateEnabled": 0.0, **rig.get("params", {}), **t3k.GLOBAL_PARAMS}
    if rig.get("scene") == "heavy":
        values["inputLevel"] = t3k.SCENES["rhythm"][0] / 127
    return values


def rig_key(rig, mode, di_hash, base):
    """Everything build_preset reads, so one sound has one key however its rig was written
    (JSON alone, or JSON + tone.csv)."""
    blocks = rig.get("left", []) + rig.get("right", [])
    norm = lambda chain: [{k: (float(v) if isinstance(v, (int, float)) and not isinstance(v, bool) else v)
                           for k, v in b.items() if k != "label"} for b in chain]
    files = [_stat(t3k.local_path(b)) for b in blocks if b.get("file")]
    return _digest(SIM_VERSION, norm(rig.get("left", [])), norm(rig.get("right", [])),
                   rig.get("split_after"), {k: float(v) for k, v in effective_params(rig).items()}, files, mode,
                   di_hash, hashlib.sha1(base).hexdigest(), _stat(VST3 / "Contents/x86_64-linux/TONE3000.so"))


# --- plugin state (what qtractor_rig.tone3000_state gives the live instance) ---------------

def state_for(rig, base):
    state = t3k.load_t3kb(base)
    preset = t3k.build_preset(rig)
    state.children = [preset.child("ChainSnapshot") if c.type == "ChainSnapshot" else c for c in state.children]
    values = {q.get("id"): q.get("value") for q in preset.child("Params").children}
    params = state.child("PARAMETERS")
    for q in params.children:
        if q.get("id") in values:
            q.set("value", float(values.pop(q.get("id"))))
    params.children += [t3k.Node("PARAM").set("id", k).set("value", float(v)) for k, v in values.items()]
    for q in params.children:
        if q.get("id") in t3k.GLOBAL_PARAMS:
            q.set("value", float(t3k.GLOBAL_PARAMS[q.get("id")]))
    state.set("activePresetId", f"user:{t3k.preset_id(rig['name'])}").set("activePresetName", rig["name"])
    return t3k.dump_t3kb(state)


_B64 = np.frombuffer(t3k.B64.encode(), np.uint8)


def juce_b64(b):
    """tone3000.juce_b64encode, vectorised (presets carry megabytes of models)."""
    bits = np.unpackbits(np.frombuffer(b, np.uint8), bitorder="little")
    bits = np.concatenate([bits, np.zeros(-len(bits) % 6, np.uint8)]).reshape(-1, 6)
    return f"{len(b)}." + _B64[bits @ (1 << np.arange(6))].tobytes().decode()


def vst3_state(t3kb, template):
    """pedalboard's VST3 raw_state: 'VC2!' + size + <VST3PluginState><IComponent>, where the
    component state is the plugin's T3KB blob plus JUCE's private-data trailer."""
    xml = template[8:].decode()
    m = re.search(r"<IComponent>([^<]*)</IComponent>", xml)
    comp = t3k.juce_b64decode(m.group(1))
    _, end = t3k.read_tree(comp, 4)
    body = (xml[:m.start(1)] + juce_b64(t3kb + comp[end:]) + xml[m.end(1):]).encode()
    return b"VC2!" + struct.pack("<I", len(body)) + body


# --- rig bus and measures ------------------------------------------------------------------

def limiter_gain(y):
    """The gain (per sample, <= 1) of a lookahead brickwall at LIMIT_DB, linked stereo, exponential release."""
    thr = 10 ** (LIMIT_DB / 20)
    pk = np.abs(y).max(1)
    if pk.max() <= thr:
        return np.ones(len(y))
    la = int(LIMIT_LOOKAHEAD_S * RATE)
    need = np.minimum(1.0, thr / np.maximum(pk, 1e-12))
    g = np.lib.stride_tricks.sliding_window_view(np.concatenate([need, np.ones(la)]), la + 1).min(1)
    a, cur, out = math.exp(-1 / (LIMIT_RELEASE_S * RATE)), 1.0, np.empty_like(g)
    for i, gi in enumerate(g.tolist()):
        cur = gi if gi < cur else gi + (cur - gi) * a
        out[i] = cur
    return out


def limiter(y):
    return y * limiter_gain(y)[:, None]


# The Rig bus's Volume stage (EXP B) sits before the limiter: how hard would each DAW level push
# it? (experiments D22: 0 = the old fixed level, +3 = the new nominal, +6 = full toe.)
VOLUME_CHECK_DB = (0.0, 3.0, 6.0)


def volume_headroom(y):
    """{dB: (% of time the limiter reduces gain by > 0.5 dB, max reduction dB)} with the Volume
    stage at each VOLUME_CHECK_DB, on a pre-limiter render."""
    out = {}
    for db in VOLUME_CHECK_DB:
        gr = -20 * np.log10(limiter_gain(y * 10 ** (db / 20)))
        out[f"{db:+.0f}"] = (round(float((gr > 0.5).mean() * 100), 2), round(max(0.0, float(gr.max())), 2))
    return out


# BS.1770 K-weighting at 48 kHz (pre-filter shelf, then the RLB high-pass), applied through
# its exact frequency response with enough zero padding for the IIR tails to decay.
_K = [([1.53512485958697, -2.69169618940638, 1.19839281085285], [1.0, -1.69065929318241, 0.73248077421585]),
      ([1.0, -2.0, 1.0], [1.0, -1.99004745483398, 0.99007225036621])]


def k_weight(y):
    n = 1 << int(math.ceil(math.log2(len(y) + RATE)))
    z = np.exp(-1j * np.pi * np.arange(n // 2 + 1) / (n // 2))
    H = np.ones_like(z)
    for b, a in _K:
        H *= np.polyval(b[::-1], z) / np.polyval(a[::-1], z)
    return np.fft.irfft(np.fft.rfft(y, n, axis=0) * H[:, None], n, axis=0)[:len(y)]


def lufs(power):
    """Integrated loudness from K-weighted squared samples summed over channels (BS.1770-4)."""
    blk, hop = int(0.4 * RATE), int(0.1 * RATE)
    c = np.concatenate([[0.0], np.cumsum(power)])
    starts = np.arange(0, len(power) - blk + 1, hop)
    z = (c[starts + blk] - c[starts]) / blk
    if not len(z):
        return float("-inf")
    loud = -0.691 + 10 * np.log10(z + 1e-20)
    z = z[loud > -70]
    if not len(z):
        return float("-inf")
    z = z[-0.691 + 10 * np.log10(z + 1e-20) > -0.691 + 10 * np.log10(z.mean()) - 10]
    return float(-0.691 + 10 * np.log10(z.mean()))


def measure(y):
    kw = k_weight(y) ** 2
    # Power spectrum summed over L and R (not of their mono sum, where a stereo rig's wobbling
    # spread/align offset comb-filters the bands differently on every run).
    n = 1 << int(math.ceil(math.log2(len(y))))
    X = (np.abs(np.fft.rfft(y * np.hanning(len(y))[:, None], n, axis=0)) ** 2).sum(1)
    f = np.fft.rfftfreq(n, 1 / RATE)
    sel = (f >= 40) & (f < 10000)
    tot = X[sel].sum() + 1e-30
    peak = float(np.abs(y).max())
    rms = float(np.sqrt((y ** 2).mean()))
    return dict(lufs=round(lufs(kw.sum(1)), 2), peak_dbfs=round(20 * math.log10(peak + 1e-20), 2),
                left_lufs=round(lufs(kw[:, 0]), 2), right_lufs=round(lufs(kw[:, 1]), 2),
                crest_db=round(20 * math.log10((peak + 1e-20) / (rms + 1e-20)), 2),
                centroid_hz=round(float((X * f)[sel].sum() / tot)),
                **{k: round(10 * math.log10(X[(f >= lo) & (f < hi)].sum() / tot + 1e-30), 2) for k, (lo, hi) in EDGES.items()})


# --- one preset per process -------------------------------------------------------------------

_DI = None  # set in the parent before the pool forks


def render(job):
    t0 = time.monotonic()
    os.environ["HOME"] = REAL_HOME  # the build reads the model cache; the plugin gets the sandbox
    os.environ.pop("XDG_CONFIG_HOME", None)
    blob = state_for(job["rig"], job["base"])
    t_build = time.monotonic() - t0
    # One sandbox HOME per worker, so each process reads its own TONE3000.log (~/.config/TONE3000).
    home = SANDBOX / f"w{os.getpid()}"
    os.environ["HOME"], os.environ["XDG_CONFIG_HOME"] = str(home), str(home / ".config")
    import pedalboard
    plugin = pedalboard.load_plugin(str(VST3))
    mode = job["mode"]
    if mode == "exact":
        x, (a, b) = _DI, (int(s * RATE) for s in EXACT["window"])
    else:
        s0 = int((CLIP["start"] - CLIP["preroll"]) * RATE)
        x, a = _DI[s0:int((CLIP["start"] + CLIP["length"]) * RATE)], int(CLIP["preroll"] * RATE)
        b = len(x)
    x = np.stack([x, x])  # Qtractor's Guitar insert feeds the DI to both inputs
    plugin.process(np.zeros((2, BLOCK), np.float32), RATE, buffer_size=BLOCK, reset=False)  # prepareToPlay
    log = home / ".config/TONE3000/TONE3000.log"
    mark = log.stat().st_size if log.exists() else 0
    plugin.raw_state = vst3_state(blob, plugin.raw_state)
    # Models load on the plugin's own threads. Wait until the plugin's log shows every queued
    # block prepared (or failed), then until the chain's load mute lifts. Output alone isn't
    # proof: when no load lands for 2 s (a busy machine) the mute lifts with blocks still dry.
    i = int(np.argmax(np.abs(_DI))) // BLOCK * BLOCK
    probe = np.stack([_DI[i:i + 4 * BLOCK]] * 2)
    expected = sum(b.get("type") != "insert" for b in job["rig"].get("left", []) + job["rig"].get("right", []))
    t1, loaded, failed = time.monotonic(), False, []
    while time.monotonic() - t1 < job["load_timeout"]:
        text = log.read_bytes()[mark:].decode(errors="replace") if log.exists() else ""
        queued = max(text.count("queued for load"), expected)
        done = len(re.findall(r"\] (NAM model|IR) prepared", text))
        failed = re.findall(r"\[(?:ModelLoader|Background)\] (?:Failed|Error|Load failed|Load dropped|Local model file missing)[^\n]*", text)
        if done + len(failed) >= queued:
            time.sleep(0.02)  # the loader applies each prepared engine right after logging it
            if np.abs(plugin.process(probe, RATE, buffer_size=BLOCK, reset=False)).max() > 1e-4:
                loaded = not failed
                break
        time.sleep(0.005)
    if mode == "exact":  # let the glide-in and gate settle before the DI starts
        plugin.process(np.zeros((2, RATE // 2), np.float32), RATE, buffer_size=BLOCK, reset=False)
    t_load = time.monotonic() - t1
    t2 = time.monotonic()
    y = plugin.process(x, RATE, buffer_size=BLOCK, reset=False).T.astype(np.float64)
    t_render = time.monotonic() - t2
    del plugin
    pre = dict(pre_peak_dbfs=round(20 * math.log10(float(np.abs(y[a:b]).max()) + 1e-20), 2),
               volume=volume_headroom(y[a:b]))
    y = limiter(y)
    res = dict(**measure(y[a:b]), **pre, loaded=loaded, load_errors=failed[:3], t_build=round(t_build, 2), t_load=round(t_load, 2),
               t_render=round(t_render, 2), t_total=round(time.monotonic() - t0, 2))
    if job.get("wav"):
        raw = y.astype("<f4").tobytes()
        hdr = struct.pack("<4sI4s4sIHHIIHH4sI", b"RIFF", 36 + len(raw), b"WAVE", b"fmt ", 16, 3, 2, RATE, RATE * 8, 8, 32,
                          b"data", len(raw))
        _atomic(Path(job["wav"]), hdr + raw)
    return job["id"], res


def _worker_init():
    os.nice(19)  # the owner's live rig (realtime threads) always wins the CPU
    (SANDBOX / ".config").mkdir(parents=True, exist_ok=True)
    fd = os.open(os.devnull, os.O_WRONLY)  # the plugin echoes its log to stderr
    os.dup2(fd, 2)


# --- selecting presets and their "before" ---------------------------------------------------------

def absolute_files(rig):
    rig = json.loads(json.dumps(rig))
    for spec in rig.get("left", []) + rig.get("right", []):
        if spec.get("file"):
            spec["file"] = str(t3k.local_path(spec))
    return rig


def select(selectors, rigs):
    """Presets by selector: tone3000.affected() ("*", "@tag", stem, name or PC) when this
    tone3000.py has it, else a rigs/*.json path, a PC, a scene kind or part of a name."""
    out = []
    for s in selectors or ["*"]:
        s = "*" if s == "all" else s
        names = []
        if hasattr(t3k, "affected"):
            try:
                names = t3k.affected(s)
            except Exception:
                names = []
        if names:
            hits = [r for r in rigs if r["name"] in names]
        elif s == "*":
            hits = rigs
        else:
            p, kind = Path(s), s.lower().lstrip("@")
            kind = kind if kind in t3k.SCENE_KINDS else kind.rstrip("s")
            hits = ([r for r in rigs if r.get("_file") == p.name] if p.suffix == ".json" else
                    [r for r in rigs if r["pc"] == int(s)] if s.isdigit() else
                    [r for r in rigs if r.get("scene") == kind or s.lower() in r["name"].lower()])
        if not hits:
            sys.exit(f"no preset matches {s!r}")
        out += [r for r in hits if r not in out]
    return sorted(out, key=lambda r: r["pc"])


def _git(*args, data=None):
    return subprocess.run(["git", "-C", str(REPO), *args], input=data, capture_output=True, check=True).stdout


def rigs_from(jsons, sheet_text):
    """load_rigs() over the given rig JSONs and tone.csv text (None = no sheet)."""
    rigs = sorted(jsons, key=lambda r: r["pc"])
    if hasattr(t3k, "apply_tone"):
        sheet = t3k.empty_sheet()
        if sheet_text is not None:
            path = CACHE / f"sheet-{os.getpid()}.csv"
            _atomic(path, sheet_text.encode())
            sheet = t3k.tone_sheet(path)
            path.unlink()
            if sheet["errors"]:
                raise ValueError("; ".join(sheet["errors"]))
        rigs = [t3k.apply_tone(r, sheet) for r in rigs]
    heavy = [r for r in rigs if r.get("scene") == "heavy"]
    nxt = max(r["pc"] for r in rigs) + 1
    return rigs + [t3k.harmony_rig(r, nxt + k) for k, r in enumerate(sorted(heavy, key=lambda r: r["bank"]))]


def before_rigs(spec):
    """(label, {name: rig}) for --before: "undo" = rigs/tone.csv before the last `tune`
    (rigs/.tune-history.json) over today's JSON, a git REV = rigs/ at that revision, "auto" =
    undo when there is a tune to undo, else HEAD."""
    hist_path = getattr(t3k, "TUNE_HISTORY", None)
    history = json.loads(hist_path.read_text()) if hist_path and hist_path.exists() else []
    if spec == "auto":
        spec = "undo" if history and t3k.TONE_CSV.exists() and history[-1]["csv"] != t3k.TONE_CSV.read_text() else "HEAD"
    if spec == "worktree":
        return "rigs/ as on disk", {r["name"]: r for r in t3k.load_rigs()}
    if spec == "undo":
        if not history:
            sys.exit("--before undo: no tune history")
        jsons = [json.loads(f.read_text()) | {"_file": f.name} for f in sorted(t3k.RIGS.glob("*.json"))]
        last = history[-1]
        return f"before `tune {last['target']} {last['word']} x{last['times']}`", \
            {r["name"]: r for r in rigs_from(jsons, last["csv"])}
    try:
        names = [n for n in _git("ls-tree", "--name-only", spec, "rigs/").decode().split()]
        wanted = [n for n in names if n.endswith(".json")] + [n for n in names if n == "rigs/tone.csv"]
        out = _git("cat-file", "--batch", data="".join(f"{spec}:{n}\n" for n in wanted).encode())
    except (subprocess.CalledProcessError, FileNotFoundError):
        print(f"(no git revision {spec}: no before)", file=sys.stderr)
        return None, {}
    blobs, i = {}, 0
    for n in wanted:
        end = out.index(b"\n", i)
        size = int(out[i:end].split()[2])
        blobs[n], i = out[end + 1:end + 1 + size], end + 2 + size
    jsons = [json.loads(v) | {"_file": Path(n).name} for n, v in blobs.items() if n.endswith(".json")]
    sheet = blobs.get("rigs/tone.csv")
    return f"rigs/ at {spec}", {r["name"]: r for r in rigs_from(jsons, sheet and sheet.decode())}


# --- output -----------------------------------------------------------------------------------

COLS = [("LUFS", "lufs", "{:.1f}"), ("low<250", "low", "{:.1f}"), ("250-800", "lowmid", "{:.1f}"),
        ("2-5k", "presence", "{:.1f}"), ("centroid", "centroid_hz", "{:.0f}"), ("crest", "crest_db", "{:.1f}")]


def cell(before, after, key, fmt):
    a = fmt.format(after[key])
    if before is None:
        return a
    d = after[key] - before[key]
    if abs(d) < (5 if key == "centroid_hz" else 0.05):
        return f"{a} (=)"
    return f"{fmt.format(before[key])}→{a} ({d:+.0f})" if key == "centroid_hz" else f"{fmt.format(before[key])}→{a} ({d:+.1f})"


def table(rows):
    head = ["PC", "preset"] + [c[0] for c in COLS] + ["peak", "L/R"]
    lines = []
    for r in rows:
        a, b = r["after"], r.get("before")
        if "error" in a:
            lines.append([str(r["pc"]), r["name"], "ERROR " + a["error"]])
            continue
        warn = "" if a.get("loaded", True) else " (!) " + " ".join(a.get("load_errors") or ["models never landed"])
        lines.append([str(r["pc"]), r["name"][:28] + warn] + [cell(b, a, k, f) for _, k, f in COLS]
                     + [f"{a['peak_dbfs']:.1f}", f"{a['left_lufs']:.1f}/{a['right_lufs']:.1f}"])
    w = [max(len(x[i]) for x in [head] + lines if i < len(x)) for i in range(len(head))]
    fmt = lambda x: "  ".join(c.ljust(w[i]) if i == 1 else c.rjust(w[i]) for i, c in enumerate(x))
    return "\n".join([fmt(head), "-" * (sum(w) + 2 * len(w))] + [fmt(x) if len(x) == len(head) else "  ".join(x) for x in lines])


def volume_table(rows):
    """Per preset: the pre-limiter peak, then for each Volume stage level the % of time the
    limiter holds more than 0.5 dB and its deepest reduction."""
    levels = [f"{db:+.0f}" for db in VOLUME_CHECK_DB]
    lines = ["\nEXP B Volume stage vs the -1 dB limiter (pre-limiter peak; per level: % of time > 0.5 dB GR / max GR dB)",
             f"{'PC':>3}  {'preset':<30} {'peak':>6}  " + "  ".join(f"{lv + ' dB':>14}" for lv in levels)]
    for r in rows:
        a = r["after"]
        if "volume" not in a:
            continue
        cells = "  ".join(f"{a['volume'][lv][0]:6.2f}% / {a['volume'][lv][1]:4.1f}" for lv in levels)
        lines.append(f"{r['pc']:>3}  {r['name'][:30]:<30} {a['pre_peak_dbfs']:6.1f}  {cells}")
    return "\n".join(lines)


# --- main ---------------------------------------------------------------------------------------

def set_di(path):
    global _DI
    _DI, name = load_di(path)
    return name


def simulate(rigs, exact=False, jobs=None, use_cache=True, wav=None, load_timeout=20.0, base=None):
    """Render rigs (dicts as load_rigs() gives them) -> (key per rig, {key: result}, keys rendered).
    Identical sounds share a key and a render; cached results are reused unless use_cache=False.
    set_di() first."""
    base = base or base_state()
    mode = "exact" if exact else "clip"
    mode_key = [mode, EXACT if exact else CLIP, BLOCK, LIMIT_DB, LIMIT_RELEASE_S]
    di_hash = hashlib.sha1(_DI.tobytes()).hexdigest()
    keys, todo, results = [], {}, {}
    for r in rigs:
        r = absolute_files(r)
        k = rig_key(r, mode_key, di_hash, base)
        keys.append(k)
        todo.setdefault(k, r)
    for k in list(todo):
        p = CACHE / "results" / f"{k}.json"
        if p.exists() and use_cache and not wav:
            results[k] = json.loads(p.read_text())
            del todo[k]
    if todo:
        work = [dict(id=k, rig=rig, base=base, mode=mode, load_timeout=load_timeout,
                     wav=wav and str(Path(wav) / f"{rig['name'].replace('/', '_')}-{k[:8]}.wav"))
                for k, rig in todo.items()]
        SANDBOX.mkdir(parents=True, exist_ok=True)
        ctx = mp.get_context("fork")  # workers inherit the imports and the DI; no plugin is loaded before the fork
        with ctx.Pool(min(jobs or os.cpu_count(), len(work)), initializer=_worker_init) as pool:
            for k, res in pool.imap_unordered(_safe_render, sorted(work, key=lambda j: -len(j["rig"].get("right", [])))):
                results[k] = res
                if "error" not in res:
                    _atomic(CACHE / "results" / f"{k}.json", json.dumps(res).encode())
        if not os.environ.get("RIGSIM_KEEP_LOGS"):
            for d in SANDBOX.glob("w[0-9]*"):
                shutil.rmtree(d, ignore_errors=True)
    return keys, results, list(todo)


def main(argv=None):
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("select", nargs="*")
    ap.add_argument("--exact", action="store_true")
    ap.add_argument("--before", default="auto")
    ap.add_argument("--di", default=os.environ.get("RIGSIM_DI", DI_DEFAULT))
    ap.add_argument("--jobs", type=int, default=os.cpu_count())
    ap.add_argument("--csv")
    ap.add_argument("--wav", help="write each rendered preset (the whole render, limiter on) to this folder")
    ap.add_argument("--load-timeout", type=float, default=20.0)
    ap.add_argument("--sheet", help="render with this tone.csv instead of rigs/tone.csv (what-if; nothing is written)")
    ap.add_argument("--no-cache", action="store_true", help="re-render even when a result is cached")
    ap.add_argument("--volume", action="store_true",
                    help="also print the pre-limiter peak and how hard each EXP B level drives the limiter (D22)")
    args = ap.parse_args(argv)
    t0 = time.monotonic()
    di_name = set_di(args.di)
    mode = "exact" if args.exact else "clip"
    base = base_state()
    try:
        if args.sheet:  # a what-if tone sheet: today's JSON with this CSV instead of rigs/tone.csv
            rigs = rigs_from([json.loads(f.read_text()) | {"_file": f.name} for f in sorted(t3k.RIGS.glob("*.json"))],
                             Path(args.sheet).read_text())
        else:
            rigs = t3k.load_rigs()
    except Exception as e:  # tone3000.ToneError: a bad tone.csv cell, named by line and column
        sys.exit(f"tone3000.load_rigs: {e}")
    chosen = select(args.select, rigs)
    for r in chosen:
        if errs := t3k.validate(r):
            sys.exit(f"{r['_file']}: " + "; ".join(errs))
    before_label, before = (None, {}) if args.before in ("none", "last") else before_rigs(args.before)
    last_path = CACHE / f"last-{mode}.json"
    last = json.loads(last_path.read_text()) if last_path.exists() else {}

    # Everything to render: each chosen preset, plus its "before" (same content = same key = one render).
    rows, want = [], []
    for r in chosen:
        row = dict(pc=r["pc"], name=r["name"], i=len(want))
        want.append(r)
        b = before.get(r["name"])
        if b and all(t3k.local_path(s).is_file() for s in b.get("left", []) + b.get("right", []) if s.get("file")):
            row["i_before"] = len(want)
            want.append(before[r["name"]])
        rows.append(row)
    t_prep = time.monotonic() - t0
    keys, results, todo = simulate(want, exact=args.exact, jobs=args.jobs, use_cache=not args.no_cache,
                                   wav=args.wav, load_timeout=args.load_timeout, base=base)
    for row in rows:
        row["key"] = keys[row["i"]]
        if "i_before" in row:
            row["key_before"] = keys[row["i_before"]]
    for row in rows:
        row["after"] = results[row["key"]]
        if args.before == "last":
            row["before"] = last.get(row["name"])
        elif "key_before" in row:
            row["before"] = results.get(row["key_before"])
    for row in rows:
        if "error" not in row["after"]:
            last[row["name"]] = row["after"]
    _atomic(last_path, json.dumps(last).encode())

    what = (f"{EXACT['window'][0]}-{EXACT['window'][1]} s of the whole DI" if args.exact else
            f"{CLIP['length']} s clip at {CLIP['start']} s (+{CLIP['preroll']} s pre-roll)")
    vs = {"none": "", "last": ", before = last run"}.get(args.before, f", before = {before_label}")
    print(f"DI: {di_name}, {what}; limiter {LIMIT_DB} dBFS; bands in dB of 40 Hz-10 kHz energy{vs}")
    print(table(rows))
    if args.volume:
        print(volume_table(rows))
    rendered =[results[k] for k in todo]
    print(f"\n{len(rows)} presets, {len(todo)} rendered, {len(results) - len(todo)} from cache "
          f"in {time.monotonic() - t0:.2f} s wall ({t_prep:.2f} s setup)"
          + (f"; per render: max {max(r.get('t_total', 0) for r in rendered):.2f} s" if rendered else ""))
    if args.csv:
        keys = ["lufs", "peak_dbfs", "left_lufs", "right_lufs", "crest_db", "centroid_hz", *EDGES, "t_build", "t_load", "t_render", "t_total"]
        levels = [f"{db:+.0f}" for db in VOLUME_CHECK_DB] if args.volume else []
        extra = ["pre_peak_dbfs"] * bool(levels) + [f"{c}_{lv}dB" for lv in levels for c in ("limited_pct", "max_gr_db")]
        with open(args.csv, "w") as fh:
            fh.write("pc,preset," + ",".join(keys + extra) + "\n")
            for row in rows:
                a = row["after"]
                if "error" not in a:
                    vals = [a[k] for k in keys] + ([a["pre_peak_dbfs"]] + [x for lv in levels for x in a["volume"][lv]]
                                                   if levels else [])
                    fh.write(f"{row['pc']},{row['name']}," + ",".join(map(str, vals)) + "\n")
    return rows


def _safe_render(job):
    try:
        return render(job)
    except Exception as e:  # one bad preset must not hide the others
        return job["id"], dict(error=repr(e))


if __name__ == "__main__":
    main()
