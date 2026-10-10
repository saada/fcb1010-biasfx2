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
  uv run tone3000.py csv        print every preset's resolved tone.csv settings
  uv run tone3000.py csv --init [--force]   write rigs/tone.csv from the JSONs
  uv run tone3000.py tune <@tag|preset|*> <word> [xN]   one rigs/vocab.csv move into tone.csv
  uv run tone3000.py tune --undo                         revert the last tune

Presets are data: one JSON file per song in rigs/ (format in rigs/README.md), and every
number we tune in rigs/tone.csv (JSON -> `*` row -> `@tag` rows -> the preset's row).
Slots are fixed because TONE3000 maps CCs to block *positions*:

  1 boost (CC22) | 2 drive (SW9/CC23) | 3 amp | 4 cab | 5 echo (CC24) | 6 echo 2 or ambience (CC30) | 7+ ambience
  Song banks: SW8 LEAD (CC26 = boost + echo) · SW10 TUNER (DAW) · EXP B/CC7 output
  Maiden scene bank: SW8 echo (CC24, slot 5) · SW9 echo 2 (CC30, slot 6) — see rig.py / README pedal map

Program Change N loads the Nth preset in the browser, so the rig presets are
written first in Presets/order.json. Captures come from the public TONE3000
catalog; model files are public storage objects.
"""

import base64
import csv
import html
import io
import json
import math
import os
import re
import struct
import subprocess
import sys
import urllib.request
import uuid
import zlib
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
    "audioDeviceBufferSize": "256",  # 128 xruns with the heavier stereo rigs (2026-09-26)
    "audioDeviceInChans": "1",
}
SINK_MATCH = "Scarlett_2i2"  # PipeWire sink set to unity by configure
ALSA_CARD = "USB"            # Scarlett 2i2 3rd Gen (snd-usb-audio / scarlett2 controls)
ALSA_CONTROLS = [("Line In 2 Level", "Inst"), ("Line In 2 Air", "nocap"), ("Direct Monitor", "Off")]
MIDI_PORT = "USB Midi MIDI 1"  # the FCB1010's USB-MIDI interface (ALSA sequencer port)
INPUT_MODE = "left"  # mono: the single guitar channel
# Scarlett 2i2 3rd Gen instrument input clips at +12.5 dBu with the gain knob
# fully down; NAM uses this to hit each capture at its original level.
CALIBRATION_DBU = 12.5
# Oversampling off: at 256 samples 2x still produced xruns on the stereo rigs
# (experiments/README.md); at 48 kHz A2 captures lose little without it.
OVERSAMPLING = False
OVERSAMPLING_FACTOR = 0.0  # choice index: 0 = 2x, 1 = 4x

# Block slots are positional (TONE3000 maps CCs to "Block N"), so every preset
# uses the same layout:  1 boost | 2 drive | 3 amp/stack | 4 cab | 5 echo | 6+ ambience.
# Stereo (dual-rig) presets split after slot 2; the right chain is
# R1 amp/stack | R2 cab | R3 echo | R4 ambience.
MIDI_MAP = [  # (targetId, CC) — must match rig.py; one CC may drive several targets
    ("gateEnabled", 20),       # SW6
    ("spreadEnabled", 21),     # SW7  stereo spread / chorus
    ("block1Power", 22),       # boost (no switch since the bank redesign; kept for manual use)
    ("block2Power", 23),       # song banks SW9: drive
    ("block5Power", 24),       # Maiden SW8: signature echo 1 (The Evil That Men Do), left / mono chain
    ("rightBlock3Power", 24),  # ... and the right chain of dual-rig presets
    ("block6Power", 30),       # Maiden SW9: second signature echo (Can I Play with Madness)
    ("rightBlock4Power", 30),
    ("block1Power", 26),       # song banks SW8 LEAD: boost + echo together
    ("block5Power", 26),
    ("rightBlock3Power", 26),
    ("toneTreble", 27),        # EXP A
]  # EXP B (CC 7) is not here: it is the DAW's volume stage (qtractor_rig.VOLUME), which no PC resets (D22)


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
    """Plugin state and v0.0.9 presets ("T3KB" + tree), or a preset file in the framing
    TONE3000 writes since 0.0.10 ("T3KH" + int32 LE header size + header tree + body tree;
    plugin/src/PresetFile.cpp). Returns the body."""
    if data[:4] == b"T3KH":
        size = struct.unpack_from("<i", data, 4)[0]
        node, end = read_tree(data, 8 + size)
        assert end == len(data)
        return node
    assert data[:4] == b"T3KB", "not a TONE3000 file"
    node, end = read_tree(data, 4)
    assert end == len(data)
    return node


def dump_t3kb(node):
    return b"T3KB" + write_tree(node)


def dump_preset(node):
    """A .t3kpreset in TONE3000's v2 framing: a small T3KPresetHeader (id, name) ahead of the
    body, so the browser (and every Program Change, which lists the folder) reads a few
    hundred bytes per preset instead of parsing megabytes of embedded models."""
    header = write_tree(Node("T3KPresetHeader").set("id", node.get("id", "")).set("name", node.get("name", "")))
    return b"T3KH" + struct.pack("<i", len(header)) + header + write_tree(node)


def preset_header(path):
    """(id, name) as TONE3000 lists a preset file: a legacy file with no id uses its stem."""
    data = path.read_bytes()
    if data[:4] == b"T3KH":
        size = struct.unpack_from("<i", data, 4)[0]
        header, _ = read_tree(data, 8)
        assert 8 + size <= len(data)
    else:
        header = load_t3kb(data)
    return header.get("id") or path.stem, header.get("name") or path.stem


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
        if not m.get("model_url"):  # some models have no file (never uploaded/processed)
            m["storage_name"] = None
            continue
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
    if not model.get("model_url"):
        raise ValueError(f"model {model['id']} ({model.get('name')}) has no downloadable file")
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
    """Mono 24-bit PCM WAV. Always an even number of frames: TONE3000 loads a WAV
    whose data chunk has an odd byte length as silence."""
    samples = list(samples) + ([0.0] if len(samples) % 2 else [])
    pcm = b"".join(max(-8388608, min(8388607, int(round(v * 8388607)))).to_bytes(3, "little", signed=True)
                   for v in samples)
    fmt = struct.pack("<HHIIHH", 1, 1, rate, rate * 3, 3, 24)
    return (b"RIFF" + struct.pack("<I", 4 + 8 + len(fmt) + 8 + len(pcm)) + b"WAVE"
            + b"fmt " + struct.pack("<I", len(fmt)) + fmt + b"data" + struct.pack("<I", len(pcm)) + pcm)


def wav_rate(raw):
    return struct.unpack_from("<I", raw, raw.index(b"fmt ") + 12)[0]


def wav_loads_in_tone3000(raw):
    """TONE3000 silently fails on non-48 kHz IRs and on odd-length data chunks."""
    size = struct.unpack_from("<I", raw, raw.index(b"data") + 4)[0]
    return wav_rate(raw) == 48000 and size % 2 == 0


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


def analog_echo(delay_ms=300.0, feedback=0.6, repeats=8, cutoff_hz=2800.0, rate=48000, reverb=None):
    """Wet-only IR of an analog (BBD) delay like a Boss DM-2: each repeat passes the
    bucket-brigade's low-pass again, so the echoes darken as they decay.

    reverb: optional (samples at 48 kHz, level_db) summed in, so one block (one
    footswitch) carries the song's delay *and* its reverb. delay_ms 0 = reverb only."""
    if not delay_ms:
        rev, _ = reverb
        peak = max(abs(v) for v in rev)
        return wav_write([v / peak * 0.9 for v in rev], rate)
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
    end = max(i for i, v in enumerate(h) if abs(v) > peak * 1e-3) + rate // 50  # -60 dB tail
    h = [v / peak for v in h[:end]]  # shorter IR = cheaper convolution
    if reverb:
        rev, level_db = reverb
        g = 10 ** (level_db / 20) / max(abs(v) for v in rev)
        h += [0.0] * max(0, len(rev) - len(h))
        for i, v in enumerate(rev):
            h[i] += v * g
    peak = max(abs(v) for v in h)
    return wav_write([v / peak * 0.9 for v in h], rate)


# Reverb tails are cut at this length. Long convolutions cost CPU on every cycle and stall
# preset loads: with three TONE3000s in the DAW, 3.5 s tails gave 71 xruns over 7 bank
# changes, and without them there were none (experiments D11).
IR_TAIL_MAX_S = float(os.environ.get("T3K_IR_TAIL_MAX_S", 2.0))


def reverb_tail(spec):
    """Catalog reverb IR for an echo block's `reverb`: first channel, 48 kHz, trimmed."""
    raw = model_file(model_of(spec["tone_id"], spec["model_id"]))
    chans, rate = wav_read(trimmed_ir(raw, min(float(spec.get("trim_seconds", 3.0)), IR_TAIL_MAX_S)))
    return chans[0], float(spec.get("level_db", -6.0))


SYNTH_TONE_ID = 900000001  # local-only tone ids for generated IRs (never hit the API)


def synth_tone(model_id, title, description, gear="pedal"):
    return {
        "id": SYNTH_TONE_ID + model_id % 1000, "title": title, "description": description,
        "gear": gear, "format": "ir", "images": [], "is_public": False, "links": [],
        "license": "t3k", "user": {"username": "fcb1010-rig"}, "makes": [], "tags": [],
        "models": [{"id": model_id, "name": title, "model_url": "", "architecture_version": None}],
    }


# Every preset's faceplate parameters (TONE3000's presetParameterIds, ProcessorPresets.cpp),
# in the plugin's real units: knobs 0-1, tone 0-10, gate dB/ms. A preset sets *all* of them on
# load and missing ones fall back to the plugin default, so they're spelled out here.
# The first block is what the rig has always used: until v0.0.9 the builder copied them
# from the first factory preset (Bogner Fullstack), which is why outputBalance, spreadOffset
# and align* aren't plugin defaults. Frozen so a new plugin's factory set can't move the sound.
BASE_PARAMS = {
    "inputLevel": 0.5, "outputLevel": 0.5299999713897705, "outputBalance": 0.5600000023841858,
    "toneBass": 5.0, "toneMid": 5.0, "toneTreble": 5.0, "gateThreshold": -70.0, "gateEnabled": 1.0,
    "toneEqEnabled": 1.0, "spreadEnabled": 0.0, "spreadOffset": 0.8100000023841858, "spreadWobble": 0.25,
    "spreadWobbleEnabled": 1.0, "spreadCrossover": 0.5, "spreadCrossoverEnabled": 1.0,
    "spreadDiffuseEnabled": 1.0, "alignEnabled": 1.0, "alignOffset": 0.5299999713897705, "alignWobble": 0.25,
    "alignWobbleEnabled": 0.0, "alignCrossover": 0.5, "alignCrossoverEnabled": 0.0, "alignDiffuseEnabled": 0.0,
    "chainPanLeft": 0.0, "chainPanRight": 1.0, "chainPanLinked": 1.0, "chainInvertLeft": 0.0,
    "chainInvertRight": 0.0,
    # TONE3000 >= 0.0.11. Gate release/hold: v0.0.9's hard-wired 100 ms / 50 ms (its gate had no
    # knobs for them). The plugin's new defaults, 50 / 20 ms, close the note tail twice as fast;
    # a preset may opt into that per song with "params". Range 80 dB = v0.0.9's -80 dB floor.
    "gateRelease": 100.0, "gateHold": 50.0, "gateRange": 80.0,
    # Pitch shift (input stage, before the amps; adds 11-31 ms latency while on): off.
    # pitchSemitones -24..24, pitchStep 1 = whole semitones, pitchTonality 20000 Hz = off,
    # pitchWindow = choice index of 20/30/40/60 ms (1 = 30 ms, the plugin default).
    "pitchEnabled": 0.0, "pitchSemitones": 0.0, "pitchStep": 1.0, "pitchTonality": 20000.0, "pitchWindow": 1.0,
}


def params_node(values):
    return Node("Params", children=[Node("Param").set("id", k).set("value", float(v)) for k, v in values.items()])


def preset_id(name):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"fcb1010-rig/{name}").hex


ROLE_SLOTS = {"boost": 1, "drive": 2, "amp": 3, "cab": 4, "echo": 5, "echo2": 6}  # left/mono chain
RIGHT_ROLE_SLOTS = {"amp": 1, "cab": 2, "echo": 3, "echo2": 4}                     # dual-rig right chain


def load_rigs(sheet=None):
    """rigs/*.json with rigs/tone.csv applied (tone_sheet), plus a generated `harmony` preset
    per scene bank, numbered after them. A tone.csv error raises ToneError."""
    sheet = sheet or tone_sheet()
    if sheet["errors"]:
        raise ToneError(sheet["errors"])
    rigs = sorted([apply_tone(json.loads(f.read_text()) | {"_file": f.name}, sheet)
                   for f in sorted(RIGS.glob("*.json"))], key=lambda r: r["pc"])
    heavy = [r for r in rigs if r.get("scene") == "heavy"]
    nxt = max(r["pc"] for r in rigs) + 1
    return rigs + [harmony_rig(r, nxt + i) for i, r in enumerate(sorted(heavy, key=lambda r: r["bank"]))]


def harmony_rig(heavy, pc):
    """The harmony guitarist's rig for a scene bank: the heavy preset's partner side (right
    chain: Smith / Gers) as one light chain - amp + cab, no delay or reverb IRs - so a third
    TONE3000 can voice the DAW's twin-lead harmony without the CPU of a full dual rig
    (experiments D11). Built on the fly; not a file in rigs/."""
    side = heavy.get("right") or heavy["left"][heavy.get("split_after", 0):]
    core = [dict(b) for b in side if b.get("role") in ("amp", "cab")]
    trim = heavy.get("song", {}).get("harmony_trim_db", 0.0)  # harmony = one guitarist, 3 dB under the pair
    if trim:
        core[-1]["out_db"] = round(max(-24.0, min(24.0, core[-1].get("out_db", 0.0) + trim)), 1)
    return {"pc": pc, "name": heavy["name"].replace("Heavy", "Harmony"), "scene": "harmony",
            "bank": heavy["bank"], "_file": f"(generated from {heavy['_file']})",
            "reference": f"Twin-lead harmony voice for {heavy['name']}",
            "rig": "the partner guitarist's amp + cab from " + heavy["name"],
            "notes": "Generated: right-hand (partner) chain of the heavy preset, no echo/ambience.",
            "left": [{"role": "boost", "type": "insert"}, {"role": "drive", "type": "insert"}, *core],
            "params": {k: v for k, v in heavy.get("params", {}).items() if not k.startswith(("chain", "align"))},
            "tone_params": dict(heavy.get("tone_params", {})), "levels": heavy.get("levels", {})}


# --- tone sheet: rigs/tone.csv, every number we tune in one sheet (rigs/README.md) -------------
# One row per preset (its JSON file stem, with its `tags`), a `*` row for every preset, and
# `@tag` rows for every preset carrying that tag. Precedence: JSON -> `*` -> `@tag` rows in file
# order -> the preset's row. An empty cell inherits.
#   rule "add" (gains, dB): `*` and `@tag` cells are offsets added to each preset's own value.
#   rule "set" (absolute):  `*` and `@tag` cells are defaults for presets whose own cell is empty.

TONE_CSV = RIGS / "tone.csv"
VOCAB_CSV = RIGS / "vocab.csv"
TUNE_HISTORY = RIGS / ".tune-history"  # gitignored: what `tune --undo` restores
EQ_COLUMNS = ("eq_100", "eq_250", "eq_650", "eq_1k6", "eq_3k5", "eq_8k")  # = EQ_BANDS, in order
CAPTURE = ("nam", "ir")
END = "end"  # a chain's end block: its cab, or its amp when the cab slot is empty (full rigs)

# column: (rule, unit, lo, hi, chains, role, block types, field). chains "L" = left/mono chain,
# "R" = right chain, "LR" = both. A param column has role None and its TONE3000 param id as field.
TONE_COLUMNS = {
    "out_db": ("add", "dB", -24, 24, "LR", END, CAPTURE, "out_db"),  # preset fader (see apply_tone)
    "in_db": ("add", "dB", -24, 24, "LR", "amp", ("nam",), "in_db"),
    "boost_db": ("add", "dB", -24, 24, "L", "boost", CAPTURE, "out_db"),
    "drive_db": ("add", "dB", -24, 24, "L", "drive", CAPTURE, "out_db"),
    "amp_db": ("add", "dB", -24, 24, "L", "amp", CAPTURE, "out_db"),
    "cab_db": ("add", "dB", -24, 24, "L", "cab", CAPTURE, "out_db"),
    "r_amp_db": ("add", "dB", -24, 24, "R", "amp", CAPTURE, "out_db"),
    "r_cab_db": ("add", "dB", -24, 24, "R", "cab", CAPTURE, "out_db"),
    **{c: ("add", "dB", -24, 24, "LR", END, CAPTURE, i) for i, c in enumerate(EQ_COLUMNS)},
    "boost_on": ("set", "0/1", 0, 1, "L", "boost", CAPTURE, "enabled"),
    "drive_on": ("set", "0/1", 0, 1, "L", "drive", CAPTURE, "enabled"),
    "echo_on": ("set", "0/1", 0, 1, "LR", "echo", ("echo",), "enabled"),
    "echo_ms": ("set", "ms", 0, 2000, "LR", "echo", ("echo",), "delay_ms"),
    "echo_fb": ("set", "0-0.8", 0, 0.8, "LR", "echo", ("echo",), "feedback"),
    "echo_mix": ("set", "0-1", 0, 1, "LR", "echo", ("echo",), "mix"),
    "echo_cutoff": ("set", "Hz", 200, 20000, "LR", "echo", ("echo",), "cutoff_hz"),
    "amb_mix": ("set", "0-1", 0, 1, "LR", "ambience", ("ir",), "mix"),
    "gate_db": ("set", "dB", -100, 0, "", None, (), "gateThreshold"),
    "gate_hold": ("set", "ms", 0, 200, "", None, (), "gateHold"),
    "gate_release": ("set", "ms", 5, 500, "", None, (), "gateRelease"),
    "gate_range": ("set", "dB", 20, 80, "", None, (), "gateRange"),
    "bass": ("set", "0-10", 0, 10, "", None, (), "toneBass"),
    "mid": ("set", "0-10", 0, 10, "", None, (), "toneMid"),
    "treble": ("set", "0-10", 0, 10, "", None, (), "toneTreble"),
}
FIELD_DEFAULTS = {"out_db": 0.0, "in_db": 0.0, "enabled": True, "mix": 1.0, "delay_ms": 0,
                  "feedback": 0.5, "cutoff_hz": 3500.0}  # what build_block assumes when a field is absent
TAG_RE = re.compile(r"[a-z0-9][a-z0-9-]*")
# `csv --init`'s first guess at the song presets' tags (FCB bank name, gain character); scene
# presets get theirs from `bank`, `scene` and name, split rigs get "stereo". Tags then live in tone.csv.
BANK_TAGS = {0: "maiden", 1: "80s", 2: "80s-clean", 3: "variety", 4: "modern"}  # = rig.py BANK_NAMES
SONG_TAGS = {
    "03-van-halen-1": "80s crunch", "04-van-halen-1984": "80s crunch",
    "05-def-leppard-pyromania": "80s crunch", "06-def-leppard-hysteria": "80s crunch",
    "07-u2-streets": "80s clean", "08-comfortably-numb": "variety lead", "09-radiohead": "variety crunch",
    "10-nirvana": "variety heavy", "11-djent": "variety heavy", "12-purple-rain": "variety clean",
    "13-satan-full-rig": "modern heavy", "14-satan-50-modern": "modern heavy",
    "15-satan-50-low-tuned": "modern heavy", "16-satan-50-lead": "modern heavy", "17-stormblade": "modern heavy",
}


class ToneError(ValueError):
    def __init__(self, errors):
        self.errors = list(errors)
        super().__init__("\n".join(self.errors))


def end_block(chain):
    """A chain's last capture: its cab, or its amp when the cab slot is empty (full rigs)."""
    for role in ("cab", "amp"):
        if hit := [b for b in chain if b.get("role") == role and b.get("type") in CAPTURE]:
            return hit[-1]
    return None


def tone_targets(rig, col):
    """The blocks a tone.csv column sets in this preset ([] for a param column)."""
    _, _, _, _, chains, role, types, _ = TONE_COLUMNS[col]
    out = []
    for side in ("left", "right"):
        chain = rig.get(side, [])
        if side[0].upper() not in chains:
            continue
        if role == END:
            out += [b] if (b := end_block(chain)) else []
        else:
            out += [b for b in chain if b.get("role") == role and b.get("type") in types]
    return out


def block_value(b, field):
    if isinstance(field, int):  # an eq band
        return (b.get("eq") or [0.0] * len(EQ_BANDS))[field]
    return b.get(field, FIELD_DEFAULTS[field])


def set_block_value(b, field, v):
    if isinstance(field, int):
        if b.get("eq_pre"):
            raise ValueError("its EQ runs before the model (eq_pre); edit that voicing in the JSON")
        b["eq"] = list(b.get("eq") or [0.0] * len(EQ_BANDS))
        b["eq"][field] = v
    elif field == "enabled":
        b[field] = bool(v)
    else:
        b[field] = int(round(v)) if field == "delay_ms" else v


def parse_number(text):
    """A tone.csv cell: None if empty, an int if written as one (so 375 stays 375), else a float."""
    t = text.strip()
    if not t:
        return None
    v = float(t)
    if not math.isfinite(v):
        raise ValueError
    return int(t) if re.fullmatch(r"[+-]?\d+", t) else v


def format_number(v):
    v = round(float(v), 6)
    return str(int(v)) if v.is_integer() else repr(v)


def empty_sheet():
    return {"star": {}, "tag_rows": [], "rows": {}, "tags": {}, "line": {}, "errors": []}


def tone_sheet(path=None):
    """Read rigs/tone.csv -> {"star": {col: v}, "tag_rows": [(tag, {col: v}, line)],
    "rows": {stem: {col: v}}, "tags": {stem: [tag]}, "line": {row key: line}, "errors": [...]}.
    Every error names the line, the row and the column. No file = an empty sheet."""
    path = Path(path or TONE_CSV)
    sheet = empty_sheet()
    if not path.exists():
        return sheet
    errors, stems = sheet["errors"], {f.stem for f in RIGS.glob("*.json")}
    lines = list(csv.reader(path.read_text().splitlines()))
    header = [h.strip() for h in (lines[0] if lines else [])]
    if header[:1] != ["preset"]:
        return sheet | {"errors": [f"{path.name} line 1: the first column must be 'preset'"]}
    for c in header[1:]:
        if c != "tags" and c not in TONE_COLUMNS:
            errors.append(f"{path.name} line 1, column '{c}': unknown column (known: tags, {', '.join(TONE_COLUMNS)})")
        elif header.count(c) > 1:
            errors.append(f"{path.name} line 1, column '{c}': appears twice")
    for n, cells in enumerate(lines[1:], 2):
        key = cells[0].strip() if cells else ""
        if not key or key.startswith("#"):  # blank or comment row (the `#rule` row)
            continue
        where = f"{path.name} line {n} ({key})"
        if key.startswith("@") and not TAG_RE.fullmatch(key[1:]):
            errors.append(f"{where}: a tag row is @ + lowercase letters, digits and dashes")
            continue
        if key != "*" and not key.startswith("@") and key not in stems:
            errors.append(f"{where}: no rigs/{key}.json")
            continue
        if key in sheet["line"]:
            errors.append(f"{where}: duplicate row (first on line {sheet['line'][key]})")
            continue
        if len(cells) > len(header):
            errors.append(f"{where}: {len(cells)} cells but {len(header)} columns")
        row, tags = {}, []
        for col, text in zip(header[1:], cells[1:]):
            if col == "tags":
                tags = text.split()
                if tags and (key == "*" or key.startswith("@")):
                    errors.append(f"{where}, column tags: only preset rows carry tags")
                for t in tags:
                    if not TAG_RE.fullmatch(t):
                        errors.append(f"{where}, column tags: '{t}' is not lowercase letters, digits and dashes")
                continue
            if col not in TONE_COLUMNS:
                continue
            try:
                v = parse_number(text)
            except ValueError:
                errors.append(f"{where}, column {col}: '{text.strip()}' is not a number")
                continue
            if v is None:
                continue
            _, unit, lo, hi = TONE_COLUMNS[col][:4]
            if unit == "0/1" and v not in (0, 1):
                errors.append(f"{where}, column {col}: {text.strip()} must be 0 or 1")
            elif not lo <= v <= hi:
                errors.append(f"{where}, column {col}: {text.strip()} is outside {lo}..{hi} {unit}")
            else:
                row[col] = v
        sheet["line"][key] = n
        if key == "*":
            sheet["star"] = row
        elif key.startswith("@"):
            sheet["tag_rows"].append((key[1:], row, n))
        else:
            sheet["rows"][key], sheet["tags"][key] = row, tags
    carried = {t for ts in sheet["tags"].values() for t in ts}
    for tag, _, n in sheet["tag_rows"]:
        if tag not in carried:
            errors.append(f"{path.name} line {n} (@{tag}): no preset carries the tag '{tag}'")
    return sheet


def apply_tone(rig, sheet):
    """Resolve the tone sheet into a rig (in place, and returned): JSON -> `*` -> `@tag` rows ->
    the preset's row. "add" columns: the preset cell replaces the JSON value, and the `*` and
    `@tag` cells add to it; out_db is a fader with no JSON value of its own, so its preset cell
    adds too. "set" columns: the preset cell, else the last `@tag` cell, else `*`, else the JSON.
    Param columns land in rig["tone_params"], which build_preset applies last. Raises ToneError
    naming the row and column."""
    stem = Path(rig.get("_file", "")).stem
    row, tags = sheet["rows"].get(stem, {}), set(sheet["tags"].get(stem, ()))
    layers = [sheet["star"], *(r for tag, r, _ in sheet["tag_rows"] if tag in tags)]
    where = f"{TONE_CSV.name} line {sheet['line'].get(stem, '?')} ({stem})"
    errors, params = [], dict(rig.get("tone_params", {}))
    # out_db last: it's a fader on the end block, which amp_db/cab_db (preset cells replace)
    # also write, so it must add on top of what they resolve to, not be overwritten by them.
    for col, (rule, unit, lo, hi, _, role, _, field) in sorted(TONE_COLUMNS.items(), key=lambda kv: kv[0] == "out_db"):
        cell = row.get(col)
        inherited = [layer[col] for layer in layers if layer.get(col) is not None]
        default = cell if cell is not None else inherited[-1] if inherited else None  # "set" rule
        offset = round(sum(inherited), 6)  # "add" rule
        if col == "out_db":
            rig["_out_db"] = round((cell or 0) + offset, 6)
        if role is None:
            if default is not None:
                params[field] = float(default)
            continue
        targets = tone_targets(rig, col)
        if cell is not None and not targets:
            errors.append(f"{where}, column {col}: this preset has no {role} block to set")
            continue
        for b in targets:
            if col == "out_db":
                v = round(block_value(b, field) + rig["_out_db"], 3) if rig["_out_db"] else None
            elif rule == "add":
                base = cell if cell is not None else block_value(b, field)
                v = round(base + offset, 3) if offset else base if cell is not None else None
            else:
                v = default
            if v is None:
                continue
            if not lo <= v <= hi:
                errors.append(f"{where}, column {col}: resolves to {v:g} on {b.get('role')} "
                              f"'{b.get('label', b.get('type'))}', outside {lo}..{hi} {unit}")
                continue
            try:
                set_block_value(b, field, v)
            except ValueError as e:
                errors.append(f"{where}, column {col}: {b.get('role')} '{b.get('label', '')}': {e}")
    if errors:
        raise ToneError(errors)
    rig["tone_params"] = params
    return rig


def tone_settings(rig):
    """{column: value} a resolved rig plays with. A value is a list (left chain first) where the
    chains differ, and None where the preset has no such block."""
    params, out = preset_params(rig), {}
    for col, (_, _, _, _, _, role, _, field) in TONE_COLUMNS.items():
        if role is None:
            out[col] = params[field]
            continue
        vals = [block_value(b, field) for b in tone_targets(rig, col)]
        if col == "out_db":
            vals = [rig.get("_out_db", 0)] if vals else []
        vals = [int(v) if isinstance(v, bool) else v for v in vals]
        out[col] = None if not vals else vals[0] if all(v == vals[0] for v in vals) else vals
    return out


def rig_stem(rig):
    return Path(rig["_file"]).stem if rig["_file"].endswith(".json") else None  # None: generated


def find_rig(rigs, name):
    """A preset by file stem, name or PC number."""
    return next((r for r in rigs if str(name) in (rig_stem(r), r["name"], str(r["pc"]))), None)


def resolved(rig_name):
    """One preset's merged settings (JSON -> `*` -> `@tag` rows -> its row): the hook for an
    offline simulator and for docs. rig_name: file stem ("00-maiden-heavy"), name or PC number.
    -> {"preset": stem, "name", "pc", "tags", "settings": one value per tone.csv column (see
    tone_settings), "params": every TONE3000 param the preset sets on load, "rig": the resolved
    preset, its blocks carrying their final numbers (out_db, in_db, eq, echo fields, enabled)}."""
    sheet = tone_sheet()
    rig = find_rig(load_rigs(sheet), rig_name)
    if not rig:
        raise KeyError(f"no preset {rig_name!r} (a rigs/*.json stem, a preset name or a PC number)")
    return {"preset": rig_stem(rig), "name": rig["name"], "pc": rig["pc"],
            "tags": sheet["tags"].get(rig_stem(rig), []), "settings": tone_settings(rig),
            "params": preset_params(rig), "rig": rig}


def affected(target, sheet=None):
    """Names of the presets a tune target reaches, in PC order: "*" = all, "@tag" = presets
    carrying the tag, else one preset (stem, name or PC). A generated harmony preset follows the
    heavy preset of its bank, because it is built from that preset's resolved blocks."""
    sheet = sheet or tone_sheet()
    rigs = load_rigs(sheet)
    if target == "*":
        hit = rigs
    elif target.startswith("@"):
        hit = [r for r in rigs if target[1:] in sheet["tags"].get(rig_stem(r) or "", ())]
    else:
        hit = [r] if (r := find_rig(rigs, target)) else []
    heavy_banks = {r["bank"] for r in hit if r.get("scene") == "heavy"}
    hit += [r for r in rigs if r.get("scene") == "harmony" and r["bank"] in heavy_banks and r not in hit]
    return [r["name"] for r in sorted(hit, key=lambda r: r["pc"])]


def build_presets(names, out_dir):
    """Build just these presets (names, e.g. from affected()) into out_dir as <id>.t3kpreset,
    the files `build` would write, without touching ~/.config/TONE3000. -> {name: path}."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    paths = {}
    for rig in load_rigs():
        if rig["name"] in names:
            paths[rig["name"]] = out / f"{preset_id(rig['name'])}.t3kpreset"
            atomic_write(paths[rig["name"]], dump_preset(build_preset(rig)))
    return paths


def init_tags(rig):
    stem, tags = rig_stem(rig), []
    if rig.get("scene"):
        tags += [BANK_TAGS.get(rig.get("bank"), f"bank{rig.get('bank')}"), rig["scene"]]
    tags += SONG_TAGS.get(stem, "").split()
    tags += [w for w in ("clean", "acoustic", "lead") if w in rig["name"].lower().split()]
    tags += ["stereo"] if rig.get("split_after") else []
    return list(dict.fromkeys(tags))


def tone_csv_init(force=False):
    """Write rigs/tone.csv from the JSONs as they are, so JSON + CSV builds what JSON alone did.
    `*`: 0 for every "add" column, today's global value for each param column. A preset cell is
    written where it differs from what `*` gives it; where its two chains differ, it stays empty
    and the JSON keeps the numbers (listed on stdout). Tags: init_tags."""
    if TONE_CSV.exists() and not force:
        sys.exit(f"{TONE_CSV} exists; --force overwrites it (and every tuning in it)")
    rigs = [apply_tone(json.loads(f.read_text()) | {"_file": f.name}, empty_sheet()) for f in RIGS.glob("*.json")]
    rigs.sort(key=lambda r: r["pc"])
    plain = preset_params({})
    star = {c: (0 if rule == "add" else plain[f] if role is None else None)
            for c, (rule, _, _, _, _, role, _, f) in TONE_COLUMNS.items()}
    buf = io.StringIO()
    w = csv.writer(buf, lineterminator="\n")
    w.writerow(["preset", "tags", *TONE_COLUMNS])
    w.writerow(["#rule", "words", *(f"{r} {u}" for r, u, *_ in TONE_COLUMNS.values())])
    w.writerow(["*", "", *("" if v is None else format_number(v) for v in star.values())])
    for rig in rigs:
        cells = []
        for col, v in tone_settings(rig).items():
            if isinstance(v, list):
                print(f"{rig_stem(rig)} {col}: chains differ {v}, kept in the JSON")
                v = None
            if v is not None and TONE_COLUMNS[col][0] == "add" and v == 0:
                v = None
            if v is not None and TONE_COLUMNS[col][0] == "set" and v == star[col]:
                v = None
            cells.append("" if v is None else format_number(v))
        w.writerow([rig_stem(rig), " ".join(init_tags(rig)), *cells])
    TONE_CSV.write_text(buf.getvalue())
    print(f"wrote {TONE_CSV} ({len(rigs)} presets, {len(TONE_COLUMNS)} columns)")


def show_value(v):
    return "-" if v is None else "/".join(map(format_number, v)) if isinstance(v, list) else format_number(v)


def cli_csv(args):
    """csv --init [--force]: write rigs/tone.csv from the JSONs. csv: print what every preset
    resolves to (L/R where its chains differ, - where it has no such block)."""
    if "--init" in args:
        return tone_csv_init(force="--force" in args)
    rows = [["preset", *TONE_COLUMNS]]
    rows += [[rig_stem(r) or r["name"], *map(show_value, tone_settings(r).values())] for r in load_rigs()]
    widths = [max(len(r[i]) for r in rows) for i in range(len(rows[0]))]
    for r in rows:
        print("  ".join(c.ljust(w) for c, w in zip(r, widths)).rstrip())


# --- tune: say it ("make all cleans fatter") -> one tone.csv edit -----------------------------

def read_vocab(path=None):
    """rigs/vocab.csv -> ({word: ([(column, step)], why)}, errors). moves: "eq_250 +1.5; eq_100 +1"."""
    path = Path(path or VOCAB_CSV)
    vocab, errors = {}, []
    if not path.exists():
        return vocab, errors
    for n, row in enumerate(csv.DictReader(path.read_text().splitlines()), 2):
        word = (row.get("word") or "").strip()
        if not word or word.startswith("#"):
            continue
        moves = []
        for part in (row.get("moves") or "").split(";"):
            col, _, step = part.strip().partition(" ")
            try:
                step = float(step)
            except ValueError:
                errors.append(f"{path.name} line {n} ({word}): '{part.strip()}' is not '<column> <step>'")
                continue
            if col not in TONE_COLUMNS:
                errors.append(f"{path.name} line {n} ({word}): unknown column {col}")
            else:
                moves.append((col, step))
        if word in vocab:
            errors.append(f"{path.name} line {n} ({word}): duplicate word")
        vocab[word] = (moves, (row.get("why") or "").strip())
    return vocab, errors


def tune_row_key(target, sheet, rigs):
    if target == "*":
        return "*"
    if target.startswith("@"):
        if not any(target[1:] in t for t in sheet["tags"].values()):
            sys.exit(f"no preset carries the tag '{target[1:]}' (tags: "
                     f"{', '.join(sorted({t for ts in sheet['tags'].values() for t in ts}))})")
        return target
    rig = find_rig(rigs, target)
    if not rig or not rig_stem(rig):
        sys.exit(f"no preset {target!r}: give @tag, * or a rigs/*.json stem, name or PC")
    return rig_stem(rig)


def tune(target, word, times=1):
    """Apply a vocab move to one row of tone.csv ("*", "@tag" or a preset); return the old text.
    "add" columns: a `*`/`@tag` cell moves its offset; an empty preset cell starts from the
    preset's JSON value. "set" columns start from the row's cell, else from the value every
    affected preset resolves to today (skipped, with a note, where they differ)."""
    vocab, errors = read_vocab()
    if errors:
        sys.exit("\n".join(errors))
    word = word.strip().lower().replace("_", " ").replace("-", " ") if word not in vocab else word
    if word not in vocab:
        sys.exit(f"unknown word {word!r}; known: {', '.join(vocab)}")
    old_text = TONE_CSV.read_text()
    sheet = tone_sheet()
    if sheet["errors"]:
        sys.exit("\n".join(sheet["errors"]))
    rigs = load_rigs(sheet)
    key = tune_row_key(target, sheet, rigs)
    names = affected(key, sheet)
    now = {r["name"]: tone_settings(r) for r in rigs if r["name"] in names}
    json_rig = None
    if key not in ("*",) and not key.startswith("@"):
        json_rig = apply_tone(json.loads((RIGS / f"{key}.json").read_text()) | {"_file": f"{key}.json"},
                              empty_sheet())
    lines = list(csv.reader(old_text.splitlines()))
    header = lines[0]
    idx = next((i for i, cells in enumerate(lines) if cells and cells[0].strip() == key), None)
    if idx is None:  # a new @tag row goes after `*` and the other tag rows; a preset row at the end
        idx = (max(i for i, c in enumerate(lines) if c and (c[0].strip() == "*" or c[0].startswith(("@", "#"))))
               + 1 if key.startswith("@") else len(lines))
        lines.insert(idx, [key])
    cells = lines[idx] + [""] * (len(header) - len(lines[idx]))
    for col, step in vocab[word][0]:
        if col not in header:
            sys.exit(f"tone.csv has no column {col}; add it to the header")
        i = header.index(col)
        rule, unit, lo, hi = TONE_COLUMNS[col][:4]
        cur, delta = parse_number(cells[i]), step * times
        if rule == "add" and (key == "*" or key.startswith("@") or col == "out_db"):
            base = cur or 0
        elif cur is not None:
            base = cur
        else:  # empty cell: an "add" preset cell starts from its JSON value, a "set" cell from today's
            vals = [tone_settings(json_rig)[col]] if rule == "add" else [s[col] for s in now.values()]
            if any(v is None or isinstance(v, list) or v != vals[0] for v in vals):
                print(f"  skip {col}: the presets differ (or lack the block) there; tune them one by one")
                continue
            base = vals[0]
        new = round(base + delta, 2)
        if rule == "set":
            new = max(lo, min(hi, new))
        blank = new == 0 and rule == "add" and key.startswith("@")
        cells[i] = "" if blank else format_number(new)
    lines[idx] = cells
    buf = io.StringIO()
    csv.writer(buf, lineterminator="\n").writerows(lines)
    TONE_CSV.write_text(buf.getvalue())
    try:
        load_rigs()
    except ToneError as e:
        TONE_CSV.write_text(old_text)
        sys.exit("tune left tone.csv unchanged:\n" + str(e))
    return old_text, names


def print_diff(before, after):
    changed = 0
    for name in after:
        diffs = [f"{c} {show_value(before[name][c])} -> {show_value(v)}"
                 for c, v in after[name].items() if v != before.get(name, {}).get(c)]
        if diffs:
            changed += 1
            print(f"  {name}: " + ", ".join(diffs))
    print(f"{changed} preset(s) changed")


def cli_tune(args):
    """tune <@tag|preset|*> <word...> [xN] [--apply] [--no-sim] | tune --undo [--apply]
    Edits tone.csv, then renders the presets it touched before -> after (rigsim.py, ~2 s).
    --apply rewrites those presets in the live TONE3000 folder: the running rig picks each one
    up on its next Program Change (TONE3000 rereads the file on every PC, experiments D19)."""
    flags = {a for a in args if a in ("--apply", "--no-sim")}
    args = [a for a in args if a not in flags]
    history = json.loads(TUNE_HISTORY.read_text()) if TUNE_HISTORY.exists() else []
    before = {r["name"]: tone_settings(r) for r in load_rigs()}
    if args[:1] == ["--undo"]:
        if not history:
            sys.exit("nothing to undo")
        last = history.pop()
        TONE_CSV.write_text(last["csv"])
        atomic_write(TUNE_HISTORY, json.dumps(history, indent=1).encode())
        print(f"undid: tune {last['target']} {last['word']} x{last['times']}")
        names = affected(last["target"])
    else:
        times = next((int(a[1:]) for a in args[1:] if re.fullmatch(r"x\d+", a)), 1)
        word = " ".join(a for a in args[1:] if not re.fullmatch(r"x\d+", a))
        if not args or not word:
            vocab, _ = read_vocab()
            sys.exit("usage: tone3000.py tune <@tag|preset|*> <word> [xN] | tune --undo\nwords: "
                     + ", ".join(vocab))
        old_text, names = tune(args[0], word, times)
        history = (history + [{"target": args[0], "word": word, "times": times, "csv": old_text}])[-30:]
        atomic_write(TUNE_HISTORY, json.dumps(history, indent=1).encode())
        print(f"tune {args[0]} {word} x{times}: {len(names)} preset(s) in reach")
    after = {r["name"]: tone_settings(r) for r in load_rigs()}
    print_diff(before, after)
    if "--apply" in flags:
        live = {n: p for n, p in ((n, PRESETS / f"{preset_id(n)}.t3kpreset") for n in names) if p.exists()}
        if missing := sorted(set(names) - set(live)):
            print(f"not live yet (run `build`): {', '.join(missing)}")
        build_presets(list(live), PRESETS)
        print(f"applied {len(live)} preset(s) live: stomp (or re-select) to hear them")
    if "--no-sim" not in flags and names:
        sys.stdout.flush()
        subprocess.run(["uv", "run", "--quiet", str(Path(__file__).with_name("rigsim.py")), *names, "--before", "last" if args[:1] == ["--undo"] else "undo"])


PACKS = Path.home() / "Music/fcb-rig/packs"  # drop folder for local pack files (never committed)
LOCAL_TONE_ID = 900100000  # local-only tone ids for local files (never hit the API)


def local_path(spec):
    return Path(os.path.expanduser(spec["file"]))


def local_tone(spec):
    """(tone record, model id, file bytes) for a block's local `file` (.nam or IR .wav)."""
    path = local_path(spec)
    if not path.is_file():
        raise FileNotFoundError(f"{path}: local file missing. Save it there, or remove the block's "
                                f"\"file\" field to use its catalog tone_id/model_id (rigs/README.md, Local files)")
    raw = path.read_bytes()
    mid = 2_000_000_000 + zlib.crc32(raw) % 100_000_000  # stable per file content, inside int32
    title = spec.get("label") or path.stem
    tone = synth_tone(mid, title, f"local file {path.name}", gear=spec.get("role", "amp"))
    tone.update(id=LOCAL_TONE_ID + mid % 100000, format=spec["type"], license=None,
                user={"username": "local"})
    if spec["type"] == "nam":  # A2 files are a SlimmableContainer; A1 is a bare WaveNet/LSTM.
        # Read the key: newer trainers (0.7.0, e.g. Stormblade A2) write "metadata" before it.
        arch = json.loads(raw).get("architecture")
        tone["models"][0]["architecture_version"] = 2 if arch == "SlimmableContainer" else 1
    return tone, mid, raw


def model_of(tone_id, model_id):
    return next(m for m in tone_record(tone_id)["models"] if m["id"] == model_id)


def build_block(spec):
    """One chain block from a rigs/*.json block spec."""
    kind = spec["type"]
    if os.environ.get("T3K_NO_LONG_IR") and (kind == "echo" or spec.get("role") == "ambience"):
        return block("insert")  # diagnostic: drop long IRs
    on = spec.get("enabled", True)
    mix = float(spec.get("mix", 1.0))
    if kind == "insert":
        return block("insert")
    if kind == "echo":  # generated analog-style echo IR (+ optional reverb tail)
        ms = int(spec.get("delay_ms", 0))
        fb, cut_hz = float(spec.get("feedback", 0.5)), float(spec.get("cutoff_hz", 3500.0))
        mid = ms * 100 + int(fb * 10) + int(cut_hz / 1000) * 1000000
        rev = spec.get("reverb")
        if rev:  # distinct model id per delay+reverb combination, kept inside int32 (1e9..2e9)
            key = json.dumps([ms, fb, cut_hz, rev], sort_keys=True).encode()
            mid = 1_000_000_000 + zlib.crc32(key) % 1_000_000_000
        title = spec.get("label") or (f"Analog echo {ms} ms" if ms else "Reverb")
        what = (f"{ms} ms BBD-style echo, feedback {fb}" if ms else "reverb") + (" + reverb" if rev and ms else "")
        return block("ir", on, synth_tone(mid, f"{title} (generated)", f"{what}, wet only"),
                     mid, mix=mix, data=analog_echo(ms, fb, cutoff_hz=cut_hz, reverb=rev and reverb_tail(rev)))
    if spec.get("file"):  # a local model/IR file (a pack outside the catalog) replaces tone_id/model_id
        tone, mid, raw = local_tone(spec)
    else:
        tone, mid = tone_record(spec["tone_id"]), spec["model_id"]
        raw = model_file(model_of(spec["tone_id"], mid)) if kind == "ir" else None
    data = raw if spec.get("file") else None  # catalog files: block() reads them from the cache
    if kind == "ir":
        if spec.get("trim_seconds") or not wav_loads_in_tone3000(raw):
            seconds = float(spec.get("trim_seconds") or 60.0)
            if spec.get("role") == "ambience":
                seconds = min(seconds, IR_TAIL_MAX_S)
            data = trimmed_ir(raw, seconds)
    node = block(kind, on, tone, mid, mix=mix, eq_gains=spec.get("eq"),
                 eq_pre=spec.get("eq_pre", False), data=data)
    if spec.get("out_db"):  # level trim (measured, see rig "levels"); block output is +-24 dB
        node.set("outputGain", max(0.0, min(1.0, 0.5 + float(spec["out_db"]) / 48)))
    if spec.get("in_db"):  # drive the block harder/softer (e.g. a lead chain = same amp, pushed)
        node.set("inputGain", max(0.0, min(1.0, 0.5 + float(spec["in_db"]) / 48)))
    return node


# A preset's optional `chorus` block: the DAW's LSP chorus (qtractor_rig.CHORUS, SW7 in the 80s
# banks) set per song through CC 89-91. Ranges are the plugin's ports; mix is the wet share.
CHORUS_LIMITS = {"rate_hz": (0.01, 20.0), "depth_ms": (0.1, 20.0), "mix": (0.0, 1.0)}


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
            if spec.get("type") in ("nam", "ir") and not (spec.get("tone_id") and spec.get("model_id")
                                                         or spec.get("file")):
                errs.append(f"{side}[{i}] needs tone_id and model_id (or a local file)")
            if spec.get("file") and not local_path(spec).is_file():
                errs.append(f"{side}[{i}] local file missing: {local_path(spec)}")
    if rig.get("right") and not rig.get("split_after"):
        errs.append("right chain needs split_after (1-based left slot)")
    if rig.get("scene") not in (None, *SCENE_KINDS):
        errs.append(f"scene must be one of {SCENE_KINDS}")
    if rig.get("scene") and not isinstance(rig.get("bank"), int):
        errs.append("scene presets need their FCB bank number")
    for key, value in rig.get("chorus", {}).items():  # the DAW chorus (qtractor_rig.CHORUS), not TONE3000
        lo, hi = CHORUS_LIMITS.get(key, (None, None))
        if lo is None and key not in ("note", "sources"):
            errs.append(f"chorus.{key}: unknown (known: {', '.join(CHORUS_LIMITS)}, note, sources)")
        elif lo is not None and not (isinstance(value, (int, float)) and lo <= value <= hi):
            errs.append(f"chorus.{key} = {value!r}: must be a number in {lo}..{hi}")
    for side in ("left", "right"):
        for i, spec in enumerate(rig.get(side, []), 1):
            if spec.get("type") == "echo" and not spec.get("delay_ms") and not spec.get("reverb"):
                errs.append(f"{side}[{i}] echo needs delay_ms and/or reverb")
    if unknown := sorted(set(rig.get("params", {})) - set(BASE_PARAMS)):
        errs.append(f"unknown params {unknown} (TONE3000 ignores them)")
    if len(rig.get("left", [])) > 12 or len(rig.get("right", [])) > 12:
        errs.append("max 12 blocks per chain")
    return errs


# Applied to every preset.
GLOBAL_PARAMS = {
    # +14 dB (normalized: 0.5 = 0 dB, 48 dB span). With the interface at unity this
    # puts the guitar ~2.5 dB above loudness-normalized music (YouTube, -14 LUFS),
    # peaks ~-5 dBFS (experiments L6). +24 dB hard-clipped. EXP B no longer touches it: the
    # pedal is a DAW gain stage after both instances (qtractor_rig.VOLUME, experiments D22).
    "outputLevel": 0.5 + 12 / 48,
    "gateEnabled": 1.0,
    "gateThreshold": -60.0,    # dB; -35 dB chopped note decays and quiet playing
}


# Scene bank (Iron Maiden, FCB bank 0): every switch in a bank sends absolute
# values, so a scene always lands in the same state and never needs a reload (a
# Program Change leaves a 60-170 ms hole, experiments D5). The DAW (qtractor_rig.py)
# runs two TONE3000s: "heavy" (PC ch 1) and "clean" (PC ch 2, clean or acoustic preset).
#   CC 80 -> heavy TONE3000 inputLevel (value/127, its own MIDI map) + DAW Solo block: +2 dB and a lead echo (on > 63)
#   CC 81 -> DAW selector: 0 = heavy rig hears the guitar, 127 = clean rig does
SCENE_DRIVE_CC, SCENE_SELECT_CC = 80, 81
SCENES = {  # name: (CC 80, CC 81, preset the clean instance loads; None = send no Program Change)
    "rhythm": (63, 0, "clean"),        # unity input (63/127), Solo block off
    "solo": (72, 0, None),             # amps pushed like a boost pedal, +2 dB, lead echo
    "clean": (63, 127, "clean"),
    "acoustic": (63, 127, "acoustic"),
    "crunch": (48, 0, None),           # guitar volume rolled back (quiet heavy intros)
}
# A Program Change - even re-sending the loaded preset - re-applies the preset's params
# *after* CCs in the same burst, so a PC would wipe CC 80 (experiments D8). Presets load at
# the rhythm drive (63), so scenes that send PCs use 63; SOLO/CRUNCH send only CCs.
SCENE_KINDS = ("heavy", "clean", "acoustic", "harmony")  # harmony presets are generated


def build_preset(rig):
    left = [build_block(b) for b in rig["left"]]
    right = [build_block(b) for b in rig.get("right", [])]
    split = rig.get("split_after")
    left += [block("insert") for _ in range(max(0, 5 - len(left)))]
    right += [block("insert") for _ in range(max(0, 5 - len(right)))]
    snap = Node("ChainSnapshot").set("stereoEnabled", bool(split)).set("branchSide", "left")
    snap.set("branchAfterBlockId", left[split - 1].get("id") if split else "")
    snap.children = [Node("ChainBlocks", children=left), Node("RightChainBlocks", children=right)]
    root = Node("T3KPreset").set("schemaVersion", 1).set("name", rig["name"]).set("id", preset_id(rig["name"]))
    root.children = [snap, params_node(preset_params(rig))]
    return root


def preset_params(rig):
    """Every TONE3000 param a preset sets on load: the baseline, the JSON's `params`, the global
    params, then the tone sheet's param columns (gate, tone stack)."""
    values = {**BASE_PARAMS, "gateEnabled": 0.0, **rig.get("params", {}), **GLOBAL_PARAMS,
              **rig.get("tone_params", {})}
    if rig.get("scene") == "heavy":  # loads at the RHYTHM scene's input drive
        values["inputLevel"] = SCENES["rhythm"][0] / 127  # = every PC-sending scene's CC 80
    return values


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

    # Linux side of the gain staging: interface output at unity (the hardware
    # monitor knob is the only volume control) and a flat DI (no Air) for NAM.
    sink = next((o["id"] for o in json.loads(subprocess.run(["pw-dump"], capture_output=True, text=True).stdout)
                 if (o.get("info") or {}).get("props", {}).get("media.class") == "Audio/Sink"
                 and SINK_MATCH in (o["info"]["props"].get("node.name") or "")), None)
    if sink:
        subprocess.run(["wpctl", "set-volume", str(sink), "1.0"])
    for control, value in ALSA_CONTROLS:
        subprocess.run(["amixer", "-c", ALSA_CARD, "-q", "sset", control, value])
    print(f"linux: interface output 100% (0 dB){'' if sink else ' — sink not found'}; "
          + ", ".join(f"{c} {v}" for c, v in ALSA_CONTROLS))

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
    if [r["pc"] for r in rigs] != list(range(len(rigs))):  # PC N = Nth entry of order.json
        sys.exit(f"pc numbers must run 0..{len(rigs) - 1} without gaps: {[r['pc'] for r in rigs]}")
    ids = []
    for rig in rigs:
        pid = preset_id(rig["name"])
        atomic_write(PRESETS / f"{pid}.t3kpreset", dump_preset(build_preset(rig)))
        ids.append(f"user:{pid}")
        print(f"PC {rig['pc']:<3} {rig['name']}")
    # Saving over a rig preset in the app renames its file to the display name (same id inside).
    # Two files with one id both list, which would shift every Program Change after them.
    ours = {i.removeprefix("user:") for i in ids}
    for path in PRESETS.glob("*.t3kpreset"):
        if path.stem not in ours and preset_header(path)[0] in ours:
            path.unlink()
            print(f"removed {path.name}: an app-saved copy of a rig preset (rebuilt from rigs/)")
    order_path = PRESETS / "order.json"
    old = json.loads(order_path.read_text()) if order_path.exists() else []
    factory = [f"factory:{preset_header(p)[0]}" for p in sorted((PRESETS / "Factory").glob("*.t3kpreset"))]
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
    if spec.get("file"):
        label += f" [local file {local_path(spec).name}]"
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
    sheet = tone_sheet()
    vocab, vocab_errors = read_vocab()
    for e in sheet["errors"] + vocab_errors:
        print(e)
    ok = not (sheet["errors"] or vocab_errors)
    if ok:
        print(f"{TONE_CSV.name}: ok ({len(sheet['rows'])} preset rows, {len(sheet['tag_rows'])} tag rows, "
              f"{len(TONE_COLUMNS)} columns); {VOCAB_CSV.name}: ok ({len(vocab)} words)")
    for f in files or sorted(RIGS.glob("*.json")):
        rig = json.loads(Path(f).read_text()) | {"_file": Path(f).name}
        errs = validate(rig)
        try:
            apply_tone(rig, sheet)
        except ToneError as e:
            errs += e.errors
        if not errs:
            try:
                size = len(dump_preset(build_preset(rig)))
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
                elif spec.get("file") and not spec.get("tone_id"):
                    what = f"{spec.get('label') or local_path(spec).stem} — local file `{spec['file']}`"
                else:
                    tone = tone_record(spec["tone_id"])
                    label = spec.get("label") or model_of(spec["tone_id"], spec["model_id"])["name"]
                    what = f"[{tone['title']}](https://www.tone3000.com/tones/{spec['tone_id']}) — {label}"
                    if spec.get("file"):
                        what += f" (replaced by local file `{spec['file']}`)"
                if spec.get("mix", 1.0) != 1.0:
                    what += f" (mix {spec['mix']:.0%})"
                state = "on" if spec.get("enabled", True) else "off"
                out.append(f"| {slot_names[side]}{i} | {spec.get('role', '')} | {what} | {state} |")
        if r.get("split_after"):
            out += ["", f"Slots L1–L{r['split_after']} feed both rigs; left and right are panned apart."]
        if c := r.get("chorus"):
            out += ["", f"*Chorus (DAW, SW7 in the 80s banks):* {c.get('rate_hz', 0.6)} Hz, {c.get('depth_ms', 4.0)} ms, "
                        f"{c.get('mix', 0.5):.0%} wet" + (f" — {c['note']}" if c.get("note") else "")]
        if r.get("notes"):
            out += ["", f"*Notes:* {r['notes']}"]
        if r.get("sources"):
            out += ["", "*Sources:* " + " · ".join(f"<{u}>" for u in r["sources"])]
    out += ["", "## Footswitches", "", "| FCB1010 | CC | TONE3000 |", "|---|---|---|",
            "| SW1–5 (song banks 01–04) | PC | presets above |",
            "| SW6 | 20 | wah (DAW) / noise gate (standalone) |", "| SW7 | 21 | octaver (DAW) / stereo spread (standalone); 80s banks: CC 31 = chorus (DAW) |",
            "| SW8 | 26 | LEAD: slot 1 boost + slot 5 / R3 echo together |", "| SW9 | 23 | slot 2: drive |",
            "| SW10 | 28 | TUNER: mutes the rig and opens the tuner (DAW helper) |",
            "| EXP A | 27 | wah sweep (DAW) / treble (standalone) |", "| EXP B | 7 | volume (DAW stage before the limiter, not a preset param) |", "",
            "## Maiden scene bank (FCB bank 00, DAW rig)", "",
            "The Maiden bank has five scene switches. Each switch sends absolute values, so it",
            "switches instantly and always lands in the same state:", "",
            "| Switch | Scene | Heavy PC (ch 1) | Clean PC (ch 2) | CC 80 drive | CC 81 rig |", "|---|---|---|---|---|---|"]
    for scene, (drive, select, kind) in SCENES.items():
        switch = {"rhythm": 1, "solo": 2, "clean": 3, "acoustic": 4, "crunch": 5}[scene]
        out.append(f"| SW{switch} | {scene.upper()} | {'song' if kind else '—'} | {kind or '—'} | {drive} | "
                   f"{'clean' if select else 'heavy'} |")
    banks = {}
    for r in rigs:
        if r.get("scene"):
            banks.setdefault(r["bank"], {})[r["scene"]] = r
    out += ["", "In this bank SW8 = The Evil That Men Do echo (CC 24, slot 5), SW9 = Can I Play with Madness echo (CC 30, slot 6),",
            "SW10 = TUNER (CC 28). SOLO is labelled LEAD on the board. SW7 is HARMONY (CC 25): the partner guitarist a diatonic third above, in the",
            "song's key. On a bank's first load the DAW helper sets the song's solo echo time and harmony scale.",
            "", "| Bank | Heavy | Clean | Acoustic | Harmony (ch 3) | Song | Scale | Solo echo |",
            "|---|---|---|---|---|---|---|---|"]
    for b, p in sorted(banks.items()):
        if {"heavy", "clean", "acoustic", "harmony"} <= set(p):
            song = p["heavy"].get("song", {})
            scale = song.get("harmony_scale_note", "").split(" (")[0].split(";")[0]
            out.append(f"| 0{b} | PC {p['heavy']['pc']} {p['heavy']['name']} | PC {p['clean']['pc']} | "
                       f"PC {p['acoustic']['pc']} | PC {p['harmony']['pc']} | {song.get('reference', '')} | "
                       f"{scale} | {song.get('solo_delay_ms', '')} ms |")
    (RIGS / "RIGS.md").write_text("\n".join(out) + "\n")
    print(f"wrote {RIGS / 'RIGS.md'} ({len(rigs)} presets)")


if __name__ == "__main__":
    cmd, args = (sys.argv[1], sys.argv[2:]) if len(sys.argv) > 1 else ("map", [])
    commands = {
        "map": lambda: show_map(), "build": lambda: build(), "configure": lambda: configure(),
        "search": lambda: cli_search(args), "models": lambda: cli_models(args),
        "check": lambda: cli_check(args), "docs": lambda: cli_docs(args), "csv": lambda: cli_csv(args),
        "tune": lambda: cli_tune(args),
    }
    commands.get(cmd, lambda: sys.exit(__doc__))()
