# /// script
# requires-python = ">=3.10"
# dependencies = ["alsa-midi>=1.0.4"]
# ///
"""Run the FCB1010 + TONE3000 rig inside Qtractor — generated, never clicked.

Usage:
  uv run qtractor_rig.py build    Write ~/Music/fcb-rig/rig.qtr and patch Qtractor.conf
                                   (Qtractor must NOT be running: it rewrites its config)
  uv run qtractor_rig.py up       build + launch Qtractor on the session (quantum 256)
  uv run qtractor_rig.py down     save (SIGUSR1) and quit Qtractor
  uv run qtractor_rig.py record   start recording (MMC): the rig in stereo + the dry DI
  uv run qtractor_rig.py stop     stop the transport and list the new takes
  uv run qtractor_rig.py map      Show the chain and the footswitch map

Signal flow. The rigs sit on MIDI tracks because Qtractor only feeds MIDI to plugins on
MIDI tracks/buses, and only the CLAP build of TONE3000 gets raw Program Change:

  Scarlett In 2 ─► Heavy: [Guitar] ─► [Selector] ─► Wah ─► TONE3000 ─► Octaver ─► Chorus ─► Solo ┐
                                          │ send (CC 81 > 63)                                  ├► bus Rig:
                   Clean: [Clean In] ◄────┘ ─► TONE3000 ────────────────────────────────────────┘  Compressor
                                                                 ─► Volume (EXP B) ─► Limiter ─► Scarlett out
  Heavy's [Harmony Tap] sends the clean DI ─► Harmony: [Harmony In] (SW7) ─► 3 x42 autotune
      stages (a diatonic third in the song's key) ─► TONE3000 (partner's amp) ─► bus Rig
  FCB1010 ─► fcb_router.py ─► MIDI bus "FCB" ─► Heavy gets MIDI ch 1, Clean ch 2, Harmony ch 3.
  The pedal only sends addresses (bank, switch); the router turns them into rig.py's layout.
  `helper` (a user service started by `up`) turns a scene bank's PC into its song's solo
  echo time and harmony scale (CC 85-88).

Scene banks (tone3000.SCENES) switch instantly with absolute CCs, never a Program Change:
  - CC 81 activates the Selector insert. Bypassed, it passes the guitar on through the
    heavy chain. Active, it sends the guitar to the clean TONE3000 and mutes the heavy one.
  - CC 80 is read by the heavy TONE3000 as inputLevel (value/127). Qtractor switches the
    Solo block (+2 dB and a 380 ms lead echo) on above 63.
Qtractor also binds SW6/SW7/EXP A to the wah and octaver (SW7 = the chorus in the 80s banks),
and EXP B (CC 7, tapered by the router) to the Rig bus's Volume stage.

Tracks for recording: "Rig Print" (the processed stereo rig, fed back from bus
"Rig"), "DI" (dry guitar, muted, for re-amping) and "Backing" (drop a song here).
"""

import base64
import html
import os
import re
import shutil
import signal
import struct
import subprocess
import sys
import threading
import time
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path
from queue import Queue

import fcb_router
import tone3000 as t3k

SESSION_DIR = Path.home() / "Music/fcb-rig"
SESSION = SESSION_DIR / "rig.qtr"
QTRACTOR_CONF = Path.home() / ".config/rncbc.org/Qtractor.conf"
QUANTUM = 256  # same as the standalone (experiments L1–L4)

GUITAR = (t3k.AUDIO["audioInputDeviceName"], "capture_MONO")
OUTPUT = t3k.AUDIO["audioOutputDeviceName"]
FCB = ("USB Midi", t3k.MIDI_PORT)
ROUTER = (fcb_router.CLIENT, fcb_router.OUT_PORT)  # what the FCB bus listens to (the router feeds it)
TONE3000_CLAP = Path.home() / ".clap/TONE3000.clap"

# FCB1010 row (rig.py): SW6 wah, SW7 octaver, SW8 boost, SW9 drive, SW10 echo,
# EXP A wah sweep, EXP B volume. The DAW takes back SW6/SW7/EXP A for real pedals, and EXP B
# for the Rig bus's Volume stage; TONE3000 keeps the block toggles.
CC_WAH, CC_OCTAVER, CC_WAH_SWEEP = 20, 21, 27
CC_HARMONY = 25  # SW7 in the scene banks: twin-guitar harmony instead of the octaver
CC_CHORUS = 31   # SW7 in the 80s banks (rig.CHORUS_CC): the stereo chorus instead of the octaver
# The chorus's per-song rate / depth / mix (chorus_ccs()): absolute CCs, sent after a song's PC.
CC_CHORUS_RATE, CC_CHORUS_DEPTH, CC_CHORUS_MIX = 89, 90, 91
CC_TUNER = 28    # SW10 in every bank: the helper shows the tuner (Fretwise) and mutes the rig
CC_TUNER_MUTE = 29  # helper -> Qtractor: absolute 127/0 (a toggled Insert lost its input links)
# Per-song settings the FCB has no room for (two CCs per switch): `helper` watches the
# FCB for a scene bank's Program Change and sends these to Qtractor.
CC_SOLO_TIME, CC_HARMONY_SCALE = 85, 86
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
# CHORUS = a real time-varying chorus, which TONE3000 can't do (Spread wobble only widens).
# LSP Chorus Stereo after the amp, where a JC-120's chorus or a rack chorus sits. Two voices,
# triangle LFO, the two channels 180 degrees apart (the JC-120's two speakers), 7 ms base
# delay. The processed path is wet only (Dry amount 0, Wet 1) and "Dry/Wet balance" is the mix
# knob, so mix 0.5 = half input, half chorus. Bypassed by default; SW7 toggles it in the 80s
# banks. Rate, depth and mix start at CHORUS_DEFAULT and follow each preset's `chorus` block
# through CC 89-91 (chorus_ccs). Qtractor maps a CC linearly over the port's range
# (logarithmic=0, as for the Solo block's time): rate 0.01-20 Hz, depth 0.1-20 ms, mix 0-100 %.
CHORUS_DEFAULT = {"rate_hz": 0.6, "depth_ms": 4.0, "mix": 0.5}  # slow, moderate, half wet: a CE-2-style 80s chorus
CHORUS_RANGE = t3k.CHORUS_LIMITS  # the ports' ranges, which a CC 0-127 spans linearly
CHORUS = ("Chorus", "http://lsp-plug.in/plugins/lv2/chorus_stereo", False,
          {13: ("Rate", CHORUS_DEFAULT["rate_hz"]), 20: ("Number of voices", 0),
           21: ("Depth", CHORUS_DEFAULT["depth_ms"]), 25: ("LFO type 1", 0), 28: ("LFO delay 1", 7.0),
           31: ("Inter-channel phase 1", 180.0), 45: ("Dry amount", 0.0), 46: ("Wet amount", 1.0),
           47: ("Dry/Wet balance", 100 * CHORUS_DEFAULT["mix"])},
          {CC_CHORUS: ("Activate", "toggle"), CC_CHORUS_RATE: (13, "hook"),
           CC_CHORUS_DEPTH: (21, "hook"), CC_CHORUS_MIX: (47, "hook")})
# SOLO (labelled LEAD on the board) = one switch: amps pushed + 2 dB, no echo. LSP Slap-back Delay
# passes the dry signal at "Dry amount", so while it is active (CC 80 > 63) it is a +2 dB boost;
# bypassed it is unity. Its echo is muted (Wet 0): the Maiden bank has its own echo switches
# (SW8/SW9, TONE3000 blocks) and two echoes at different times would flam. (A cranked amp's input push alone adds only ~+0.5 dB: experiments D8.)
SOLO = ("Solo", "http://lsp-plug.in/plugins/lv2/slap_delay_stereo", False,
        {15: ("Dry amount", db(2)), 17: ("Wet amount", 0.0),
         22: ("Delay 1 mode", 1), 23: ("Delay 1 left channel panorama", -100.0),
         24: ("Delay 1 right channel panorama", 100.0), 29: ("Delay 1 time", 380.0),
         34: ("Delay 1 low-cut", 1), 35: ("Delay 1 low-cut frequency", 250.0),
         36: ("Delay 1 high-cut", 1), 37: ("Delay 1 high-cut frequency", 4500.0),
         43: ("Delay 1 feedback", 0.3), 44: ("Delay 1 gain", db(-10))},
        {t3k.SCENE_DRIVE_CC: ("Activate", "latch"), CC_SOLO_TIME: (29, "hook")})  # time: per song (helper)
# Bypassed by default: at -18 dB / 3:1 / 10 ms attack it sat on every heavy preset and flattened
# palm-mute chugs ("too muffled"); the owner preferred the rig with it off (experiments D16).
# The limiter alone keeps peaks under -1 dBTP. It stays in the chain to switch on in Qtractor.
COMPRESSOR = ("Compressor", "http://lsp-plug.in/plugins/lv2/compressor_stereo", False,
              {29: ("Attack threshold", db(-18)), 30: ("Attack time", 10.0), 32: ("Release time", 120.0),
               34: ("Ratio", 3.0), 38: ("Makeup gain", db(3))}, {})
# VOLUME = EXP B (CC 7): one gain stage for all three rigs, before the limiter, so it guards the
# toe and no Program Change resets the level (a preset param would: experiments D8, D22). LSP
# Slap-back Delay, dry only: "Dry amount" (0-10, linear) follows CC 7, which the router sends
# through an audio taper (fcb_router.volume_cc), and a fixed "Output gain" trim makes CC 127
# exactly 0 dB (full toe = the presets' YouTube-matched level). It starts at 0 dB.
VOLUME = ("Volume", "http://lsp-plug.in/plugins/lv2/slap_delay_stereo", True,
          {15: ("Dry amount", round(fcb_router.VOLUME_STAGE_MAX * fcb_router.VOLUME_NOMINAL_CC / 127, 6)),
           16: ("Dry mute", 0), 17: ("Wet amount", 0.0), 18: ("Wet mute", 1),
           19: ("Dry/Wet balance", 100.0), 20: ("Mono output", 0), 21: ("Output gain", fcb_router.VOLUME_STAGE_TRIM)},
          {fcb_router.VOLUME_CC: (15, "hook")})
LIMITER = ("Limiter", "http://gareus.org/oss/lv2/dpl#stereo", True,
           {3: ("Input Gain", 0.0), 4: ("Threshold", -1.0), 5: ("Release Time", 0.01), 6: ("True Peak", 1.0)}, {})
# TONE3000: (label, "tone3000", scene its startup preset comes from, MIDI map)
HEAVY_T3K = ("TONE3000 Heavy", "tone3000", "heavy", HEAVY_MIDI_MAP)
CLEAN_T3K = ("TONE3000 Clean", "tone3000", "clean", TONE3000_MIDI_MAP)
HEAVY_CHAIN = [WAH, HEAVY_T3K, OCTAVER, CHORUS, SOLO]
CLEAN_CHAIN = [CLEAN_T3K]
BUS_CHAIN = [COMPRESSOR, VOLUME, LIMITER]  # on bus "Rig": every rig shares the volume and the dynamics

# HARMONY = the second guitarist a diatonic third above, through his own amp. The heavy
# chain taps the clean DI. Track "Harmony" (SW7 toggles its input) runs it through three x42
# autotune stages that build the third from scale steps, then through a third TONE3000 on
# MIDI ch 3. That TONE3000 loads the bank's generated harmony preset: the partner guitarist's
# amp and cab only, since a full dual rig here cost 80 xruns in 30 s. The song's scale comes
# from `helper`. Pitch tracking has to happen on the clean DI: after a cranked amp only 64% of
# frames were right, on the DI 8/8 notes (experiments D11).
FAT1_SCALES = ["Chromatic"] + [f"{k} Major" for k in "C Db D Eb E F F# G Ab A Bb B".split()] \
    + [f"{k} Minor" for k in "C C# D Eb E F F# G G# A Bb B".split()]
def fat1_stage(label, offset, scale_cc=None):
    """x42 autotune: snap to the song's scale (Manual mode = the scale mask only) and shift."""
    return (label, "http://gareus.org/oss/lv2/fat1#scales", True,
            {3: ("Mode", 2.0), 7: ("Filter", 0.02), 8: ("Correction", 1.0), 9: ("Offset", offset),
             11: ("Fast Correction", 1.0), 12: ("Scale", float(FAT1_SCALES.index("G Major")))},
            {scale_cc: (12, "hook")} if scale_cc is not None else {})


# Diatonic third = two diatonic steps. Each stage shifts +2 semitones and snaps to the scale;
# a whole step that leaves the scale sits exactly between two scale notes and x42 resolves
# that tie downward, i.e. to the half step - so each stage is one scale step. A last stage
# only snaps. Three light stages replaced Rubber Band, whose FFT spikes on top of a third
# TONE3000 caused xruns (experiments D11). Every stage follows the helper's scale CC... but
# Qtractor allows one observer per CC, so stages 2-3 get their own CCs (CC_HARMONY_SCALE+1/+2).
HARMONY_CHAIN = [
    fat1_stage("Harmony Step 1", 2.0, CC_HARMONY_SCALE),
    fat1_stage("Harmony Step 2", 2.0, CC_HARMONY_SCALE + 1),
    fat1_stage("Harmony Snap", 0.0, CC_HARMONY_SCALE + 2),
    ("TONE3000 Harmony", "tone3000", "harmony", TONE3000_MIDI_MAP),  # light partner-side preset (PC ch 3)
]


def scale_cc(scale):
    """CC value that lands on fat1's `Scale` index (Qtractor maps 0-127 linearly onto 0-24)."""
    return -(-FAT1_SCALES.index(scale) * 127 // 24)


def song_settings():
    """{heavy PC: (solo echo ms, harmony scale)} for the scene banks (rigs `song` block)."""
    out = {}
    for rig in t3k.load_rigs():
        if rig.get("scene") == "heavy":
            song = rig.get("song", {})
            echo = next((b for b in rig["left"] if b.get("role") == "echo" and b.get("delay_ms")), {})
            out[rig["pc"]] = (song.get("solo_delay_ms") or echo.get("delay_ms") or 380,
                              song.get("harmony_scale", "G Major"))
    return out


def chorus_settings():
    """{heavy-instance PC: {rate_hz, depth_ms, mix}}: each preset's `chorus` block over
    CHORUS_DEFAULT (every song and heavy scene preset, so a new song never keeps the last one's)."""
    return {r["pc"]: CHORUS_DEFAULT | {k: v for k, v in r.get("chorus", {}).items() if k in CHORUS_DEFAULT}
            for r in t3k.load_rigs() if r.get("scene") in (None, "heavy")}


def chorus_ccs(pc, settings=None):
    """The absolute CCs (MIDI ch 1) that set the chorus to preset `pc`'s rate, depth and mix.
    Whatever reads the FCB (helper, GuitarMood, the MIDI router) sends them after that PC."""
    s = (settings or chorus_settings()).get(pc, CHORUS_DEFAULT)
    to_cc = lambda k: round((s[k] - CHORUS_RANGE[k][0]) / (CHORUS_RANGE[k][1] - CHORUS_RANGE[k][0]) * 127)
    return [(CC_CHORUS_RATE, to_cc("rate_hz")), (CC_CHORUS_DEPTH, to_cc("depth_ms")), (CC_CHORUS_MIX, to_cc("mix"))]


def el(parent, tag, text=None, **attrs):
    e = ET.SubElement(parent, tag, {k.replace("_", "-"): str(v) for k, v in attrs.items()})
    if text is not None:
        e.text = str(text)
    return e


def fields(parent, **values):
    for k, v in values.items():
        el(parent, k.replace("_", "-"), int(v) if isinstance(v, bool) else v)


def connects(parent, tag, ports):
    """ports: (client, port) per channel, or (channel, client, port) to wire several per channel."""
    c = el(parent, tag)
    for i, spec in enumerate(ports):
        ch, client, port = spec if len(spec) == 3 else (i, *spec)
        e = el(c, "connect", index=ch)
        el(e, "client", client)
        el(e, "port", port)


def audio_bus(parent, name, mode, channels, ins=(), outs=(), plugins=(), panning=0):
    b = el(parent, "audio-bus", name=name, mode=mode)
    fields(b, monitor=0, channels=channels, auto_connect=0)
    if mode != "output":
        fields(b, input_gain=1, input_panning=0)
        connects(b, "input-connects", ins)
    if mode != "input":
        fields(b, output_gain=1, output_panning=panning)
        chain = el(b, "output-plugins")
        for spec in plugins:
            spec(chain) if callable(spec) else plugin(chain, *spec)
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
        params = state.child("PARAMETERS")
        for q in params.children:
            if q.get("id") in values:
                q.set("value", float(values.pop(q.get("id"))))
        # Parameters newer than the standalone's saved state (e.g. the 0.0.11 gate/pitch knobs)
        # would otherwise restore at the plugin default rather than the preset's value.
        params.children += [t3k.Node("PARAM").set("id", k).set("value", float(v)) for k, v in values.items()]
        state.set("activePresetId", f"user:{t3k.preset_id(rig['name'])}").set("activePresetName", rig["name"])
    for q in state.child("PARAMETERS").children:  # the standalone may have saved any outputLevel
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


def insert(parent, label, returns, send=0, wet=1, active=True, activate_cc=None, dry=0, activate_mode="latch"):
    """Qtractor Audio Insert: JACK ports Qtractor:<label>/out_N (send) and in_N (return).
    On a MIDI track the chain input is silence, so the return is what you hear."""
    p = el(parent, "plugin", type="Insert")
    fields(p, index=2, label=label, activated=int(active))
    cfg = el(p, "configs")
    for ch, (client, port) in enumerate(returns):
        el(cfg, "config", f"{ch}|{client}|{port}", key=f"in_{ch}")
    ps = el(p, "params")
    for i, (name, v) in enumerate([("Send Gain", send), ("Dry Gain", dry), ("Wet Gain", wet), ("Latency (frames)", 0)]):
        el(ps, "param", v, index=i, name=name)
    if activate_cc is not None:
        fields(p, activate_subject_index=ACTIVATE_INDEX)
        controller(el(p, "controllers"), "Activate", ACTIVATE_INDEX, activate_cc, activate_mode)


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
        spec[0](plugins) if callable(spec[0]) else plugin(plugins, *spec)
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
    connects(fcb, "input-connects", [ROUTER])  # the router also connects itself and drops any direct FCB link
    fields(el(midi, "midi-bus", name="MIDI Out", mode="output"), monitor=0)

    tracks = el(root, "tracks")
    fields(el(tracks, "view"), pixels_per_beat=32, horizontal_zoom=100, vertical_zoom=100, snap_per_beat=4)
    # Heavy (PC ch 1): guitar -> Selector (bypassed = passes through; active = sends the
    # guitar to the clean rig and mutes this one) -> wah -> TONE3000 -> octaver -> solo.
    rig_track(tracks, "Heavy", 0, lambda ps: (
        insert(ps, "Guitar", [GUITAR, GUITAR]),
        # SW10 tuner: active = silence (dry 0, wet 0). It sits before the Selector and the harmony
        # tap, so it mutes all three rigs, and their reverb tails ring out. On a bus, a bypassed
        # Insert stayed silent (experiments D13).
        insert(ps, "Tuner Mute", [], send=1, wet=0, active=False, activate_cc=CC_TUNER_MUTE),  # latch, like the Selector
        insert(ps, "Selector", [], send=1, wet=0, active=False, activate_cc=t3k.SCENE_SELECT_CC),
        insert(ps, "Harmony Tap", [], send=1, dry=1, wet=0)), HEAVY_CHAIN)  # passes on + sends the DI
    # Harmony (PC ch 3: the bank's light partner-side preset): SW7 opens its input; silent otherwise.
    rig_track(tracks, "Harmony", 2, lambda ps: insert(
        ps, "Harmony In", [("Qtractor", "Harmony Tap/out_1"), ("Qtractor", "Harmony Tap/out_2")],
        active=False, activate_cc=CC_HARMONY, activate_mode="toggle"), HARMONY_CHAIN)
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


def qtractor_port():
    out = subprocess.run(["aconnect", "-l"], capture_output=True, text=True).stdout
    m = re.search(r"client (\d+): 'Qtractor'", out)
    return f"{m.group(1)}:0" if m else None


def send_song(port, pc, settings):
    ms, scale = settings[pc]
    for cc, value in ((CC_SOLO_TIME, round(ms / 1000 * 127)), (CC_HARMONY_SCALE, scale_cc(scale)),
                      (CC_HARMONY_SCALE + 1, scale_cc(scale)), (CC_HARMONY_SCALE + 2, scale_cc(scale))):
        subprocess.run(["aseqsend", "-p", port, "B0", f"{cc:02X}", f"{value:02X}"], check=True)


def start_router(work):
    """The FCB router (fcb_router.py) in a thread: each translated burst lands in `work`, and
    None when the router stops. RIG_HELPER_SOURCE replays an aseqdump log instead (tests)."""
    if replay := os.environ.get("RIG_HELPER_SOURCE", "").split():
        translator = fcb_router.Translator()

        def feed():
            for line in subprocess.Popen(replay, stdout=subprocess.PIPE, text=True).stdout:
                if ev := fcb_router.parse_aseqdump(line):
                    work.put(translator.translate(ev)[1])
            work.put(None)
        threading.Thread(target=feed, daemon=True).start()
        return None
    router = fcb_router.Router(on_events=lambda addr, events: work.put(events),
                               on_status=lambda s: print(f"router: {s}", flush=True))

    def run():
        try:
            router.run()
        except Exception as e:  # noqa: BLE001 - another owner, or no ALSA sequencer
            print(f"FCB router failed: {e}", flush=True)
        work.put(None)
    router.thread = threading.Thread(target=run, daemon=True, name="fcb-router")
    router.thread.start()
    return router


def helper():
    """The rig's MIDI side when GuitarMood isn't running (the qtractor-rig-helper user unit):
    the FCB router (pedal addresses -> rig.py's layout -> Qtractor), and on its translated
    stream, a scene bank's heavy Program Change sends that song's solo echo time and harmony
    scale, and SW10 toggles the tuner."""
    work = Queue()
    router = start_router(work)
    if router:
        signal.signal(signal.SIGTERM, lambda *_: router.stop())  # systemctl stop: the router exits cleanly
    settings = song_settings()
    while not (port := qtractor_port()):
        time.sleep(1)
    first = next((r["pc"] for r in t3k.load_rigs() if r.get("scene") == "heavy"), None)
    if first is not None:
        send_song(port, first, settings)  # the session starts on the first scene bank
    tuner_proc, tuning = None, False
    while (events := work.get()) is not None:
        for kind, ch, *rest in events:
            if kind == "cc" and ch == 0 and rest[0] == CC_TUNER and rest[1] >= 64:
                tuning = not tuning  # SW10 toggles: mute the rig (absolute CC to Qtractor) + tuner
                tuner_proc = set_tuning(tuning, qtractor_port() or port, tuner_proc)
                print(f"tuner {'on' if tuning else 'off'}", flush=True)
            elif kind == "pc" and ch == 0 and rest[0] in settings:
                send_song(qtractor_port() or port, rest[0], settings)
                print(f"PC {rest[0]}: solo echo {settings[rest[0]][0]} ms, harmony {settings[rest[0]][1]}", flush=True)
    if router:
        router.thread.join(3)
        if not router.stopping.is_set():
            sys.exit(1)  # the router died: let systemd restart the unit


def guitar_source():
    """PipeWire node name of the guitar input (tone3000.AUDIO's input device)."""
    out = subprocess.run(["pactl", "list", "sources"], capture_output=True, text=True).stdout
    for block in out.split("\n\n"):
        if f"Description: {t3k.AUDIO['audioInputDeviceName']}" in block:
            return re.search(r"Name: (\S+)", block).group(1)
    sys.exit("guitar input not found")


# SW10's tuner: Fretwise, an Omarchy bar plugin (marketplace, verified). Its panel listens
# only while it is open, so SW10 summons it on the guitar input and hides it again.
# Without the plugin (not Omarchy) the helper falls back to Chromatic (Flathub).
TUNER_PLUGIN = "io.github.waynekruger.fretwise"
TUNER_PLUGIN_DIR = Path.home() / ".config/omarchy/plugins" / TUNER_PLUGIN
TUNER_APP = "io.github.nate_xyz.Chromatic"
TUNER_PORT = "ALSA plug-in [chromatic]:input_MONO"


def feed_only(port, src):
    """Connect src to an input port and drop every other connection to it."""
    lines = subprocess.run(["pw-link", "-l"], capture_output=True, text=True).stdout.splitlines()
    for i, line in enumerate(lines):
        if line.strip() == port:
            for dep in lines[i + 1:]:
                if not dep.startswith("  |<- "):
                    break
                other = dep[len("  |<- "):].strip()
                if other != src:
                    subprocess.run(["pw-link", "-d", other, port], capture_output=True)
    subprocess.run(["pw-link", src, port], capture_output=True)


def fretwise():
    return TUNER_PLUGIN_DIR.is_dir() and shutil.which("omarchy-shell")


def tuner_on():
    """Show the tuner on the clean guitar input (Scarlett Input 2, not the mic on Input 1)."""
    if fretwise():
        subprocess.run(["omarchy-bar", "set", TUNER_PLUGIN, "captureSource", guitar_source()], capture_output=True)
        subprocess.run(["omarchy-shell", "-q", "shell", "summon", TUNER_PLUGIN, "{}"], capture_output=True)
        return None
    return chromatic_on()


def chromatic_on():
    """Chromatic records through ALSA-on-PipeWire and grabs the default source (the mic), so
    its input is re-patched to the guitar once it appears. A Hyprland rule floats it (README)."""
    if subprocess.run(["flatpak", "info", TUNER_APP], capture_output=True).returncode:
        subprocess.run(["notify-send", "FCB tuner",
                        f"install one: omarchy plugin add https://github.com/WayneKruger/omarchy-fretwise.git --enable"])
        return None
    subprocess.run(["flatpak", "kill", TUNER_APP], capture_output=True)  # never two tuners
    proc = subprocess.Popen(["flatpak", "run", TUNER_APP], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    src = guitar_source() + ":capture_MONO"
    for _ in range(60):
        if TUNER_PORT in subprocess.run(["pw-link", "-i"], capture_output=True, text=True).stdout.splitlines():
            time.sleep(0.3)  # let its own default connection land, then replace it
            feed_only(TUNER_PORT, src)
            break
        time.sleep(0.2)
    return proc


def set_tuning(on, port, proc=None):
    """SW10: mute the rig (absolute CC 29 to Qtractor) and show or hide the tuner."""
    subprocess.run(["aseqsend", "-p", port, "B0", f"{CC_TUNER_MUTE:02X}", "7F" if on else "00"])
    if on:
        return tuner_on()
    tuner_off(proc)
    return None


def tuner_off(proc):
    if fretwise():
        subprocess.run(["omarchy-shell", "-q", "shell", "hide", TUNER_PLUGIN], capture_output=True)
    subprocess.run(["flatpak", "kill", TUNER_APP], capture_output=True)  # the sandboxed app outlives `flatpak run`
    if proc:
        proc.terminate()


def up(helper=True):
    """build + launch. GuitarMood passes helper=False: it runs the helper's job itself."""
    build()
    if subprocess.run(["pgrep", "-x", "TONE3000"], capture_output=True).stdout:
        print("note: the TONE3000 standalone is running too — quit it or you'll hear both")
    subprocess.run(["systemd-run", "--user", "--quiet", "--collect", "--unit=qtractor-rig",
                    f"--setenv=PIPEWIRE_QUANTUM={QUANTUM}/48000", "qtractor", str(SESSION)], check=True)
    for _ in range(60):
        if pid() and "Qtractor" in subprocess.run(["aconnect", "-l"], capture_output=True, text=True).stdout:
            if helper:
                subprocess.run(["systemd-run", "--user", "--quiet", "--collect", "--unit=qtractor-rig-helper",
                                "-p", "Restart=always", "-p", "RestartSec=2",
                                sys.executable, str(Path(__file__).resolve()), "helper"])
            print(f"Qtractor up (pid {pid()}, quantum {QUANTUM})" + (" + FCB router and per-song helper" if helper else ""))
            return
        time.sleep(0.5)
    sys.exit("Qtractor did not come up — journalctl --user -u qtractor-rig")


def down():
    subprocess.run(["systemctl", "--user", "stop", "qtractor-rig-helper"], capture_output=True)
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
    print(f"  SW7 80s  CC {CC_CHORUS}     chorus on/off (Qtractor); CC {CC_CHORUS_RATE}-{CC_CHORUS_MIX} its rate/depth/mix")
    for target, cc in TONE3000_MIDI_MAP:
        print(f"  {'':8} CC {cc:<5} TONE3000 {target}")
    print(f"  EXP A    CC {CC_WAH_SWEEP}     wah sweep (Qtractor)")
    print(f"  EXP B    CC {fcb_router.VOLUME_CC}      volume (Qtractor, Rig bus, before the limiter; "
          "0 dB at the toe and until moved, heel silent)")
    print("\nScene banks (03-09), CC 80 drive / CC 81 selector:")
    for name, (drive, select, clean) in t3k.SCENES.items():
        print(f"  {name:<9} CC80={drive:<3} CC81={select:<3} clean instance: {clean} preset")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "map"
    {"build": build, "up": up, "down": down, "record": record, "stop": stop, "map": show_map, "helper": helper}.get(cmd, lambda: sys.exit(__doc__))()
