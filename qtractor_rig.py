# /// script
# requires-python = ">=3.10"
# ///
"""Run the FCB1010 + TONE3000 rig inside Qtractor — generated, never clicked.

Usage:
  python3 qtractor_rig.py build    Write ~/Music/fcb-rig/rig.qtr and patch Qtractor.conf
                                   (Qtractor must NOT be running: it rewrites its config)
  python3 qtractor_rig.py up       build + launch Qtractor on the session (quantum 256)
  python3 qtractor_rig.py down     save (SIGUSR1) and quit Qtractor
  python3 qtractor_rig.py record   start recording (MMC): the rig in stereo + the dry DI
  python3 qtractor_rig.py stop     stop the transport and list the new takes
  python3 qtractor_rig.py map      Show the chain and the footswitch map

Signal flow. The rigs sit on MIDI tracks because Qtractor only feeds MIDI to plugins on
MIDI tracks/buses, and only the CLAP build of TONE3000 gets raw Program Change:

  Scarlett In 2 ─► Heavy: [Guitar] ─► [Selector] ─► Wah ─► TONE3000 ─► Octaver ─► Solo ──────┐
                                          │ send (CC 81 > 63)                                  ├► bus Rig:
                   Clean: [Clean In] ◄────┘ ─► TONE3000 ────────────────────────────────────────┘  Compressor
                                                                              ─► Limiter ─► Scarlett out
  FCB1010 ─► MIDI bus "FCB" ─► Heavy gets MIDI ch 1, Clean gets ch 2 (their PCs + CCs).

Scene banks (tone3000.SCENES) switch instantly with absolute CCs, never a Program Change:
  - CC 81 activates the Selector insert. Bypassed, it passes the guitar on through the
    heavy chain. Active, it sends the guitar to the clean TONE3000 and mutes the heavy one.
  - CC 80 is read by the heavy TONE3000 as inputLevel (value/127). Qtractor switches the
    Solo block (+2 dB and a 380 ms lead echo) on above 63.
Qtractor also binds SW6/SW7/EXP A to the wah and octaver.

Tracks for recording: "Rig Print" (the processed stereo rig, fed back from bus
"Rig"), "DI" (dry guitar, muted, for re-amping) and "Backing" (drop a song here).
"""

import base64
import html
import os
import re
import signal
import struct
import subprocess
import sys
import time
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

import tone3000 as t3k

SESSION_DIR = Path.home() / "Music/fcb-rig"
SESSION = SESSION_DIR / "rig.qtr"
QTRACTOR_CONF = Path.home() / ".config/rncbc.org/Qtractor.conf"
QUANTUM = 256  # same as the standalone (experiments L1–L4)

GUITAR = (t3k.AUDIO["audioInputDeviceName"], "capture_MONO")
OUTPUT = t3k.AUDIO["audioOutputDeviceName"]
FCB = ("USB Midi", t3k.MIDI_PORT)
TONE3000_CLAP = Path.home() / ".clap/TONE3000.clap"

# FCB1010 row (rig.py): SW6 wah, SW7 octaver, SW8 boost, SW9 drive, SW10 echo,
# EXP A wah sweep, EXP B volume. The DAW takes back SW6/SW7/EXP A for real pedals;
# TONE3000 keeps the block toggles and output level.
CC_WAH, CC_OCTAVER, CC_WAH_SWEEP = 20, 21, 27
TONE3000_MIDI_MAP = [(target, cc) for target, cc in t3k.MIDI_MAP if cc not in (CC_WAH, CC_OCTAVER, CC_WAH_SWEEP)]
# Scene banks (tone3000.SCENES): CC 80 = the heavy TONE3000's input drive, read by its
# own MIDI map as value/127; Qtractor also switches the Solo boost + delay on above 63.
# CC 81 = the Selector insert: active routes the guitar to the clean TONE3000 instead.
HEAVY_MIDI_MAP = TONE3000_MIDI_MAP + [("inputLevel", t3k.SCENE_DRIVE_CC)]


def db(x):
    return round(10 ** (x / 20), 6)


# LV2: (label, uri, active, {port index: (name, value)}, {cc: (port index or "Activate", mode)})
# mode: "toggle" = every press flips (stock FCB sends 127), "latch" = value > 63 on / <= 63 off,
# "hook" = continuous, jumps straight to the pedal position.
WAH = ("Wah", "http://guitarix.sourceforge.net/plugins/gxautowah#wah", False,
       {3: ("Wah", 0.5)}, {CC_WAH: ("Activate", "toggle"), CC_WAH_SWEEP: (3, "hook")})
OCTAVER = ("Octaver", "http://guitarix.sourceforge.net/plugins/gx_detune_#_detune_", False,
           {2: ("DETUNE", -12.0), 6: ("WET", 45.0), 7: ("DRY", 80.0)}, {CC_OCTAVER: ("Activate", "toggle")})
# SOLO = one switch: +2 dB and a dark 380 ms lead echo. LSP Slap-back Delay passes the dry
# signal at "Dry amount", so while it is active (CC 80 > 63) it is the boost *and* the delay;
# bypassed it is unity. (A cranked amp's input push alone adds only ~+0.5 dB: experiments D8.)
SOLO = ("Solo", "http://lsp-plug.in/plugins/lv2/slap_delay_stereo", False,
        {15: ("Dry amount", db(2)), 17: ("Wet amount", 1.0),
         22: ("Delay 1 mode", 1), 23: ("Delay 1 left channel panorama", -100.0),
         24: ("Delay 1 right channel panorama", 100.0), 29: ("Delay 1 time", 380.0),
         34: ("Delay 1 low-cut", 1), 35: ("Delay 1 low-cut frequency", 250.0),
         36: ("Delay 1 high-cut", 1), 37: ("Delay 1 high-cut frequency", 4500.0),
         43: ("Delay 1 feedback", 0.3), 44: ("Delay 1 gain", db(-10))},
        {t3k.SCENE_DRIVE_CC: ("Activate", "latch")})
COMPRESSOR = ("Compressor", "http://lsp-plug.in/plugins/lv2/compressor_stereo", True,
              {29: ("Attack threshold", db(-18)), 30: ("Attack time", 10.0), 32: ("Release time", 120.0),
               34: ("Ratio", 3.0), 38: ("Makeup gain", db(3))}, {})
LIMITER = ("Limiter", "http://gareus.org/oss/lv2/dpl#stereo", True,
           {3: ("Input Gain", 0.0), 4: ("Threshold", -1.0), 5: ("Release Time", 0.01), 6: ("True Peak", 1.0)}, {})
# TONE3000: (label, "tone3000", scene its startup preset comes from, MIDI map)
HEAVY_T3K = ("TONE3000 Heavy", "tone3000", "heavy", HEAVY_MIDI_MAP)
CLEAN_T3K = ("TONE3000 Clean", "tone3000", "clean", TONE3000_MIDI_MAP)
HEAVY_CHAIN = [WAH, HEAVY_T3K, OCTAVER, SOLO]
CLEAN_CHAIN = [CLEAN_T3K]
BUS_CHAIN = [COMPRESSOR, LIMITER]  # on bus "Rig": both instances share one set of dynamics


def el(parent, tag, text=None, **attrs):
    e = ET.SubElement(parent, tag, {k.replace("_", "-"): str(v) for k, v in attrs.items()})
    if text is not None:
        e.text = str(text)
    return e


def fields(parent, **values):
    for k, v in values.items():
        el(parent, k.replace("_", "-"), int(v) if isinstance(v, bool) else v)


def connects(parent, tag, ports):
    c = el(parent, tag)
    for i, (client, port) in enumerate(ports):
        e = el(c, "connect", index=i)
        el(e, "client", client)
        el(e, "port", port)


def audio_bus(parent, name, mode, channels, ins=(), outs=(), plugins=()):
    b = el(parent, "audio-bus", name=name, mode=mode)
    fields(b, monitor=0, channels=channels, auto_connect=0)
    if mode != "output":
        fields(b, input_gain=1, input_panning=0)
        connects(b, "input-connects", ins)
    if mode != "input":
        fields(b, output_gain=1, output_panning=0)
        chain = el(b, "output-plugins")
        for spec in plugins:
            plugin(chain, *spec)
        connects(b, "output-connects", outs)


def controller(parent, name, index, cc, mode):
    c = el(parent, "controller", name=name, index=index, type="CONTROLLER")
    fields(c, channel=0, param=cc, logarithmic=0, feedback=0, invert=0,
           hook=int(mode == "hook"), latch=int(mode == "latch"))


ACTIVATE_INDEX = 1000  # any index past the plugin's own ports; matched by the name "Activate"


def qcompress(raw):
    """Qt's qCompress(): 4-byte big-endian length + zlib stream."""
    return base64.b64encode(struct.pack(">I", len(raw)) + zlib.compress(raw)).decode()


def tone3000_state(midi_map, scene):
    """The standalone's plugin state with this instance's MIDI map, starting on the
    first `scene` preset from rigs/ (so the rig is ready before any footswitch)."""
    text = t3k.SETTINGS.read_text()
    m = re.search(r'name="filterState" val="([^"]*)"', text)
    if not m:
        return None
    state = t3k.load_t3kb(t3k.juce_b64decode(html.unescape(m.group(1))))
    mappings = state.child("MidiMappings")
    mappings.set("channel", 0)
    mappings.children = [t3k.Node("Mapping").set("targetId", target).set("source", "cc").set("number", cc)
                         for target, cc in midi_map]
    rig = next((r for r in t3k.load_rigs() if r.get("scene") == scene), None)
    path = rig and t3k.PRESETS / f"{t3k.preset_id(rig['name'])}.t3kpreset"
    if path and path.exists():
        preset = t3k.load_t3kb(path.read_bytes())
        state.children = [preset.child("ChainSnapshot") if c.type == "ChainSnapshot" else c for c in state.children]
        values = {q.get("id"): q.get("value") for q in preset.child("Params").children}
        for q in state.child("PARAMETERS").children:
            if q.get("id") in values:
                q.set("value", float(values[q.get("id")]))
        state.set("activePresetId", f"user:{t3k.preset_id(rig['name'])}").set("activePresetName", rig["name"])
    for q in state.child("PARAMETERS").children:  # EXP B may have left outputLevel anywhere
        if q.get("id") in t3k.GLOBAL_PARAMS:
            q.set("value", float(t3k.GLOBAL_PARAMS[q.get("id")]))
    return t3k.dump_t3kb(state)


def plugin(parent, label, uri, active, params, ccs=None):
    if uri == "tone3000":  # CLAP: raw PC/CC go to its own MIDI map
        scene, midi_map = active, params
        p = el(parent, "plugin", type="CLAP")
        fields(p, filename=TONE3000_CLAP, index=0, label=label, activated=1)
        cfg = el(p, "configs")
        if (state := tone3000_state(midi_map, scene)) is not None:
            el(cfg, "config", qcompress(state), key="state")
        el(p, "params")
        return
    p = el(parent, "plugin", type="LV2")
    fields(p, filename=uri, index=0, label=label, activated=int(active))
    el(p, "configs")
    ps = el(p, "params")
    for index, (name, value) in params.items():
        el(ps, "param", value, index=index, name=name)
    if any(target == "Activate" for target, _ in ccs.values()):
        fields(p, activate_subject_index=ACTIVATE_INDEX)
    cs = el(p, "controllers")
    for cc, (target, mode) in ccs.items():
        if target == "Activate":
            controller(cs, "Activate", ACTIVATE_INDEX, cc, mode)
        else:
            controller(cs, params[target][0], target, cc, mode)


def insert(parent, label, returns, send=0, wet=1, active=True, activate_cc=None):
    """Qtractor Audio Insert: JACK ports Qtractor:<label>/out_N (send) and in_N (return).
    On a MIDI track the chain input is silence, so the return is what you hear."""
    p = el(parent, "plugin", type="Insert")
    fields(p, index=2, label=label, activated=int(active))
    cfg = el(p, "configs")
    for ch, (client, port) in enumerate(returns):
        el(cfg, "config", f"{ch}|{client}|{port}", key=f"in_{ch}")
    ps = el(p, "params")
    for i, (name, v) in enumerate([("Send Gain", send), ("Dry Gain", 0), ("Wet Gain", wet), ("Latency (frames)", 0)]):
        el(ps, "param", v, index=i, name=name)
    if activate_cc is not None:
        fields(p, activate_subject_index=ACTIVATE_INDEX)
        controller(el(p, "controllers"), "Activate", ACTIVATE_INDEX, activate_cc, "latch")


def track(parent, name, kind, in_bus, out_bus, monitor=False, mute=False, midi_channel=None, record=False):
    tr = el(parent, "track", name=name, type=kind)
    props = el(tr, "properties")
    fields(props, input_bus=in_bus, output_bus=out_bus)
    if midi_channel is not None:  # only this channel reaches the plugins
        fields(props, midi_omni=0, midi_channel=midi_channel, midi_drums=0)
    fields(el(tr, "state"), mute=mute, solo=0, record=record, monitor=monitor, gain=1, panning=0)
    el(tr, "controllers")
    el(tr, "clips")
    return tr


def rig_track(tracks, name, channel, head, chain):
    tr = track(tracks, name, "midi", "FCB", "MIDI Out", monitor=True, midi_channel=channel)
    plugins = el(tr, "plugins")
    head(plugins)
    for spec in chain:
        plugin(plugins, *spec)
    fields(plugins, audio_output_bus=0, audio_output_bus_name="Rig", audio_output_auto_connect=0)


def session_xml():
    root = ET.Element("session", name="rig", version="Qtractor 1.6.4")
    fields(el(root, "properties"), directory=SESSION_DIR, description="FCB1010 + TONE3000 rig (generated by qtractor_rig.py)",
           tempo=120, ticks_per_beat=960, beats_per_bar=4, beat_divisor=2)
    devices = el(root, "devices")
    audio = el(devices, "audio-engine")
    fields(el(audio, "audio-control"), transport_mode="none", timebase=0)
    out = [(OUTPUT, "playback_FL"), (OUTPUT, "playback_FR")]
    audio_bus(audio, "Master", "output", 2, outs=out)
    audio_bus(audio, "Rig", "output", 2, outs=out, plugins=BUS_CHAIN)
    audio_bus(audio, "Rig Print", "input", 2, ins=[("Qtractor", "Rig/out_1"), ("Qtractor", "Rig/out_2")])
    audio_bus(audio, "Guitar In", "input", 1, ins=[GUITAR])
    midi = el(devices, "midi-engine")
    fields(el(midi, "midi-control"), mmc_mode="input", mmc_device=127, spp_mode="none", clock_mode="none")
    fcb = el(midi, "midi-bus", name="FCB", mode="input")  # first MIDI input = control bus
    fields(fcb, monitor=0)
    connects(fcb, "input-connects", [FCB])
    fields(el(midi, "midi-bus", name="MIDI Out", mode="output"), monitor=0)

    tracks = el(root, "tracks")
    fields(el(tracks, "view"), pixels_per_beat=32, horizontal_zoom=100, vertical_zoom=100, snap_per_beat=4)
    # Heavy (PC ch 1): guitar -> Selector (bypassed = passes through; active = sends the
    # guitar to the clean rig and mutes this one) -> wah -> TONE3000 -> octaver -> solo.
    rig_track(tracks, "Heavy", 0, lambda ps: (
        insert(ps, "Guitar", [GUITAR, GUITAR]),
        insert(ps, "Selector", [], send=1, wet=0, active=False, activate_cc=t3k.SCENE_SELECT_CC)), HEAVY_CHAIN)
    # Clean (PC ch 2): hears the guitar only through the Selector's send.
    rig_track(tracks, "Clean", 1, lambda ps: insert(
        ps, "Clean In", [("Qtractor", "Selector/out_1"), ("Qtractor", "Selector/out_2")]), CLEAN_CHAIN)
    # Armed but not monitored: silent until the transport records (MMC or the GUI).
    for name, in_bus, mute, armed in [("Rig Print", "Rig Print", False, True), ("DI", "Guitar In", True, True),
                                      ("Backing", "Rig Print", False, False)]:
        el(track(tracks, name, "audio", in_bus, "Master", mute=mute, record=armed), "plugins")
    ET.indent(root, " ")
    return '<?xml version="1.0" encoding="UTF-8"?>\n<!DOCTYPE qtractorSession>\n' + ET.tostring(root, encoding="unicode") + "\n"


# Qtractor.conf keys for an unattended start (QSettings INI; edited in place).
CONF = {
    "Default": {"AutoMonitor": "false", "AutoDeactivate": "false", "SessionBackup": "false"},
    "Options": {"Audio\\MasterAutoConnect": "false", "Audio\\SelfConnected": "false",
                "Display\\ConfirmRemove": "false", "Display\\ConfirmArchive": "false",
                "Midi\\ControlBus": "false", "Midi\\MmcMode": "1"},
    "Plugins": {"AudioOutputBus": "false", "AudioOutputAutoConnect": "false"},
    "AutoSave": {"Enabled": "false"},
}


def patch_conf():
    lines = QTRACTOR_CONF.read_text().splitlines() if QTRACTOR_CONF.exists() else []
    for section, keys in CONF.items():
        header = f"[{section}]"
        if header not in lines:
            lines += ["", header]
        start = lines.index(header) + 1
        end = next((i for i in range(start, len(lines)) if lines[i].startswith("[")), len(lines))
        for key, value in keys.items():
            hit = next((i for i in range(start, end) if lines[i].split("=", 1)[0] == key), None)
            if hit is None:
                lines.insert(end, f"{key}={value}")
                end += 1
            else:
                lines[hit] = f"{key}={value}"
    QTRACTOR_CONF.parent.mkdir(parents=True, exist_ok=True)
    QTRACTOR_CONF.write_text("\n".join(lines) + "\n")


def pid():
    out = subprocess.run(["pgrep", "-x", "qtractor"], capture_output=True, text=True).stdout.split()
    return int(out[0]) if out else None


def build():
    if pid():
        sys.exit("Qtractor is running — `qtractor_rig.py down` first (it rewrites its config).")
    SESSION_DIR.mkdir(parents=True, exist_ok=True)
    SESSION.write_text(session_xml())
    patch_conf()
    print(f"wrote {SESSION} and patched {QTRACTOR_CONF}")


def up():
    build()
    if subprocess.run(["pgrep", "-x", "TONE3000"], capture_output=True).stdout:
        print("note: the TONE3000 standalone is running too — quit it or you'll hear both")
    subprocess.run(["systemd-run", "--user", "--quiet", "--collect", "--unit=qtractor-rig",
                    f"--setenv=PIPEWIRE_QUANTUM={QUANTUM}/48000", "qtractor", str(SESSION)], check=True)
    for _ in range(60):
        if pid() and "Qtractor" in subprocess.run(["aconnect", "-l"], capture_output=True, text=True).stdout:
            print(f"Qtractor up (pid {pid()}, quantum {QUANTUM})")
            return
        time.sleep(0.5)
    sys.exit("Qtractor did not come up — journalctl --user -u qtractor-rig")


def down():
    if not (p := pid()):
        return print("Qtractor is not running")
    before = SESSION.stat().st_mtime if SESSION.exists() else 0
    os.kill(p, signal.SIGUSR1)  # save first, so SIGTERM never asks
    for _ in range(60):  # the save is several MB (plugin states): wait until it has landed
        time.sleep(0.5)
        if SESSION.exists() and SESSION.stat().st_mtime != before and SESSION.stat().st_size \
                and SESSION.read_bytes().rstrip().endswith(b"</session>"):
            break
    os.kill(p, signal.SIGTERM)
    for _ in range(40):
        if not pid():
            return print("Qtractor saved and closed")
        time.sleep(0.25)
    sys.exit("Qtractor still running (a dialog may be open)")


MMC = {"stop": "01", "play": "02", "record": "06", "record-exit": "07"}


def mmc(*commands):
    """MIDI Machine Control into Qtractor's control bus (the FCB input port)."""
    out = subprocess.run(["aconnect", "-l"], capture_output=True, text=True).stdout
    m = re.search(r"client (\d+): 'Qtractor'", out)
    if not m:
        sys.exit("Qtractor is not running — `qtractor_rig.py up`")
    for c in commands:
        subprocess.run(["aseqsend", "-p", f"{m.group(1)}:0", "F0", "7F", "7F", "06", MMC[c], "F7"], check=True)
        time.sleep(0.1)


def record():
    mmc("record", "play")
    print(f"recording 'Rig Print' + 'DI' into {SESSION_DIR} — `qtractor_rig.py stop` to finish")


def stop():
    mmc("stop", "record-exit")
    takes = sorted(SESSION_DIR.glob("*.wav"), key=lambda f: f.stat().st_mtime)[-2:]
    print("stopped" + "".join(f"\n  {f}" for f in takes))


def show_map():
    names = lambda chain: " -> ".join(f"{label}{'' if active in (True, 'heavy', 'clean') else ' (off)'}"
                                      for label, _, active, *_ in chain)
    print("Heavy: Guitar -> Selector -> " + names(HEAVY_CHAIN) + " -> bus Rig")
    print("Clean: Selector send -> " + names(CLEAN_CHAIN) + " -> bus Rig")
    print("Rig bus: " + names(BUS_CHAIN))
    print("\nFCB1010:")
    print("  SW1-5    PC 0-14   TONE3000 presets (rigs/)")
    print(f"  SW6      CC {CC_WAH}     wah on/off (Qtractor)")
    print(f"  SW7      CC {CC_OCTAVER}     octaver on/off (Qtractor)")
    for target, cc in TONE3000_MIDI_MAP:
        print(f"  {'':8} CC {cc:<5} TONE3000 {target}")
    print(f"  EXP A    CC {CC_WAH_SWEEP}     wah sweep (Qtractor)")
    print("\nScene banks (03-09), CC 80 drive / CC 81 selector:")
    for name, (drive, select, clean) in t3k.SCENES.items():
        print(f"  {name:<9} CC80={drive:<3} CC81={select:<3} clean instance: {clean} preset")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "map"
    {"build": build, "up": up, "down": down, "record": record, "stop": stop, "map": show_map}.get(cmd, lambda: sys.exit(__doc__))()
