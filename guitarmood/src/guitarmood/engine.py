"""The rig's lifetime: start Qtractor (or join the one already running), do the helper's
job (per-song echo + harmony key, the SW10 tuner), and shut everything down cleanly.

GuitarMood replaces the `qtractor-rig-helper` user unit while it runs: one process reads the
FCB, so the tuner state on screen is the tuner state for real.
"""

import os
import queue
import shlex
import signal
import subprocess
import threading
import time
import xml.etree.ElementTree as ET

from .board import qr
from .state import parse

HELPER_UNIT = "qtractor-rig-helper"
DAW_LABELS = {"Wah": "wah", "Octaver": "octaver", "Harmony In": "harmony", "Tuner Mute": "tuner"}


def read_daw_toggles():
    """Ask the running Qtractor to save (SIGUSR1) and read which DAW plugins are on."""
    before = qr.SESSION.stat().st_mtime if qr.SESSION.exists() else 0
    os.kill(qr.pid(), signal.SIGUSR1)
    for _ in range(40):
        time.sleep(0.25)
        if qr.SESSION.exists() and qr.SESSION.stat().st_mtime != before \
                and qr.SESSION.read_bytes().rstrip().endswith(b"</session>"):
            break
    else:
        return {}
    out = {}
    for p in ET.parse(qr.SESSION).getroot().iter("plugin"):
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
        self.fcb = None  # the aseqdump process
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
    def _midi_loop(self):
        warned = False
        while not self.stopping.is_set():
            live = self.source[0] == "aseqdump"
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
                    self.on_event(ev)
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
                continue
            port = qr.qtractor_port() or self.port
            if not port:
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
            except Exception as e:  # noqa: BLE001
                self.on_status({"warning": f"{kind}: {e}"})

    # ---------------------------------------------------------------- stop
    def stop(self):
        """Blocking: close the tuner, stop reading the FCB, save and quit Qtractor."""
        self.stopping.set()
        if self.fcb and self.fcb.poll() is None:
            self.fcb.terminate()
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
