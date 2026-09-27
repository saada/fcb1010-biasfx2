"""GuitarMood: open it and the rig starts; close it and the rig is saved and shut down.

  uv run guitarmood                 start (or join) the rig and show the board
  uv run guitarmood install         add GuitarMood to the Omarchy launcher (+ Hyprland rules)
  uv run guitarmood uninstall       take it out again

Environment, for testing without the rig:
  GUITARMOOD_NO_RIG=1               display only: never start or stop Qtractor
  GUITARMOOD_MIDI_SOURCE="cmd ..."  read aseqdump-format lines from a command (a replayed log)
"""

import os
import signal
import sys
import threading
from pathlib import Path

from PySide6.QtCore import Property, QObject, QTimer, QUrl, Signal, Slot
from PySide6.QtGui import QFont, QGuiApplication, QIcon
from PySide6.QtQml import QQmlApplicationEngine
from PySide6.QtQuick import QQuickWindow  # noqa: F401 - root objects come back as QQuickWindow

from . import theme
from .board import Board
from .engine import Engine
from .state import RigState

APP_ID = "guitarmood"
HERE = Path(__file__).resolve().parent


class Bridge(QObject):
    stateChanged = Signal()
    statusChanged = Signal()
    themeChanged = Signal()
    finished = Signal()
    _event = Signal(object)
    _status = Signal(object)
    _adopt = Signal(object)

    def __init__(self):
        super().__init__()
        self.board = Board()
        self.rig = RigState(self.board)
        self._state = self.rig.snapshot()
        self._statusv = {"engine": "starting", "detail": "", "fcb": None, "fcbDetail": "", "warning": ""}
        self._colors, self._stamp = theme.palette(), theme.stamp()
        self._font = theme.font_family()
        self._dirty = False
        self.closing = False
        self._event.connect(self._on_event)
        self._status.connect(self._on_status)
        self._adopt.connect(self._on_adopt)
        self.engine = Engine(self._status.emit, self._event.emit, self._adopt.emit,
                             no_rig=bool(os.environ.get("GUITARMOOD_NO_RIG")),
                             source=os.environ.get("GUITARMOOD_MIDI_SOURCE"))
        # One timer: push at most 30 snapshots/s (EXP pedals stream fast; the rig needs the CPU),
        # and give Python a chance to run its signal handlers.
        self._tick = QTimer(self, interval=33, timeout=self._flush)
        self._tick.start()
        self._themer = QTimer(self, interval=2000, timeout=self._check_theme)
        self._themer.start()

    # ---------------------------------------------------------------- QML API
    @Property("QVariantMap", notify=stateChanged)
    def state(self):
        return self._state

    @Property("QVariantMap", notify=statusChanged)
    def status(self):
        return self._statusv

    @Property("QVariantMap", notify=themeChanged)
    def colors(self):
        return self._colors

    @Property(str, notify=themeChanged)
    def font(self):
        return self._font

    @Slot(int)
    def pressSwitch(self, sw):
        """Click or key: press that switch in the bank on screen."""
        self.press(self.rig.bank, sw)

    @Slot(int)
    def loadBank(self, bank):
        """Setlist, UP/DOWN tile or arrow key: press that bank's first switch (RHYTHM / first song)."""
        self.press(bank, 1)

    def press(self, bank, sw):
        events = self.board.messages(bank, sw)
        if not events or self.closing:
            return
        self.engine.send(events)  # queued before the helper's reaction to them
        for ev in events:
            self._on_event(ev)
        self.rig.via = "screen"
        self._flush()

    @Slot()
    def shutdown(self):
        """Window closed, SIGTERM or SIGINT: stop the rig off the UI thread, then quit."""
        if self.closing:
            return
        self.closing = True

        def run():
            self.engine.stop()
            self.finished.emit()

        threading.Thread(target=run, daemon=True, name="rig-stop").start()

    # ---------------------------------------------------------------- internals
    def _on_event(self, ev, via="pedal"):
        if not (ev[0] == "cc" and ev[2] in self.rig.exp):  # rocking a pedal isn't a press
            self.rig.via = via
        if action := self.rig.feed(ev):
            self.engine.act(action)
        self._dirty = True

    def _on_status(self, patch):
        self._statusv = {**self._statusv, **patch}
        self.statusChanged.emit()

    def _on_adopt(self, daw):
        self.rig.adopt(daw)
        self._dirty = True

    def _flush(self):
        if self._dirty:
            self._dirty = False
            self._state = self.rig.snapshot()
            self.stateChanged.emit()

    def _check_theme(self):
        if (s := theme.stamp()) != self._stamp:
            self._stamp, self._colors = s, theme.palette()
            self.themeChanged.emit()


def run():
    QGuiApplication.setDesktopFileName(APP_ID)  # Wayland app_id = Hyprland class "guitarmood"
    app = QGuiApplication(sys.argv)
    app.setApplicationName("GuitarMood")
    app.setWindowIcon(QIcon(str(HERE / "guitarmood.svg")))
    bridge = Bridge()
    app.setFont(QFont(bridge.font))
    engine = QQmlApplicationEngine()
    engine.rootContext().setContextProperty("rig", bridge)
    engine.load(QUrl.fromLocalFile(str(HERE / "qml/Main.qml")))
    if not engine.rootObjects():
        sys.exit("GuitarMood: the window failed to load")
    bridge.finished.connect(lambda: app.exit(0))  # not quit(): it asks the window, which refuses until the rig is down
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda *_: bridge.shutdown())
    if shot := os.environ.get("GUITARMOOD_SNAPSHOT"):  # tests: render offscreen at WxH, save a PNG, quit
        path, _, size = shot.partition(",")
        win = engine.rootObjects()[0]
        if size:
            w, h = map(int, size.lower().split("x"))
            win.setWidth(w)
            win.setHeight(h)

        def grab():
            try:
                win.grabWindow().save(path)
            finally:
                app.exit(0)

        QTimer.singleShot(int(os.environ.get("GUITARMOOD_SNAPSHOT_MS", "1500")), grab)
    bridge.engine.start()
    sys.exit(app.exec())


def main():
    cmd = sys.argv[1] if len(sys.argv) > 1 else ""
    if cmd in ("install", "uninstall"):
        from . import install
        return getattr(install, cmd)()
    if cmd in ("-h", "--help", "help"):
        return print(__doc__)
    run()


if __name__ == "__main__":
    main()
