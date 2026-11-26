"""The rig's lifetime: start Qtractor (or join the one already running), run the FCB router
and do the helper's job (per-song echo + harmony key, the SW10 tuner), and shut everything
down cleanly.

GuitarMood replaces the `qtractor-rig-helper` user unit while it runs: one process owns the
FCB router (fcb_router.py: the pedal's addresses -> rig.py's layout -> Qtractor), and the board
reads the router's translated stream, so what's on screen is what the rig got.
"""

import os
import queue
import shlex
import signal
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

from . import retune
from .board import qr, t3k
from .state import parse

router_mod = qr.fcb_router

HELPER_UNIT = "qtractor-rig-helper"
DAW_LABELS = {"Wah": "wah", "Octaver": "octaver", "Harmony In": "harmony", "Chorus": "chorus", "Vibe": "vibe", "Tuner Mute": "tuner"}


def save_session():
    """Ask the running Qtractor to save (SIGUSR1) and parse what it wrote. None if it didn't."""
    before = qr.SESSION.stat().st_mtime if qr.SESSION.exists() else 0
    os.kill(qr.pid(), signal.SIGUSR1)
    for _ in range(40):
        time.sleep(0.25)
        if qr.SESSION.exists() and qr.SESSION.stat().st_mtime != before \
                and qr.SESSION.read_bytes().rstrip().endswith(b"</session>"):
            return ET.parse(qr.SESSION).getroot()
    return None


def read_daw_toggles():
    """Ask the running Qtractor to save and read which DAW plugins are on."""
    if (root := save_session()) is None:
        return {}
    out = {}
    for p in root.iter("plugin"):
        if (label := p.findtext("label")) in DAW_LABELS:
            out[DAW_LABELS[label]] = p.findtext("activated") == "1"
    return out


def midi_hex(ev):
    """("pc", ch, program) / ("cc", ch, cc, value) -> aseqsend's hex bytes."""
    if ev[0] == "pc":
        return [f"{0xC0 | ev[1]:02X}", f"{ev[2]:02X}"]
    return [f"{0xB0 | ev[1]:02X}", f"{ev[2]:02X}", f"{ev[3]:02X}"]


def fcb_present():
    out = subprocess.run(["aseqdump", "-l"], capture_output=True, text=True).stdout
    return qr.FCB[0] in out


class Engine:
    def __init__(self, on_status, on_event, on_adopt, no_rig=False, source=None):
        self.on_status, self.on_event, self.on_adopt = on_status, on_event, on_adopt
        self.no_rig = no_rig
        self.source = shlex.split(source) if source else ["aseqdump", "-p", qr.FCB[0]]
        self.port = None
        self.fcb = None  # the aseqdump process (display-only and replay modes)
        self.router = None  # the in-process FCB router (when GuitarMood owns the rig)
        self.translator = None
        self._watching = False
        self.stopping = threading.Event()
        self.actions = queue.Queue()
        self.tuner_proc = None
        self.tuning = False
        self.settings = qr.song_settings()

    def status(self, engine, detail="", **extra):
        self.on_status({"engine": engine, "detail": detail, **extra})

    # ---------------------------------------------------------------- start
    def start(self):
        threading.Thread(target=self._start, daemon=True, name="rig-start").start()
        threading.Thread(target=self._act_loop, daemon=True, name="rig-actions").start()

    def _start(self):
        if self.no_rig:
            self.status("demo", "Display only: the rig is not started")
        else:
            try:
                self._bring_up()
            except SystemExit as e:
                return self.status("error", str(e) or "the rig did not start")
            except Exception as e:  # noqa: BLE001 - surface anything on screen, never die silently
                return self.status("error", f"{type(e).__name__}: {e}")
            if self.source[0] == "aseqdump":  # GuitarMood owns the rig: it runs the router
                return self._router_loop()
        self._midi_loop()

    def _bring_up(self):
        if qr.pid():
            subprocess.run(["systemctl", "--user", "stop", HELPER_UNIT], capture_output=True)
            self.status("starting", "Joining the running rig…")
            daw = read_daw_toggles()
            self.tuning = daw.get("tuner", False)
            self.on_adopt(daw)
            self.port = qr.qtractor_port()
            return self.status("live", "Joined the running rig")
        self.status("starting", "Building the session and starting Qtractor…")
        qr.up(helper=False)
        for _ in range(40):
            self.port = qr.qtractor_port()
            if self.port:
                break
            time.sleep(0.25)
        first = next((pc for pc in sorted(self.settings)), None)
        if first is not None and self.port:
            qr.send_song(self.port, first, self.settings)  # the session starts on the first scene bank
        self.status("live", f"Qtractor up · quantum {qr.QUANTUM}")

    # ---------------------------------------------------------------- FCB
    def _routed(self, addr, events):
        """One press, translated: its address first (the bank, exactly), then what the rig got."""
        if addr is not None:
            self.on_event(("addr", *addr))
        for ev in events:
            self.on_event(ev)

    def _router_loop(self):
        """Own the FCB router in-process (the helper unit is stopped). If it can't start, say
        so loudly and keep retrying: without it the pedal does not reach the rig."""
        while not self.stopping.is_set():
            self.router = router_mod.Router(on_events=self._routed, on_status=self.on_status)
            try:
                self.router.run()
            except Exception as e:  # noqa: BLE001 - surface it, never die silently
                self.on_status({"router": False, "routerDetail": f"{type(e).__name__}: {e}"})
            if self.stopping.wait(2):
                return

    def _watch_router(self):
        while not self.stopping.is_set():
            running = router_mod.router_running()
            self.on_status({"router": running, "routerDetail": "" if running else
                            "no FCB router is running: start the rig (qtractor_rig.py up)"})
            self.stopping.wait(3)

    def _midi_loop(self):
        """Display-only or replay: read the FCB (or a log) and decode its addresses the way the
        router does. Live, also watch that some router owns the pedal, and say so if none does."""
        warned = False
        while not self.stopping.is_set():
            live = self.source[0] == "aseqdump"
            if self.translator is None:
                try:
                    self.translator = router_mod.Translator()
                except Exception as e:  # noqa: BLE001 - a broken layout: show the raw stream
                    self.on_status({"warning": f"layout: {e}"})
                    self.translator = router_mod.Translator(table={})
            if live and not self._watching:
                self._watching = True
                threading.Thread(target=self._watch_router, daemon=True, name="router-watch").start()
            if live and not fcb_present():  # aseqdump buffers its banner, so ask the port list
                if not warned:
                    self.on_status({"fcb": False, "fcbDetail": "plug in the FCB1010's USB MIDI cable"})
                    warned = True
                self.stopping.wait(2)
                continue
            try:
                self.fcb = subprocess.Popen(self.source, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
            except OSError as e:
                self.on_status({"fcb": False, "fcbDetail": str(e)})
                return
            self.on_status({"fcb": True, "fcbDetail": ""})
            warned = False
            for line in self.fcb.stdout:
                if ev := parse(line):
                    self._routed(*self.translator.translate(ev))
            self.fcb.wait()
            if self.stopping.is_set():
                return
            if not live:  # a replayed log: done
                return self.on_status({"fcb": True, "fcbDetail": "replay finished"})
            err = (self.fcb.stderr.read() or "").strip().splitlines()
            self.on_status({"fcb": False, "fcbDetail": err[-1] if err else "the FCB1010 went away"})
            warned = True
            self.stopping.wait(2)

    # ---------------------------------------------------------------- helper
    def act(self, action):
        self.actions.put(action)

    def send(self, events):
        """A switch pressed on screen: the same messages, into the same Qtractor port as the FCB."""
        self.actions.put(("send", events))

    def _act_loop(self):
        while True:
            kind, arg = self.actions.get()
            if self.no_rig:
                if kind == "retune":  # display only: never touch tone.csv or the presets
                    self.pitch_status(retune.current(), "error", "display only: the tuning wasn't changed")
                continue
            port = qr.qtractor_port() or self.port
            if not port or (kind == "retune" and not qr.pid()):
                if kind == "retune":
                    self.pitch_status(retune.current(), "error", "the rig isn't running: the tuning wasn't changed")
                continue
            try:
                if kind == "send":
                    for ev in arg:
                        subprocess.run(["aseqsend", "-p", port, *midi_hex(ev)], check=True)
                elif kind == "song" and arg in self.settings:
                    qr.send_song(port, arg, self.settings)
                elif kind == "tuner":
                    self.tuning = arg
                    self.tuner_proc = qr.set_tuning(arg, port, self.tuner_proc)
                elif kind == "retune":
                    self._retune(port, *arg)
            except Exception as e:  # noqa: BLE001
                if kind == "retune":
                    self.pitch_status(retune.current(), "error", f"{type(e).__name__}: {e}")
                else:
                    self.on_status({"warning": f"{kind}: {e}"})

    # ---------------------------------------------------------------- guitar tuning
    def pitch_status(self, st, phase, detail=""):
        self.on_status({"pitch": {"st": st, "name": retune.name(st), "phase": phase, "detail": detail,
                                  "choices": retune.choices()}})

    def retune(self, st, scene):
        """The tuning buttons: shift the whole rig `st` semitones from the guitar's own tuning.
        `scene` = (CC 80, CC 81) to restore on the heavy instance after its reload, or None."""
        self.actions.put(("retune", (st, scene)))

    def _send(self, port, *events):
        for ev in events:
            subprocess.run(["aseqsend", "-p", port, *midi_hex(ev)], check=True)

    def _load(self, port, ch, rig, timeout=8.0):
        """Program Change `rig` on channel `ch`; wait until TONE3000 logs it as loaded."""
        mark = retune.log_mark()
        self._send(port, ("pc", ch, rig["pc"]))
        end = time.monotonic() + timeout
        while time.monotonic() < end:
            if rig["name"] in retune.log_loaded(mark):
                return
            time.sleep(0.05)
        raise TimeoutError(f"TONE3000 didn't log loading {rig['name']} within {timeout:g} s")

    def _retune(self, port, st, scene):
        self.pitch_status(st, "applying", f"writing the presets for {retune.name(st)}…")
        retune.set_tuning(st)
        rigs = t3k.load_rigs()
        by_name = {r["name"]: r for r in rigs}
        root = save_session()
        if root is None:
            return self.pitch_status(st, "saved", "Qtractor didn't save its session: switch songs to hear it")
        loaded = retune.instances(root)
        self.pitch_status(st, "applying", "reloading the presets…")
        for label, ch in retune.INSTANCES.items():
            rig = by_name.get((loaded.get(label) or {}).get("preset"))
            if rig is None:  # an instance on a preset rigs/ no longer has: leave it be
                continue
            try:
                self._load(port, ch, retune.hop(rig["pc"], rigs))
            finally:  # whatever happened on the way out, never leave an instance on the hop preset
                self._load(port, ch, rig)
        if scene:  # a PC puts the heavy instance's input drive back to the preset's: restore the scene
            drive, select = scene
            self._send(port, ("cc", 0, t3k.SCENE_DRIVE_CC, drive), ("cc", 0, t3k.SCENE_SELECT_CC, select))
        root = save_session()
        if root is None:
            return self.pitch_status(st, "saved", "reloaded, but Qtractor didn't save its session to confirm")
        if bad := retune.mismatches(retune.instances(root), rigs):
            return self.pitch_status(st, "error", "not live on " + "; ".join(bad))
        self.pitch_status(st, "live")
        self.on_status({"pitchReloaded": time.monotonic()})

    # ---------------------------------------------------------------- stop
    def stop(self):
        """Blocking: close the tuner, stop reading the FCB, save and quit Qtractor."""
        self.stopping.set()
        if self.fcb and self.fcb.poll() is None:
            self.fcb.terminate()
        if self.router:
            self.router.stop()  # closes its ALSA client within 0.25 s
            self.router.closed.wait(2)
        if self.no_rig:
            return
        self.status("stopping", "Saving the session and closing Qtractor…")
        if self.tuning:
            qr.tuner_off(self.tuner_proc)
        subprocess.run(["systemctl", "--user", "stop", HELPER_UNIT], capture_output=True)
        try:
            qr.down()
        except SystemExit as e:
            self.status("error", str(e))
            if p := qr.pid():  # never leave a half-closed rig behind
                os.kill(p, signal.SIGKILL)
