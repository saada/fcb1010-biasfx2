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

Signal flow (a MIDI track, because Qtractor only feeds MIDI to plugins on MIDI
tracks/buses, and only the CLAP build of TONE3000 gets raw Program Change):

  Scarlett Input 2 ─► [Insert "Guitar"] ─► Wah ─► TONE3000 (CLAP) ─► Octaver
      ─► Compressor ─► Limiter ─► bus "Rig" ─► Scarlett out
  FCB1010 ─► MIDI bus "FCB" ─► track "Rig" ─► PC/CC straight into TONE3000;
      Qtractor itself binds SW6/SW7/EXP A to the DAW pedals.

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


def db(x):
    return round(10 ** (x / 20), 6)


# Each LV2 entry: (label, uri, active, {port index: (name, value)}, {cc: (port index or "Activate", hook)})
CHAIN = [
    ("Wah", "http://guitarix.sourceforge.net/plugins/gxautowah#wah", False,
     {3: ("Wah", 0.5)},
     {CC_WAH: ("Activate", False), CC_WAH_SWEEP: (3, True)}),
    ("TONE3000", None, True, {}, {}),
    ("Octaver", "http://guitarix.sourceforge.net/plugins/gx_detune_#_detune_", False,
     {2: ("DETUNE", -12.0), 6: ("WET", 45.0), 7: ("DRY", 80.0)},
     {CC_OCTAVER: ("Activate", False)}),
    ("Compressor", "http://lsp-plug.in/plugins/lv2/compressor_stereo", True,
     {29: ("Attack threshold", db(-18)), 30: ("Attack time", 10.0), 32: ("Release time", 120.0),
      34: ("Ratio", 3.0), 38: ("Makeup gain", db(3))},
     {}),
    ("Limiter", "http://gareus.org/oss/lv2/dpl#stereo", True,
     {3: ("Input Gain", 0.0), 4: ("Threshold", -1.0), 5: ("Release Time", 0.01), 6: ("True Peak", 1.0)},
     {}),
]


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


def audio_bus(parent, name, mode, channels, ins=(), outs=()):
    b = el(parent, "audio-bus", name=name, mode=mode)
    fields(b, monitor=0, channels=channels, auto_connect=0)
    if mode != "output":
        fields(b, input_gain=1, input_panning=0)
        connects(b, "input-connects", ins)
    if mode != "input":
        fields(b, output_gain=1, output_panning=0)
        connects(b, "output-connects", outs)


def controller(parent, name, index, cc, hook):
    c = el(parent, "controller", name=name, index=index, type="CONTROLLER")
    fields(c, channel=0, param=cc, logarithmic=0, feedback=0, invert=0, hook=int(hook), latch=0)


def qcompress(raw):
    """Qt's qCompress(): 4-byte big-endian length + zlib stream."""
    return base64.b64encode(struct.pack(">I", len(raw)) + zlib.compress(raw)).decode()


def tone3000_state():
    """The standalone's plugin state (presets, globals) with the DAW's MIDI map."""
    text = t3k.SETTINGS.read_text()
    m = re.search(r'name="filterState" val="([^"]*)"', text)
    if not m:
        return None
    state = t3k.load_t3kb(t3k.juce_b64decode(html.unescape(m.group(1))))
    mappings = state.child("MidiMappings")
    mappings.set("channel", 0)
    mappings.children = [t3k.Node("Mapping").set("targetId", target).set("source", "cc").set("number", cc)
                         for target, cc in TONE3000_MIDI_MAP]
    for p in state.child("PARAMETERS").children:  # EXP B may have left outputLevel anywhere
        if p.get("id") in t3k.GLOBAL_PARAMS:
            p.set("value", float(t3k.GLOBAL_PARAMS[p.get("id")]))
    return t3k.dump_t3kb(state)


def plugin(parent, label, uri, active, params, ccs):
    if uri is None:  # TONE3000 CLAP: raw PC/CC handled by its own MIDI map
        p = el(parent, "plugin", type="CLAP")
        fields(p, filename=TONE3000_CLAP, index=0, label=label, activated=1)
        cfg = el(p, "configs")
        if (state := tone3000_state()) is not None:
            el(cfg, "config", qcompress(state), key="state")
        el(p, "params")
        return
    p = el(parent, "plugin", type="LV2")
    fields(p, filename=uri, index=0, label=label, activated=int(active))
    el(p, "configs")
    ps = el(p, "params")
    for index, (name, value) in params.items():
        el(ps, "param", value, index=index, name=name)
    activate_index = max([*params, 100]) + 1
    if any(target == "Activate" for target, _ in ccs.values()):
        fields(p, activate_subject_index=activate_index)
    cs = el(p, "controllers")
    for cc, (target, hook) in ccs.items():
        if target == "Activate":
            controller(cs, "Activate", activate_index, cc, hook)
        else:
            controller(cs, params[target][0], target, cc, hook)


def track(parent, name, kind, in_bus, out_bus, monitor=False, mute=False, midi=False, record=False):
    tr = el(parent, "track", name=name, type=kind)
    props = el(tr, "properties")
    fields(props, input_bus=in_bus, output_bus=out_bus)
    if midi:
        fields(props, midi_omni=1, midi_channel=0, midi_drums=0)
    fields(el(tr, "state"), mute=mute, solo=0, record=record, monitor=monitor, gain=1, panning=0)
    el(tr, "controllers")
    el(tr, "clips")
    return tr


def session_xml():
    root = ET.Element("session", name="rig", version="Qtractor 1.6.4")
    fields(el(root, "properties"), directory=SESSION_DIR, description="FCB1010 + TONE3000 rig (generated by qtractor_rig.py)",
           tempo=120, ticks_per_beat=960, beats_per_bar=4, beat_divisor=2)
    devices = el(root, "devices")
    audio = el(devices, "audio-engine")
    fields(el(audio, "audio-control"), transport_mode="none", timebase=0)
    out = [(OUTPUT, "playback_FL"), (OUTPUT, "playback_FR")]
    audio_bus(audio, "Master", "output", 2, outs=out)
    audio_bus(audio, "Rig", "output", 2, outs=out)
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
    rig = track(tracks, "Rig", "midi", "FCB", "MIDI Out", monitor=True, midi=True)
    plugins = el(rig, "plugins")
    ins = el(plugins, "plugin", type="Insert")
    fields(ins, index=2, label="Guitar", activated=1)
    cfg = el(ins, "configs")
    for ch in range(2):  # mono guitar into both chain channels
        el(cfg, "config", f"{ch}|{GUITAR[0]}|{GUITAR[1]}", key=f"in_{ch}")
    ps = el(ins, "params")
    for i, (name, v) in enumerate([("Send Gain", 0), ("Dry Gain", 0), ("Wet Gain", 1), ("Latency (frames)", 0)]):
        el(ps, "param", v, index=i, name=name)
    for spec in CHAIN:
        plugin(plugins, *spec)
    fields(plugins, audio_output_bus=0, audio_output_bus_name="Rig", audio_output_auto_connect=0)
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
    os.kill(p, signal.SIGUSR1)  # save first, so SIGTERM never asks
    time.sleep(1.5)
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
    print("Chain: Guitar in -> " + " -> ".join(f"{label}{'' if active else ' (off)'}" for label, _, active, *_ in CHAIN))
    print("\nFCB1010:")
    print("  SW1-5    PC 0-14   TONE3000 presets (rigs/)")
    print(f"  SW6      CC {CC_WAH}     wah on/off (Qtractor)")
    print(f"  SW7      CC {CC_OCTAVER}     octaver on/off (Qtractor)")
    for target, cc in TONE3000_MIDI_MAP:
        print(f"  {'':8} CC {cc:<5} TONE3000 {target}")
    print(f"  EXP A    CC {CC_WAH_SWEEP}     wah sweep (Qtractor)")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "map"
    {"build": build, "up": up, "down": down, "record": record, "stop": stop, "map": show_map}.get(cmd, lambda: sys.exit(__doc__))()
