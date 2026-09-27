"""What the rig is doing right now, rebuilt from the FCB1010's MIDI.

The FCB only ever sends fixed messages (a toggle switch sends CC 127 on every press), so the
state follows the rig's own rules:
  - DAW toggles (wah, octaver, harmony) flip on every press and survive preset loads.
  - TONE3000 block toggles (boost, drive, delay, lead) flip too, but every Program Change on
    channel 1 reloads the preset and puts the block back to the preset's saved state.
  - The scene is decided by the CC 81 that ends every scene switch's burst
    (PC ch1, PC ch2, PC ch3, CC 80, CC 81): CC 81 > 63 = the clean instance, which plays
    whatever PC ch 2 loaded (clean or acoustic); otherwise CC 80 picks rhythm/solo/crunch.
  - SW10 (CC 28) toggles the tuner; GuitarMood runs the helper, so this is the real state.
The FCB's UP/DOWN pedals send nothing, so the bank shown is the one the last press came from.
"""

import re
import time

from .board import TOGGLES, Board, fcb, key_name, qr, t3k

LINE = re.compile(r"(Program change|Control change)\s+(\d+), (?:program (\d+)|controller (\d+), value (\d+))")


def parse(line):
    """One aseqdump line -> ("pc", ch, program) | ("cc", ch, cc, value) | None."""
    m = LINE.search(line)
    if not m:
        return None
    if m.group(1) == "Program change":
        return ("pc", int(m.group(2)), int(m.group(3)))
    return ("cc", int(m.group(2)), int(m.group(4)), int(m.group(5)))


SCENE_BY_DRIVE = {drive: name for name, (drive, select, kind) in t3k.SCENES.items() if not select and name != "rhythm"}


class RigState:
    def __init__(self, board: Board):
        self.board = board
        self.bank = board.start_bank
        start = board.banks[self.bank]
        self.heavy_pc = start.presets.get("heavy")
        self.clean_pc = start.presets.get("clean")
        self.harmony_pc = start.presets.get("harmony")
        self.drive, self.select = t3k.SCENES["rhythm"][0], 0
        self.scene = "rhythm" if start.kind == "scenes" else None
        self.song_sw = None
        self.daw = {"wah": False, "octaver": False, "harmony": False}
        self.flips = {}  # t3k toggle -> presses since the last preset load
        self.t3k_known = True
        self.tuning = False
        self.exp = {qr.CC_WAH_SWEEP: None, fcb.VOLUME_CC: None}  # EXP A, EXP B: unknown until moved
        self.last = "Session start: " + start.title
        self.last_sw = None
        self.synced = True
        self.presses = 0
        self.via = "pedal"  # or "screen": the FCB's own display doesn't follow screen presses
        self.last_at = time.monotonic()

    # ---------------------------------------------------------------- input
    def feed(self, event):
        """Apply one MIDI event. Returns an action for the helper side, or None:
        ("song", heavy_pc) on a scene bank's heavy PC, ("tuner", on) on SW10."""
        kind, ch, *rest = event
        self.last_at = time.monotonic()
        if kind == "pc":
            (program,) = rest
            if ch == 0:
                return self._heavy_pc(program)
            if ch == 1:
                self.clean_pc = program
            elif ch == 2:
                self.harmony_pc = program
            return None
        cc, value = rest
        if ch != 0:
            return None
        if cc in self.exp:
            self.exp[cc] = value / 127
            return None
        if cc == t3k.SCENE_DRIVE_CC:
            self.drive = value
            return None
        if cc == t3k.SCENE_SELECT_CC:
            self.select = value
            self._settle_scene()
            return None
        if cc in TOGGLES and value >= 64:
            tid, where = TOGGLES[cc]
            self.presses += 1
            self.last_sw = self._switch_for(tid)
            if where == "daw":
                self.daw[tid] = not self.daw[tid]
                self.last = f"{self._label(tid)} {'on' if self.daw[tid] else 'off'}"
            elif where == "t3k":
                self.flips[tid] = self.flips.get(tid, 0) + 1
                self.last = f"{self._label(tid)} {'on' if self.toggle_on(tid) else 'off'}"
            else:
                self.tuning = not self.tuning
                self.last = "Tuner on: rig muted" if self.tuning else "Tuner off: playing"
                return ("tuner", self.tuning)
        return None

    def _heavy_pc(self, program):
        self.heavy_pc = program
        self.flips.clear()
        self.t3k_known = True
        self.synced = True
        self.presses += 1
        if program in self.board.song_pc:
            self.bank, self.song_sw = self.board.song_pc[program]
            self.scene = None
            self.last_sw = self.song_sw
            self.last = f"Song: {self.board.banks[self.bank].switches[self.song_sw].label}"
            return None
        if program in self.board.heavy_pc:
            self.bank = self.board.heavy_pc[program]
            self.song_sw = None
            return ("song", program)
        return None

    def _settle_scene(self):
        bank = self.board.banks[self.bank]
        if bank.kind != "scenes":
            return
        if self.select > 63:
            self.scene = "acoustic" if self.board.clean_kind(self.clean_pc) == "acoustic" else "clean"
        else:
            self.scene = SCENE_BY_DRIVE.get(self.drive, "rhythm")
        self.presses += 1
        self.synced = True
        self.last_sw = next(sw for sw, s in bank.switches.items() if s.key == self.scene)
        self.last = f"{bank.switches[self.last_sw].label}"

    # ---------------------------------------------------------------- queries
    def toggle_on(self, tid):
        """True/False, or None when the rig doesn't know (adopted session, empty block)."""
        if tid in self.daw:
            return self.daw[tid]
        if tid == "tuner":
            return self.tuning
        if not self.t3k_known:
            return None
        odd = self.flips.get(tid, 0) % 2 == 1
        if tid == "lead":  # boost + echo together: "on" = switched since the preset loaded
            return odd
        default = self.board.block_default(self.heavy_pc, tid)
        return None if default is None else default ^ odd

    def _switch_for(self, tid):
        bank = self.board.banks[self.bank]
        return next((sw for sw, s in bank.switches.items() if s.key == tid), None)

    def _label(self, tid):
        sw = self._switch_for(tid)
        return self.board.banks[self.bank].switches[sw].label if sw else tid.title()

    def adopt(self, daw):
        """A rig that was already running: take the DAW toggles from its saved session and
        mark what only a fresh press can tell (bank, scene, TONE3000 blocks) as unknown."""
        self.daw.update({k: v for k, v in daw.items() if k in self.daw})
        self.tuning = daw.get("tuner", False)
        self.t3k_known = False
        self.synced = False
        self.last = "Joined a running rig: press a scene switch to sync"

    # ---------------------------------------------------------------- output
    def snapshot(self):
        b = self.board
        bank = b.banks[self.bank]
        switches = []
        for sw in range(1, 11):
            s = bank.switches.get(sw)
            if s is None:
                switches.append({"sw": sw, "kind": "empty", "key": "", "label": "—", "detail": "", "on": False,
                                 "known": True, "last": False})
                continue
            detail = s.detail
            if s.kind == "scene":  # never light a guess: an adopted rig shows "?" until a press
                on, known = self.synced and s.key == self.scene, self.synced
            elif s.kind == "song":
                on, known = self.synced and self.song_sw == sw, self.synced
            elif s.kind == "toggle":
                state = self.toggle_on(s.key)
                on, known = bool(state), state is not None
                if s.key in ("boost", "drive", "delay") and not detail:
                    detail = b.block_label(self.heavy_pc, s.key)
                    if b.block_default(self.heavy_pc, s.key) is None and self.t3k_known:
                        known = True  # an empty slot: the switch does nothing in this preset
                        detail = "empty slot in this preset"
            else:
                on, known = False, True
            switches.append({"sw": sw, "kind": s.kind, "key": s.key, "label": s.label, "detail": detail,
                             "on": on, "known": known, "last": sw == self.last_sw})
        song = bank.song
        ms = b.settings.get(bank.presets.get("heavy"), (None,))[0] if bank.kind == "scenes" else None
        facts = []
        if bank.kind == "scenes":
            facts = [f for f in (key_name(song) and f"Key {key_name(song)}",
                                 song.get("tempo_bpm") and f"{song['tempo_bpm']} BPM",
                                 ms and f"Solo echo {ms} ms") if f]
        playing = ""
        if bank.kind == "scenes" and self.scene and self.synced:
            playing = bank.switches[next(sw for sw, s in bank.switches.items() if s.key == self.scene)].label
        elif self.song_sw:
            playing = bank.switches[self.song_sw].label
        numbers = sorted(b.banks)
        i = numbers.index(self.bank)
        nb = lambda n: {"number": n, "title": b.banks[n].title, "subtitle": b.banks[n].subtitle} if n is not None else None
        return {
            "bank": self.bank, "bankKind": bank.kind, "title": bank.title, "subtitle": bank.subtitle,
            "facts": facts, "playing": playing, "synced": self.synced,
            "switches": switches,
            "expA": {"cc": qr.CC_WAH_SWEEP, "label": "Wah sweep", "value": self.exp[qr.CC_WAH_SWEEP],
                     "live": self.daw["wah"]},
            "expB": {"cc": fcb.VOLUME_CC, "label": "Volume", "value": self.exp[fcb.VOLUME_CC], "live": True},
            "tuning": self.tuning,
            "last": self.last + (" · on screen" if self.via == "screen" else ""),
            "via": self.via,
            "up": nb(numbers[i + 1] if i + 1 < len(numbers) else None),
            "down": nb(numbers[i - 1] if i > 0 else None),
            "banks": [dict(nb(n), current=n == self.bank, kind=b.banks[n].kind) for n in numbers],
        }

