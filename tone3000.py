# /// script
# requires-python = ">=3.10"
# ///
"""Build the FCB1010 rig for the TONE3000 plugin (NAM captures + IRs).

Usage:
  uv run tone3000.py map     Show the rig: PC -> preset, blocks, MIDI map
  uv run tone3000.py build   Download captures, write the 15 presets (PC order)
                             and the FCB1010 MIDI mappings. TONE3000 must NOT
                             be running (the standalone saves state on exit).
  uv run tone3000.py configure
                             Standalone settings for this hardware: JACK at
                             48 kHz/128, guitar input, FCB MIDI input, mono
                             input, NAM input calibration, low-latency launcher.

TONE3000 has no wah/modulation/delay, so every preset uses one fixed layout
and the global MIDI map (which addresses blocks by position) works everywhere:

  Block 1  lead boost (off)   <- SW8 / CC 22
  Block 2  song drive pedal   <- SW9 / CC 23
  Block 3  full stack (amp + cab capture of the player's real rig)
  Block 4  cab IR, only when the authentic amp exists as a DI capture
  Spread (stereo widen/chorus) <- SW7 / CC 21    Noise gate <- SW6 / CC 20
  EXP A / CC 27 -> treble sweep                   EXP B / CC 7 -> output level

Program Change N loads the Nth preset in the browser, so the rig presets are
written first in Presets/order.json (PC 0-14). Captures come from the public
TONE3000 catalog; model files are public storage objects.
"""

import base64
import html
import json
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

# One shared clean boost for leads: (tone_id, model_id)
BOOST = (2599, 419209)  # real silver Klon Centaur, low gain ("KLON 2")

# Authentic record/live rigs, as full-stack (amp + cab) captures.
# (pc, name, drive (tone_id, model_id), drive_on, stack (tone_id, model_id), cab (tone_id, model_id) | None, gate_on)
RIG = [
    # Big Muff Ram's Head -> 1972 Hiwatt DR103 + its Fane 4x12
    (0, "Comfortably Numb", (87219, 738628), False, (2711, 424952), None, False),
    # Boss DS-1 -> Mesa Mark (no Mark I captured: Mark VII fat clean)
    (1, "Purple Rain", (2508, 419239), False, (62659, 576013), None, False),
    # TS808 tightener -> Marshall JCM800 2203 + Marshall 4x12, high gain
    (2, "Tornado of Souls", (70280, 577277), True, (89825, 756311), None, True),
    # TS808 -> Mesa Mark VII in IIC+ mode + Mesa 4x12
    (3, "Dream Theater", (70280, 577277), True, (62659, 576017), None, True),
    # TS808 -> 90s Mesa Dual Rectifier, red modern, full rig
    (4, "Slipknot", (70280, 577277), True, (87866, 742072), None, True),
    # Tube Screamer at gain 0 -> Peavey 5150 + Mesa 4x12
    (5, "Djent", (87219, 738635), True, (32868, 418392), None, True),
    # Marshall ShredMaster -> 1987 Fender Eighty-Five (Jonny Greenwood, OK Computer)
    (6, "Radiohead", (78738, 684083), False, (75367, 668845), None, False),
    # Tube Screamer -> JCM800 2203 + Marshall 4x12, crunch
    (7, "Oasis", (87219, 738629), False, (89825, 756307), None, False),
    # Boss DS-1 -> Nirvana live-at-the-Paramount rig (Mesa Studio preamp, G12T-75 cab)
    (8, "Nirvana", (2508, 419246), False, (78100, 679829), None, False),
    # ProCo Rat -> Mesa Dual Rectifier rev G, orange crunch
    (9, "Foo Fighters", (87219, 738627), False, (79103, 682769), None, False),
    # Boss SD-1 boost -> 1980 Marshall JMP 2204 + Marshall 4x12
    (10, "Iron Maiden", (87914, 742716), False, (70521, 577106), None, False),
    # Klon -> '65 Deluxe Reverb reissue, clean full rig
    (11, "My Clean", (87219, 738638), False, (51649, 383468), None, False),
    # Tube Screamer -> Mesa Mark VII bright clean
    (12, "Petrucci Clean", (87219, 738629), False, (62659, 576000), None, False),
    # Diamond compressor -> Boss AC-3 acoustic simulator
    (13, "Acoustic", (88975, 750114), True, (71126, 588442), None, False),
    # Boss Blues Driver -> '65 Twin Reverb + G12-65, clean
    (14, "Glassy Clean", (1729, 420026), False, (35497, 381343), None, False),
]

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

MIDI_MAP = [  # (targetId, CC) — must match rig.py
    ("gateEnabled", 20),
    ("spreadEnabled", 21),
    ("block1Power", 22),
    ("block2Power", 23),
    ("toneTreble", 27),
    ("outputLevel", 7),
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
    path.write_text(json.dumps(record, indent=2))
    return record


def model_file(model):
    name = model.get("storage_name") or model["model_url"].rsplit("/", 1)[-1]
    path = CACHE / name
    if not path.exists():
        with urllib.request.urlopen(STORAGE + name, timeout=60) as resp:
            path.write_bytes(resp.read())
    return path.read_bytes()


# --- preset construction -----------------------------------------------------------

EQ_BANDS = [("lowshelf", 100.0, 0.7099999785423279), ("bell", 250.0, 1.0), ("bell", 650.0, 1.0),
            ("bell", 1600.0, 1.0), ("bell", 3500.0, 1.399999976158142),
            ("highshelf", 8000.0, 0.7099999785423279)]


def block(kind, enabled=True, tone=None, model_id=None):
    node = Node("ChainBlock").set("id", uuid.uuid4().hex).set("type", kind).set("enabled", enabled)
    node.set("normalize", True).set("slimSize", 1.0 if kind == "nam" else 0.0)
    node.set("inputGain", 0.5).set("outputGain", 0.5).set("mix", 1.0)
    if tone is None:
        return node
    model = next(m for m in tone["models"] if m["id"] == model_id)
    record = dict(tone, models=[{k: v for k, v in model.items() if k != "storage_name"}])
    node.set("toneId", tone["id"]).set("toneJson", json.dumps(record, indent=2))
    node.set("activeModelId", model_id)
    eq = Node("Eq").set("enabled", True).set("pre", False)
    eq.children = [Node("Band").set("type", t).set("freqHz", f).set("gainDb", 0.0).set("q", q)
                   for t, f, q in EQ_BANDS]
    cache = Node("ModelCache", children=[Node("CachedModel").set("modelId", model_id)
                                         .set("data", model_file(model))])
    node.children = [eq, cache]
    return node


def default_params():
    """Params node from a factory preset, so every parameter gets a sane value."""
    factory = sorted((PRESETS / "Factory").glob("*.t3kpreset"))[0]
    return load_t3kb(factory.read_bytes()).child("Params")


def preset_id(name):
    return uuid.uuid5(uuid.NAMESPACE_URL, f"fcb1010-rig/{name}").hex


def build_preset(name, drive, drive_on, amp, cab, gate_on):
    boost_tone, boost_model = BOOST
    left = [
        block("nam", False, tone_record(boost_tone), boost_model),
        block("nam", drive_on, tone_record(drive[0]), drive[1]),
        block("nam", True, tone_record(amp[0]), amp[1]),
        block("ir", True, tone_record(cab[0]), cab[1]) if cab else block("insert"),
        block("insert"),
    ]
    right = [block("insert") for _ in range(5)]
    snap = Node("ChainSnapshot").set("stereoEnabled", False).set("branchSide", "left")
    snap.set("branchAfterBlockId", "")
    snap.children = [Node("ChainBlocks", children=left), Node("RightChainBlocks", children=right)]
    params = default_params()
    for p in params.children:
        if p.get("id") == "gateEnabled":
            p.set("value", 1.0 if gate_on else 0.0)
        if p.get("id") == "gateThreshold" and gate_on:
            p.set("value", -60.0)
    root = Node("T3KPreset").set("schemaVersion", 1).set("name", name)
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
    ids = []
    for pc, name, drive, drive_on, amp, cab, gate_on in RIG:
        pid = preset_id(name)
        (PRESETS / f"{pid}.t3kpreset").write_bytes(
            dump_t3kb(build_preset(name, drive, drive_on, amp, cab, gate_on)))
        ids.append(f"user:{pid}")
        print(f"PC {pc:<3} {name}")
    order_path = PRESETS / "order.json"
    old = json.loads(order_path.read_text()) if order_path.exists() else []
    factory = [f"factory:{p.stem}" for p in sorted((PRESETS / "Factory").glob("*.t3kpreset"))]
    rest_ids = [i for i in old + factory if i not in ids]
    order_path.write_text(json.dumps(ids + list(dict.fromkeys(rest_ids)), indent=2))
    write_midi_map()
    print(f"\n{len(ids)} presets written in PC order. Start TONE3000 and stomp away.")


def show_map():
    for pc, name, drive, drive_on, amp, cab, gate_on in RIG:
        print(f"PC {pc:<3} {name:<17} drive {drive} {'on' if drive_on else 'off'} | amp {amp} | cab {cab}"
              f"{' | gate' if gate_on else ''}")
    for target, cc in MIDI_MAP:
        print(f"CC {cc:<3} -> {target}")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "map"
    {"map": show_map, "build": build, "configure": configure}.get(cmd, lambda: sys.exit(__doc__))()
