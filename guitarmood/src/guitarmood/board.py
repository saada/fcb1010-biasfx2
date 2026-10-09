"""The board as the rig defines it: what every FCB1010 switch does in every bank.

Everything comes from the rig repo (rig.py, tone3000.py, qtractor_rig.py, rigs/*.json), so
GuitarMood never disagrees with the sysex on the pedal or the Qtractor session.
"""

import os
import re
import sys
from dataclasses import dataclass, field
from pathlib import Path

REPO = Path(os.environ.get("GUITARMOOD_RIG", Path(__file__).resolve().parents[3]))
sys.path.insert(0, str(REPO))

import qtractor_rig as qr  # noqa: E402
import rig as fcb  # noqa: E402
import tone3000 as t3k  # noqa: E402

# Toggle ids by CC. "daw" toggles are Qtractor plugins and survive a preset load; "t3k"
# toggles are TONE3000 block switches, reset to the preset's own state by every PC on ch 1.
TOGGLES = {
    qr.CC_WAH: ("wah", "daw"),
    qr.CC_OCTAVER: ("octaver", "daw"),
    qr.CC_HARMONY: ("harmony", "daw"),
    22: ("boost", "t3k"),
    23: ("drive", "t3k"),
    24: ("delay", "t3k"),
    30: ("delay2", "t3k"),
    26: ("lead", "t3k"),
    qr.CC_TUNER: ("tuner", "helper"),
}
# Which TONE3000 chain block each t3k toggle switches (left chain, 0-based).
TOGGLE_BLOCK = {"boost": 0, "drive": 1, "delay": 4, "delay2": 5, "lead": 4}
SCENE_LABELS = {"rhythm": "Rhythm", "solo": "Lead", "clean": "Clean", "acoustic": "Acoustic", "crunch": "Crunch"}


@dataclass
class Switch:
    sw: int
    kind: str  # scene | song | toggle | empty
    key: str  # scene name, song PC, or toggle id
    label: str
    detail: str = ""


@dataclass
class Bank:
    number: int
    kind: str  # scenes | songs
    title: str
    subtitle: str
    switches: dict = field(default_factory=dict)  # sw -> Switch
    song: dict = field(default_factory=dict)  # scene banks: the heavy rig's `song` block
    presets: dict = field(default_factory=dict)  # scene kind -> PC


def short(text, limit=48):
    """First clause of a rig label: 'MXR Micro Amp, low gain (clean solo lift)' -> 'MXR Micro Amp'."""
    text = re.split(r" \(|, | — | - ", text or "", maxsplit=1)[0].strip()
    return text if len(text) <= limit else text[: limit - 1].rstrip() + "…"


def key_name(song):
    """'E natural minor (Rime ...)' -> 'E minor'; falls back to the harmony scale."""
    note = song.get("harmony_scale_note", "")
    m = re.match(r"([A-G][#b]?) (?:natural )?(major|minor|dorian|mixolydian|phrygian)", note, re.I)
    if m:
        return f"{m.group(1)} {m.group(2).lower()}"
    return song.get("harmony_scale", "").replace("Major", "major").replace("Minor", "minor")


class Board:
    def __init__(self):
        self.rigs = {r["pc"]: r for r in t3k.load_rigs()}
        self.settings = qr.song_settings()  # heavy PC -> (solo echo ms, harmony scale)
        self.banks = {}
        self.song_pc = {}  # song PC -> (bank, sw)
        self.heavy_pc = {}  # scene bank heavy PC -> bank
        for bank in fcb.BANKS_IN_USE:
            self.banks[bank] = self._scene_bank(bank) if bank in fcb.SCENE_BANKS else self._song_bank(bank)
        self.start_bank = min(fcb.SCENE_BANKS) if fcb.SCENE_BANKS else min(self.banks)
        self._fcb = None  # the FCB layout, built on first use

    def _toggle_switches(self, bank, song=None):
        out = {}
        for sw, cc, name in fcb.toggles(bank):
            tid, _ = TOGGLES[cc]
            label = {"wah": "Wah", "octaver": "Octaver", "harmony": "Harmony", "boost": "Boost",
                     "drive": "Drive", "delay": "Delay", "delay2": "Delay 2", "lead": "Lead", "tuner": "Tuner"}[tid]
            if tid in ("delay", "delay2") and bank in fcb.SCENE_BANKS:  # "Evil That Men Do delay (375 ms)"
                label = short(name, 40).replace(" delay", "") + " Delay"
            detail = {"wah": "EXP A sweeps it", "octaver": "an octave down", "lead": "boost + echo",
                      "tuner": "mutes the rig"}.get(tid, "")
            if tid == "harmony" and song:
                detail = f"a 3rd above · {key_name(song)}"
            out[sw] = Switch(sw, "toggle", tid, label, detail)
        return out

    def _scene_bank(self, bank):
        p = fcb.SCENE_BANKS[bank]
        heavy = self.rigs[p["heavy"][0]]
        song = heavy.get("song", {})
        title = fcb.BANK_NAMES.get(bank, short(heavy["name"]))
        subtitle = f"Brave New World tone · {song.get('reference', '')}".strip(" ·")
        b = Bank(bank, "scenes", title, subtitle, song=song, presets={k: v[0] for k, v in p.items()})
        details = {"rhythm": "both guitars · unity", "solo": "amps pushed · +2 dB",
                   "clean": short(self.rigs[p["clean"][0]]["name"]), "acoustic": short(self.rigs[p["acoustic"][0]]["name"]),
                   "crunch": "volume rolled back"}
        for sw, scene in enumerate(fcb.SCENE_SWITCHES, 1):
            b.switches[sw] = Switch(sw, "scene", scene, SCENE_LABELS[scene], details[scene])
        b.switches.update(self._toggle_switches(bank, song))
        self.heavy_pc[p["heavy"][0]] = bank
        return b

    def _song_bank(self, bank):
        songs = [(sw, pc, name) for b, sw, pc, name in fcb.SONGS if b == bank]
        b = Bank(bank, "songs", fcb.BANK_NAMES.get(bank, f"Songs {bank}"), " · ".join(n for _, _, n in songs))
        for sw in range(1, 6):
            hit = next(((pc, name) for s, pc, name in songs if s == sw), None)
            if hit:
                pc, name = hit
                b.switches[sw] = Switch(sw, "song", str(pc), name, short(self.rigs[pc].get("reference", ""), 40))
                self.song_pc[pc] = (bank, sw)
            else:
                b.switches[sw] = Switch(sw, "empty", "", "—")
        b.switches.update(self._toggle_switches(bank))
        return b

    def messages(self, bank, sw):
        """What the FCB sends when that switch is pressed, straight from the layout rig.py
        uploads: PC 1-3, then CC 1-2 (the pedal's own order). Empty for an unused switch."""
        if self._fcb is None:
            self._fcb = fcb.build()
        f = self._fcb
        p = f.preset[fcb.preset_index(bank, sw)]
        out = []
        for n, ch in ((1, f.pc1_midi_channel), (2, f.pc2_midi_channel), (3, f.pc3_midi_channel)):
            if getattr(p, f"pc{n}_enabled"):
                out.append(("pc", ch, getattr(p, f"pc{n}_program")))
        for n, ch in ((1, f.cc1_midi_channel), (2, f.cc2_midi_channel)):
            if getattr(p, f"cc{n}_enabled"):
                out.append(("cc", ch, getattr(p, f"cc{n}_controller"), getattr(p, f"cc{n}_value")))
        return out

    def block_default(self, pc, toggle):
        """The preset's own on/off state for the block a t3k toggle switches (None = empty slot)."""
        rig = self.rigs.get(pc)
        if rig is None or toggle not in TOGGLE_BLOCK:
            return None
        left = rig.get("left", [])
        i = TOGGLE_BLOCK[toggle]
        if i >= len(left) or left[i].get("type") == "insert":
            return None
        return bool(left[i].get("enabled", True))

    def block_label(self, pc, toggle):
        rig = self.rigs.get(pc)
        if rig is None or toggle not in TOGGLE_BLOCK:
            return ""
        left = rig.get("left", [])
        i = TOGGLE_BLOCK[toggle]
        return short(left[i].get("label", ""), 40) if i < len(left) and left[i].get("type") != "insert" else "empty slot"

    def clean_kind(self, pc):
        return self.rigs.get(pc, {}).get("scene")
