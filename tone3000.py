# /// script
# requires-python = ">=3.10"
# ///
"""Build the FCB1010 rig for the TONE3000 plugin (NAM captures + IRs).

Usage:
  uv run tone3000.py map        Show the rig: PC -> preset, blocks, MIDI map
  uv run tone3000.py build      Build every rigs/*.json preset (PC order) and the
                                FCB1010 MIDI map. TONE3000 must NOT be running
                                (the standalone saves state on exit).
  uv run tone3000.py configure  Standalone settings for this hardware: JACK at
                                48 kHz/128, guitar input, FCB MIDI input, mono,
                                NAM calibration, 2x oversampling, A2-Full default.
  uv run tone3000.py search <words> [--gear=...] [--arch=2|1|any]
  uv run tone3000.py models <tone_id> ...
  uv run tone3000.py check [rigs/NN-x.json ...]   validate + build in memory
  uv run tone3000.py docs       regenerate rigs/RIGS.md (chains + capture links)

Presets are data: one JSON file per song in rigs/ (format in rigs/README.md).
Slots are fixed because TONE3000 maps CCs to block *positions*:

  1 boost (SW8/CC22) | 2 drive (SW9/CC23) | 3 amp | 4 cab | 5 echo (SW10/CC24) | 6+ ambience
  SW6/CC20 gate · SW7/CC21 spread · EXP A/CC27 treble · EXP B/CC7 output

Program Change N loads the Nth preset in the browser, so the rig presets are
written first in Presets/order.json. Captures come from the public TONE3000
catalog; model files are public storage objects.
"""

import base64
import html
import json
import math
import re
import struct
import subprocess
import sys
import urllib.request
import uuid
from pathlib import Path

CONFIG = Path.home() / ".config/TONE3000"
PRESETS = CONFIG / "Presets"
SETTINGS = CONFIG / "TONE3000.settings"
CACHE = Path.home() / ".cache/tone3000-rig"

API = "https://api.tone3000.com"
STORAGE = API + "/storage/v1/object/public/models/"
# Public anon key the tone3000.com site itself ships to every browser.
ANON_KEY = ("eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6Imd6eWJp"
            "dW9weGtkeGJ5dG5vamRzIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MzgwODIxNjUsImV4cCI6MjA1"
            "MzY1ODE2NX0.Gq66BJXjtLsqP2nAGXm9Xb9PAjoeZalWUj66K4nmVSU")

# One JSON file per preset (pc, chain, sources, notes) — see rigs/README.md.
RIGS = Path(__file__).resolve().parent / "rigs"

# Standalone audio/MIDI setup for this rig (Scarlett 2i2 3rd Gen on PipeWire).
# JACK via pipewire-jack; the launcher pins the graph to BUFFER with
# PIPEWIRE_QUANTUM, since JACK clients can't set the PipeWire quantum themselves.
AUDIO = {
    "deviceType": "JACK",
    "audioInputDeviceName": "Scarlett 2i2 3rd Gen Input 2 Mic/Inst/Line",  # guitar jack
    "audioOutputDeviceName": "Scarlett 2i2 3rd Gen Headphones / Line 1-2",
    "audioDeviceRate": "48000.0",
    "audioDeviceBufferSize": "128",
    "audioDeviceInChans": "1",
}
MIDI_PORT = "USB Midi MIDI 1"  # the FCB1010's USB-MIDI interface (ALSA sequencer port)
INPUT_MODE = "left"  # mono: the single guitar channel
# Scarlett 2i2 3rd Gen instrument input clips at +12.5 dBu with the gain knob
# fully down; NAM uses this to hit each capture at its original level.
CALIBRATION_DBU = 12.5
# 2x oversampling tames aliasing on high-gain captures; measured ~11% DSP load
# at 128 samples without it on an i7-1365U, so there's ample headroom.
OVERSAMPLING = True
OVERSAMPLING_FACTOR = 0.0  # choice index: 0 = 2x, 1 = 4x

# Block slots are positional (TONE3000 maps CCs to "Block N"), so every preset
# uses the same layout:  1 boost | 2 drive | 3 amp/stack | 4 cab | 5 echo | 6+ ambience.
# Stereo (dual-rig) presets split after slot 2; the right chain is
# R1 amp/stack | R2 cab | R3 echo | R4 ambience.
MIDI_MAP = [  # (targetId, CC) — must match rig.py; one CC may drive several targets
    ("gateEnabled", 20),       # SW6
    ("spreadEnabled", 21),     # SW7  stereo spread / chorus
    ("block1Power", 22),       # SW8  lead boost
    ("block2Power", 23),       # SW9  drive
    ("block5Power", 24),       # SW10 echo (left / mono chain)
    ("rightBlock3Power", 24),  # SW10 echo (right chain of dual-rig presets)
    ("toneTreble", 27),        # EXP A
    ("outputLevel", 7),        # EXP B
]


# --- JUCE ValueTree binary (T3KB) -------------------------------------------

def read_cint(b, i):
    n = b[i]; i += 1
    neg, n = n & 0x80, n & 0x7F
    v = int.from_bytes(b[i:i + n], "little")
    return (-v if neg else v), i + n


def write_cint(v):
    body = abs(v).to_bytes((abs(v).bit_length() + 7) // 8, "little") if v else b""
    return bytes([len(body) | (0x80 if v < 0 else 0)]) + body


def read_var(b, i):
    n, i = read_cint(b, i)
    if n <= 0:
        return ("void", None), i
    t, body, i = b[i], b[i + 1:i + n], i + n
    return {
        1: lambda: ("int", struct.unpack("<i", body)[0]),
        2: lambda: ("bool", True),
        3: lambda: ("bool", False),
        4: lambda: ("double", struct.unpack("<d", body)[0]),
        5: lambda: ("str", body[:-1].decode()),
        6: lambda: ("int64", struct.unpack("<q", body)[0]),
        8: lambda: ("bin", bytes(body)),
        9: lambda: ("undef", None),
    }[t](), i


def write_var(tv):
    t, v = tv
    if t == "void":
        return write_cint(0)
    body = {
        "int": lambda: b"\x01" + struct.pack("<i", v),
        "bool": lambda: b"\x02" if v else b"\x03",
        "double": lambda: b"\x04" + struct.pack("<d", v),
        "str": lambda: b"\x05" + v.encode() + b"\0",
        "int64": lambda: b"\x06" + struct.pack("<q", v),
        "bin": lambda: b"\x08" + v,
        "undef": lambda: b"\x09",
    }[t]()
    return write_cint(len(body)) + body


class Node:
    def __init__(self, type_, props=None, children=None):
        self.type, self.props, self.children = type_, props or {}, children or []

    def get(self, k, default=None):
        return self.props[k][1] if k in self.props else default

    def set(self, k, v):
        t = ("bool" if isinstance(v, bool) else "int" if isinstance(v, int)
             else "double" if isinstance(v, float) else "bin" if isinstance(v, bytes) else "str")
        self.props[k] = (t, v)
        return self

    def child(self, type_):
        return next(c for c in self.children if c.type == type_)


def read_tree(b, i):
    j = b.index(0, i); name = b[i:j].decode(); i = j + 1
    n, i = read_cint(b, i)
    props = {}
    for _ in range(n):
        j = b.index(0, i); k = b[i:j].decode(); i = j + 1
        props[k], i = read_var(b, i)
    n, i = read_cint(b, i)
    kids = []
    for _ in range(n):
        c, i = read_tree(b, i)
        kids.append(c)
    return Node(name, props, kids), i


def write_tree(node):
    out = [node.type.encode() + b"\0", write_cint(len(node.props))]
    for k, tv in node.props.items():
        out += [k.encode() + b"\0", write_var(tv)]
    out.append(write_cint(len(node.children)))
    out += [write_tree(c) for c in node.children]
    return b"".join(out)


def load_t3kb(data):
    assert data[:4] == b"T3KB", "not a TONE3000 file"
    node, end = read_tree(data, 4)
    assert end == len(data)
    return node


def dump_t3kb(node):
    return b"T3KB" + write_tree(node)


# --- JUCE MemoryBlock base64 ("<size>.<chars>", little-endian bit stream) ---

B64 = ".ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789+"


B64_INDEX = {ch: i for i, ch in enumerate(B64)}


def juce_b64decode(s):
    size, _, chars = s.partition(".")
    out, acc, nbits = bytearray(), 0, 0
    for ch in chars:
        acc |= B64_INDEX[ch] << nbits
        nbits += 6
        if nbits >= 8:
            out.append(acc & 0xFF)
            acc >>= 8
            nbits -= 8
    return bytes(out[:int(size)])


def juce_b64encode(b):
    out, acc, nbits = [], 0, 0
    for byte in b:
        acc |= byte << nbits
        nbits += 8
        while nbits >= 6:
            out.append(B64[acc & 63])
            acc >>= 6
            nbits -= 6
    if nbits:
        out.append(B64[acc & 63])
    return f"{len(b)}." + "".join(out)


# --- TONE3000 catalog ----------------------------------------------------------

def rest(path, body=None):
    headers = {"apikey": ANON_KEY, "authorization": "Bearer " + ANON_KEY,
               "content-type": "application/json", "content-profile": "public"}
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(API + "/rest/v1/" + path, data=data, headers=headers)
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.load(resp)


def search_meta(tone):
    """Creator/makes/tags/counts, which only the search RPC exposes."""
    hits = rest("rpc/search_tones_a2", {
        "query_term": tone["title"], "page_number": 1, "page_size": 50, "order_by": "best-match",
        "tag_names": None, "make_names": None, "gear_filters": None, "is_calibrated": False,
        "size_filters": None, "usernames": None, "architecture_filter": None, "verified_only": False})
    return next((h for h in hits if h["id"] == tone["id"]), {})


def tone_record(tone_id):
    """Tone metadata in the shape the plugin embeds as toneJson (cached)."""
    CACHE.mkdir(parents=True, exist_ok=True)
    path = CACHE / f"tone-{tone_id}.json"
    if path.exists():
        return json.loads(path.read_text())
    tone = rest(f"tones?id=eq.{tone_id}&select=*")[0]
    models = rest(f"models?tone_id=eq.{tone_id}&is_deleted=eq.false&select=id,tone_id,user_id,"
                  "created_at,updated_at,name,model_url,size,architecture_version")
    meta = search_meta(tone)
    for m in models:  # the plugin's own URL form; the file itself is public storage
        m["storage_name"] = m["model_url"].rsplit("/", 1)[-1]
        m["model_url"] = f"https://www.tone3000.com/api/v1/models/{m['id']}/download/{m['storage_name']}"
    username = meta.get("username", "")
    record = {
        "id": tone["id"], "user_id": tone["user_id"], "title": tone["title"],
        "description": tone.get("description") or "", "created_at": tone.get("created_at"),
        "updated_at": tone.get("updated_at"), "published_at": tone.get("published_at"),
        "images": tone.get("images") or [], "is_public": True, "links": tone.get("links") or [],
        "models_count": meta.get("models_count", len(models)),
        "favorites_count": meta.get("favorites_count", 0),
        "downloads_count": meta.get("downloads_count", 0),
        "license": tone.get("license"), "sizes": tone.get("sizes"),
        "a1_models_count": meta.get("a1_models_count", 0),
        "a2_models_count": meta.get("a2_models_count", 0),
        "irs_count": meta.get("irs_count", 0), "custom_models_count": meta.get("custom_models_count", 0),
        "is_favorite": False,
        "user": {"id": tone["user_id"], "username": username, "avatar_url": meta.get("avatar_url"),
                 "display_name": meta.get("display_name"), "is_verified": meta.get("is_verified", False),
                 "url": f"https://www.tone3000.com/{username}"},
        "makes": [{"name": n} for n in meta.get("makes") or []],
        "tags": [{"name": n} for n in meta.get("tags") or []],
        "gear": tone["gear"], "format": tone.get("platform", "nam"),
        "url": f"https://www.tone3000.com/tones/{tone['id']}",
        "models": models,
    }
    atomic_write(path, json.dumps(record, indent=2).encode())
    return record


def atomic_write(path, data):
    """Write via a temp file + rename, so parallel runs never see a partial file."""
    tmp = path.with_name(f".{path.name}.{uuid.uuid4().hex}")
    tmp.write_bytes(data)
    tmp.replace(path)


def model_file(model):
    name = model.get("storage_name") or model["model_url"].rsplit("/", 1)[-1]
    path = CACHE / name
    if not path.exists():
        CACHE.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(STORAGE + name, timeout=60) as resp:
            atomic_write(path, resp.read())
    return path.read_bytes()


# --- preset construction -----------------------------------------------------------

EQ_BANDS = [("lowshelf", 100.0, 0.7099999785423279), ("bell", 250.0, 1.0), ("bell", 650.0, 1.0),
            ("bell", 1600.0, 1.0), ("bell", 3500.0, 1.399999976158142),
            ("highshelf", 8000.0, 0.7099999785423279)]


# Humbucker -> single-coil voicing (pre-model EQ, gain dB per EQ_BANDS band):
# thins lows/low-mids, adds the Tele/Strat quack and top end.
HB_TO_SINGLE_COIL = [-3.0, -2.5, 0.0, 1.5, 3.0, 2.0]


def block(kind, enabled=True, tone=None, model_id=None, mix=1.0, eq_gains=None, eq_pre=False, data=None):
    node = Node("ChainBlock").set("id", uuid.uuid4().hex).set("type", kind).set("enabled", enabled)
    node.set("normalize", True).set("slimSize", 1.0 if kind == "nam" else 0.0)
    node.set("inputGain", 0.5).set("outputGain", 0.5).set("mix", mix)
    if tone is None:
        return node
    model = next(m for m in tone["models"] if m["id"] == model_id)
    record = dict(tone, models=[{k: v for k, v in model.items() if k != "storage_name"}])
    node.set("toneId", tone["id"]).set("toneJson", json.dumps(record, indent=2))
    node.set("activeModelId", model_id)
    gains = eq_gains or [0.0] * len(EQ_BANDS)
    eq = Node("Eq").set("enabled", True).set("pre", eq_pre)
    eq.children = [Node("Band").set("type", t).set("freqHz", f).set("gainDb", float(g)).set("q", q)
                   for (t, f, q), g in zip(EQ_BANDS, gains)]
    cache = Node("ModelCache", children=[Node("CachedModel").set("modelId", model_id)
                                         .set("data", data if data is not None else model_file(model))])
    node.children = [eq, cache]
    return node


# --- IR audio helpers (IRs are embedded as WAV bytes) ------------------------------

def wav_read(raw):
    """-> (channels: list[list[float]], rate). PCM 16/24/32-bit or float32."""
    pos, fmt = 12, None
    while pos < len(raw):
        cid, size = raw[pos:pos + 4], struct.unpack_from("<I", raw, pos + 4)[0]
        body = raw[pos + 8:pos + 8 + size]
        if cid == b"fmt ":
            fmt = struct.unpack_from("<HHIIHH", body)
        elif cid == b"data":
            break
        pos += 8 + size + (size & 1)
    tag, nch, rate, _, _, bits = fmt
    width = bits // 8
    frames = len(body) // (width * nch)
    if tag == 3:
        vals = struct.unpack(f"<{frames * nch}f", body[:frames * nch * 4])
    elif width == 2:
        vals = [v / 32768 for v in struct.unpack(f"<{frames * nch}h", body[:frames * nch * 2])]
    elif width == 3:
        vals = [int.from_bytes(body[i:i + 3], "little", signed=True) / 8388608
                for i in range(0, frames * nch * 3, 3)]
    else:
        vals = [v / 2147483648 for v in struct.unpack(f"<{frames * nch}i", body[:frames * nch * 4])]
    return [list(vals[c::nch]) for c in range(nch)], rate


def wav_write(samples, rate=48000):
    """Mono 24-bit PCM WAV."""
    pcm = b"".join(max(-8388608, min(8388607, int(round(v * 8388607)))).to_bytes(3, "little", signed=True)
                   for v in samples)
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 3, 3, 24)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(pcm)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm)


def wav_rate(raw):
    return struct.unpack_from("<I", raw, raw.index(b"fmt ") + 12)[0]


def resample(x, src, dst=48000):
    """Linear-interpolation resample (fine for reverb tails)."""
    if src == dst:
        return x
    n = int(len(x) * dst / src)
    out = []
    for i in range(n):
        t = i * src / dst
        j = int(t)
        f = t - j
        out.append(x[j] * (1 - f) + (x[j + 1] if j + 1 < len(x) else 0.0) * f)
    return out


def trimmed_ir(raw, seconds, channel=0):
    """First channel at 48 kHz (TONE3000 loads non-48k IRs as silence), cut to
    `seconds` with a 10% cosine fade-out (keeps presets small)."""
    chans, rate = wav_read(raw)
    x = resample(chans[channel][:int(seconds * rate)], rate)
    rate = 48000
    fade = max(1, len(x) // 10)
    for i in range(fade):
        x[len(x) - fade + i] *= 0.5 * (1 + math.cos(math.pi * i / fade))
    return wav_write(x, rate)


def analog_echo(delay_ms=300.0, feedback=0.6, repeats=8, cutoff_hz=2800.0, rate=48000):
    """Wet-only IR of an analog (BBD) delay like a Boss DM-2: each repeat passes the
    bucket-brigade's low-pass again, so the echoes darken as they decay."""
    d = int(rate * delay_ms / 1000)
    h = [0.0] * (d * (repeats + 1) + rate // 10)
    a = math.exp(-2 * math.pi * cutoff_hz / rate)
    pulse = [1.0] + [0.0] * (rate // 20)
    for k in range(1, repeats + 1):
        y, prev = [], 0.0
        for v in pulse:  # two one-pole low-passes per trip through the BBD
            prev = (1 - a) * v + a * prev
            y.append(prev)
        pulse, prev = [], 0.0
        for v in y:
            prev = (1 - a) * v + a * prev
            pulse.append(prev * (feedback if k > 1 else 1.0))
        for i, v in enumerate(pulse):
            h[k * d + i] += v
    peak = max(abs(v) for v in h)
    return wav_write([v / peak * 0.9 for v in h], rate)


SYNTH_TONE_ID = 900000001  # local-only tone ids for generated IRs (never hit the API)


def synth_tone(model_id, title, description, gear="pedal"):
    return {
        "id": SYNTH_TONE_ID + model_id % 1000, "title": title, "description": description,
        "gear": gear, "format": "ir", "images": [], "is_public": False, "links": [],
        "license": "t3k", "user": {"username": "fcb1010-rig"}, "makes": [], "tags": [],
        "models": [{"id": model_id, "name": title, "model_url": "", "architecture_version": None}],
    }


def default_params():
    """Params node from a factory preset, so every parameter gets a sane value."""
    factory = sorted((PRESETS / "Factory").glob("*.t3kpreset"))[0]
    return load_t3kb(factory.read_bytes()).child("Params")


def preset_id(name):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"fcb1010-rig/{name}").hex


ROLE_SLOTS = {"boost": 1, "drive": 2, "amp": 3, "cab": 4, "echo": 5}  # left/mono chain
RIGHT_ROLE_SLOTS = {"amp": 1, "cab": 2, "echo": 3}                     # dual-rig right chain


def load_rigs():
    rigs = [json.loads(f.read_text()) | {"_file": f.name} for f in sorted(RIGS.glob("*.json"))]
    return sorted(rigs, key=lambda r: r["pc"])


def model_of(tone_id, model_id):
    return next(m for m in tone_record(tone_id)["models"] if m["id"] == model_id)


def build_block(spec):
    """One chain block from a rigs/*.json block spec."""
    kind = spec["type"]
    on = spec.get("enabled", True)
    mix = float(spec.get("mix", 1.0))
    if kind == "insert":
        return block("insert")
    if kind == "echo":  # generated analog-style echo IR
        ms = int(spec["delay_ms"])
        fb, cut_hz = float(spec.get("feedback", 0.5)), float(spec.get("cutoff_hz", 3500.0))
        mid = ms * 100 + int(fb * 10) + int(cut_hz / 1000) * 1000000
        title = spec.get("label") or f"Analog echo {ms} ms"
        return block("ir", on, synth_tone(mid, f"{title} (generated)",
                                          f"{ms} ms BBD-style echo, feedback {fb}, wet only"),
                     mid, mix=mix, data=analog_echo(ms, fb, cutoff_hz=cut_hz))
    tone, mid = tone_record(spec["tone_id"]), spec["model_id"]
    data = None
    if kind == "ir":
        raw = model_file(model_of(spec["tone_id"], mid))
        if spec.get("trim_seconds") or wav_rate(raw) != 48000:  # non-48k IRs load silent
            data = trimmed_ir(raw, float(spec.get("trim_seconds") or 60.0))
    return block(kind, on, tone, mid, mix=mix, eq_gains=spec.get("eq"),
                 eq_pre=spec.get("eq_pre", False), data=data)


def validate(rig):
    """Structural checks so footswitch slots line up across presets."""
    errs = []
    for side, slots in (("left", ROLE_SLOTS), ("right", RIGHT_ROLE_SLOTS)):
        for i, spec in enumerate(rig.get(side, []), 1):
            want = slots.get(spec.get("role"))
            if want and want != i:
                errs.append(f"{side}[{i}] role {spec['role']} must be slot {want}")
            if spec.get("type") not in ("nam", "ir", "echo", "insert"):
                errs.append(f"{side}[{i}] unknown type {spec.get('type')}")
            if spec.get("type") in ("nam", "ir") and not (spec.get("tone_id") and spec.get("model_id")):
                errs.append(f"{side}[{i}] needs tone_id and model_id")
    if rig.get("right") and not rig.get("split_after"):
        errs.append("right chain needs split_after (1-based left slot)")
    if len(rig.get("left", [])) > 12 or len(rig.get("right", [])) > 12:
        errs.append("max 12 blocks per chain")
    return errs


# Applied to every preset.
GLOBAL_PARAMS = {
    "outputLevel": 1.0,        # max: +24 dB (normalized, 0.5 = 0 dB)
    "gateEnabled": 1.0,
    "gateThreshold": -35.0,    # dB
}


def build_preset(rig):
    left = [build_block(b) for b in rig["left"]]
    right = [build_block(b) for b in rig.get("right", [])]
    split = rig.get("split_after")
    left += [block("insert") for _ in range(max(0, 5 - len(left)))]
    right += [block("insert") for _ in range(max(0, 5 - len(right)))]
    snap = Node("ChainSnapshot").set("stereoEnabled", bool(split)).set("branchSide", "left")
    snap.set("branchAfterBlockId", left[split - 1].get("id") if split else "")
    snap.children = [Node("ChainBlocks", children=left), Node("RightChainBlocks", children=right)]
    params = default_params()
    values = {"gateEnabled": 0.0, **rig.get("params", {}), **GLOBAL_PARAMS}
    for p in params.children:
        if p.get("id") in values:
            p.set("value", float(values[p.get("id")]))
    root = Node("T3KPreset").set("schemaVersion", 1).set("name", rig["name"])
    root.children = [snap, params]
    return root


def running():
    return bool(subprocess.run(["pgrep", "-x", "TONE3000"], capture_output=True).stdout)


def edit_state(fn):
    """Decode the standalone's saved plugin state, apply fn, write it back."""
    text = SETTINGS.read_text()
    m = re.search(r'name="filterState" val="([^"]*)"', text)
    if not m:
        sys.exit("No saved TONE3000 state yet — open and close the standalone once, then re-run.")
    state = load_t3kb(juce_b64decode(html.unescape(m.group(1))))
    fn(state)
    encoded = html.escape(juce_b64encode(dump_t3kb(state)), quote=True)
    SETTINGS.write_text(text[:m.start(1)] + encoded + text[m.end(1):])


def write_midi_map():
    def apply(state):
        mappings = state.child("MidiMappings")
        mappings.set("channel", 0)
        mappings.children = [Node("Mapping").set("targetId", t).set("source", "cc").set("number", cc)
                             for t, cc in MIDI_MAP]
    edit_state(apply)
    print(f"MIDI map written ({len(MIDI_MAP)} mappings, omni)")


def alsa_midi_id(port_name):
    """JUCE identifies ALSA MIDI inputs as '<client>-<port>'."""
    out = subprocess.run(["aconnect", "-i"], capture_output=True, text=True).stdout
    client = None
    for line in out.splitlines():
        if m := re.match(r"client (\d+):", line):
            client = m.group(1)
        elif (m := re.match(r"\s+(\d+) '(.*?)\s*'", line)) and m.group(2) == port_name:
            return f"{client}-{m.group(1)}"
    return None


def configure():
    """Audio device, MIDI input, input mode and NAM input calibration."""
    if running():
        sys.exit("TONE3000 is running — quit it first.")
    text = SETTINGS.read_text()
    midi_id = alsa_midi_id(MIDI_PORT)
    attrs = " ".join(f'{k}="{v}"' for k, v in AUDIO.items())
    midi = f'<MIDIINPUT name="{MIDI_PORT}" identifier="{midi_id}"/>' if midi_id else ""
    setup = html.escape(f"<DEVICESETUP {attrs}>{midi}</DEVICESETUP>", quote=True)
    entry = f'<VALUE name="audioSetup" val="{setup}"/>'
    text, n = re.subn(r'<VALUE name="audioSetup">.*?</VALUE>|<VALUE name="audioSetup" val="[^"]*"/>',
                      lambda _: entry, text, flags=re.S)
    if not n:
        text = text.replace("</PROPERTIES>", f"  {entry}\n</PROPERTIES>")
    SETTINGS.write_text(text)
    print(f"audio: {AUDIO['deviceType']} {AUDIO['audioInputDeviceName']} -> "
          f"{AUDIO['audioOutputDeviceName']} @ {AUDIO['audioDeviceRate']} Hz / {AUDIO['audioDeviceBufferSize']}")
    print(f"MIDI input: {MIDI_PORT} ({midi_id or 'NOT FOUND — plug in the USB-MIDI cable'})")

    def apply(state):
        state.set("inputMode", INPUT_MODE)
        for p in state.child("PARAMETERS").children:
            if p.get("id") == "calibrateInput":
                p.set("value", 1.0)
            elif p.get("id") == "inputCalibrationLevel":
                p.set("value", CALIBRATION_DBU)
            elif p.get("id") == "osEnabled":
                p.set("value", 1.0 if OVERSAMPLING else 0.0)
            elif p.get("id") == "osFactor":
                p.set("value", OVERSAMPLING_FACTOR)
    edit_state(apply)
    print(f"input mode: {INPUT_MODE}; NAM input calibration on at {CALIBRATION_DBU} dBu; "
          f"oversampling {'2x' if OVERSAMPLING_FACTOR == 0 else '4x'} {'on' if OVERSAMPLING else 'off'}")

    # Global preferences: A2-Full as the default NAM size for blocks added in-app.
    (CONFIG / "preferences.settings").write_text(
        '<?xml version="1.0" encoding="UTF-8"?>\n\n<PROPERTIES>\n'
        '  <VALUE name="namSlimSizeDefault" val="1.0"/>\n'
        '  <VALUE name="multiCore" val="1"/>\n</PROPERTIES>\n')
    print("preferences: default NAM size A2-Full, multi-core on")

    launcher = Path.home() / ".local/share/applications/tone3000.desktop"
    if launcher.exists():
        quantum = f"PIPEWIRE_QUANTUM={AUDIO['audioDeviceBufferSize']}/{int(float(AUDIO['audioDeviceRate']))}"
        lines = [re.sub(r"^Exec=(?:env \S+ )?", f"Exec=env {quantum} ", l) if l.startswith("Exec=") else l
                 for l in launcher.read_text().splitlines()]
        launcher.write_text("\n".join(lines) + "\n")
        print(f"launcher: {quantum}")


def build():
    if running():
        sys.exit("TONE3000 is running — quit it first.")
    CACHE.mkdir(parents=True, exist_ok=True)
    rigs = load_rigs()
    for rig in rigs:
        if errs := validate(rig):
            sys.exit(f"{rig['_file']}: " + "; ".join(errs))
    ids = []
    for rig in rigs:
        pid = preset_id(rig["name"])
        (PRESETS / f"{pid}.t3kpreset").write_bytes(dump_t3kb(build_preset(rig)))
        ids.append(f"user:{pid}")
        print(f"PC {rig['pc']:<3} {rig['name']}")
    order_path = PRESETS / "order.json"
    old = json.loads(order_path.read_text()) if order_path.exists() else []
    factory = [f"factory:{p.stem}" for p in sorted((PRESETS / "Factory").glob("*.t3kpreset"))]
    rest_ids = [i for i in old + factory if i not in ids]
    order_path.write_text(json.dumps(ids + list(dict.fromkeys(rest_ids)), indent=2))
    write_midi_map()
    print(f"\n{len(ids)} presets written in PC order. Start TONE3000 and stomp away.")


def describe(spec):
    if spec["type"] == "insert":
        return "-"
    if spec["type"] == "echo":
        label = f"echo {spec['delay_ms']} ms"
    else:
        label = spec.get("label") or model_of(spec["tone_id"], spec["model_id"])["name"]
    return label + ("" if spec.get("enabled", True) else " (off)")


def show_map():
    for rig in load_rigs():
        print(f"PC {rig['pc']:<3} {rig['name']}  — {rig.get('reference', '')}")
        for side in ("left", "right"):
            for i, spec in enumerate(rig.get(side, []), 1):
                tag = f"{'L' if side == 'left' else 'R'}{i}"
                print(f"        {tag:<3} {spec.get('role', ''):<9} {describe(spec)}")
    for target, cc in MIDI_MAP:
        print(f"CC {cc:<3} -> {target}")


# --- research helpers (safe to run anytime; never touch ~/.config/TONE3000) ----------

def search(query, gear=None, arch="2", n=25):
    """tone3000.com catalog search. gear: amp, amp-cab (full rig), pedal, cab (IRs),
    outboard, space (reverbs), experimental. arch: "2" (A2 NAM), "1", or None."""
    return rest("rpc/search_tones_a2", {
        "query_term": query, "page_number": 1, "page_size": n, "order_by": "best-match",
        "tag_names": None, "make_names": None, "gear_filters": [gear] if gear else None,
        "is_calibrated": False, "size_filters": None, "usernames": None,
        "architecture_filter": arch, "verified_only": False})


def cli_search(args):
    gear = next((a.split("=", 1)[1] for a in args if a.startswith("--gear=")), None)
    arch = next((a.split("=", 1)[1] for a in args if a.startswith("--arch=")), "2")
    query = " ".join(a for a in args if not a.startswith("--"))
    for h in search(query, gear, None if arch == "any" else arch):
        print(f"{h['id']:>6} {h['gear']:<8} {h['platform']:<3} a2={h['a2_models_count']:<3} "
              f"ir={h['irs_count']:<3} dl={h['downloads_count']:<6} fav={h['favorites_count']:<5} "
              f"{h['title'][:70]} | {','.join(h['makes'] or [])[:40]} @{h['username']}")


def cli_models(args):
    for tid in args:
        tone = rest(f"tones?id=eq.{tid}&select=id,title,gear,platform,description")[0]
        models = rest(f"models?tone_id=eq.{tid}&is_deleted=eq.false&select=id,name,architecture_version")
        print(f"=== {tone['id']} [{tone['gear']}/{tone['platform']}] {tone['title']}")
        print("   ", (tone.get("description") or "").replace("\n", " ")[:800])
        for m in models:
            print(f"      {m['id']:>7} a{m['architecture_version'] or '-'} {m['name']}")


def cli_check(files):
    """Validate rigs/*.json, resolve every tone/model and build in memory (no writes
    to TONE3000). Downloads land in the shared cache."""
    ok = True
    for f in files or sorted(RIGS.glob("*.json")):
        rig = json.loads(Path(f).read_text())
        errs = validate(rig)
        if not errs:
            try:
                size = len(dump_t3kb(build_preset(rig)))
            except Exception as e:  # unknown tone/model, bad IR, network
                errs = [f"{type(e).__name__}: {e}"]
        ok &= not errs
        status = "; ".join(errs) if errs else f"ok ({size / 1e6:.1f} MB)"
        print(f"{Path(f).name}: {status}")
    sys.exit(0 if ok else 1)


def cli_docs(args):
    """Write rigs/RIGS.md: every preset, its chain, capture links and sources."""
    slot_names = {"left": "L", "right": "R"}
    out = ["# The rig, preset by preset", "",
           "Generated by `python3 tone3000.py docs` from `rigs/*.json` — edit the JSON, not this file.",
           "Every capture links to its TONE3000 page (free download).", ""]
    rigs = load_rigs()
    out += ["| PC | Preset | Reference |", "|---|---|---|"]
    out += [f"| {r['pc']} | [{r['name']}](#{re.sub(r'[^a-z0-9 -]', '', r['name'].lower()).replace(' ', '-')}) "
            f"| {r.get('reference', '')} |" for r in rigs]
    for r in rigs:
        stereo = " · dual-rig stereo" if r.get("split_after") else ""
        out += ["", f"## {r['name']}", "", f"**PC {r['pc']}**{stereo} — {r.get('reference', '')}", ""]
        if r.get("rig"):
            out += [f"*Original rig:* {r['rig']}", ""]
        out += ["| Slot | Role | Block | Default |", "|---|---|---|---|"]
        for side in ("left", "right"):
            for i, spec in enumerate(r.get(side, []), 1):
                if spec["type"] == "insert":
                    continue
                if spec["type"] == "echo":
                    what = f"{spec.get('label') or 'Analog echo'} — generated {spec['delay_ms']} ms BBD-style IR"
                else:
                    tone = tone_record(spec["tone_id"])
                    label = spec.get("label") or model_of(spec["tone_id"], spec["model_id"])["name"]
                    what = f"[{tone['title']}](https://www.tone3000.com/tones/{spec['tone_id']}) — {label}"
                if spec.get("mix", 1.0) != 1.0:
                    what += f" (mix {spec['mix']:.0%})"
                state = "on" if spec.get("enabled", True) else "off"
                out.append(f"| {slot_names[side]}{i} | {spec.get('role', '')} | {what} | {state} |")
        if r.get("split_after"):
            out += ["", f"Slots L1–L{r['split_after']} feed both rigs; left and right are panned apart."]
        if r.get("notes"):
            out += ["", f"*Notes:* {r['notes']}"]
        if r.get("sources"):
            out += ["", "*Sources:* " + " · ".join(f"<{u}>" for u in r["sources"])]
    out += ["", "## Footswitches", "", "| FCB1010 | CC | TONE3000 |", "|---|---|---|",
            "| SW1–5 (banks 00–02) | PC 0–14 | presets above |",
            "| SW6 | 20 | noise gate |", "| SW7 | 21 | stereo spread (chorus-ish) |",
            "| SW8 | 22 | slot 1: lead boost |", "| SW9 | 23 | slot 2: drive |",
            "| SW10 | 24 | slot 5 / R3: solo echo |", "| EXP A | 27 | treble sweep |", "| EXP B | 7 | output level |"]
    (RIGS / "RIGS.md").write_text("\n".join(out) + "\n")
    print(f"wrote {RIGS / 'RIGS.md'} ({len(rigs)} presets)")


if __name__ == "__main__":
    cmd, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("map", [])
    commands = {
        "map": lambda: show_map(), "build": lambda: build(), "configure": lambda: configure(),
        "search": lambda: cli_search(args), "models": lambda: cli_models(args),
        "check": lambda: cli_check(args), "docs": lambda: cli_docs(args),
    }
    commands.get(cmd, lambda: sys.exit(__doc__))()
