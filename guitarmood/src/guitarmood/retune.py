"""Guitar tuning for the whole rig: TONE3000's input pitch shifter, set from GuitarMood.

The guitar stays in its own tuning (the JP70, B E A D G B E: rigs/README.md "Voicing, low B and
tuning") and the rig shifts it, so A# standard is -1 semitone and C standard +1. The setting is
the `*` row's `pitch_st` cell in rigs/tone.csv, so `tone3000.py build`, rigsim and the next rig
start all agree with the button. A preset or @tag row with its own `pitch_st` keeps it (the "set"
rule), as tone.csv does everywhere. The shifter adds 11-31 ms while it is on; B standard (0)
turns it off.

Applying it live: rewrite the presets, then make each TONE3000 instance load its preset again.
Re-sending the PC already loaded does nothing (experiments D5), so each instance hops to another
preset and back. The saved session says what every instance has loaded, before and after.
"""

import base64
import csv
import io
import xml.etree.ElementTree as ET
import zlib
from pathlib import Path

from .board import qr, t3k

NOTES = ["C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B"]
HOME = "B"  # the guitar's own low string: 0 semitones
CHOICES = (-2, -1, 0, 1, 2)  # A, A#, B, C, C# standard: the shifter's sweet spot
LOWEST, HIGHEST = -12, 12  # tone.csv's pitch_st range
# TONE3000 instance label in the Qtractor session -> its MIDI channel (qtractor_rig.T3K_CHANNEL).
INSTANCES = {"TONE3000 Heavy": qr.T3K_CHANNEL["heavy"], "TONE3000 Clean": qr.T3K_CHANNEL["clean"],
             "TONE3000 Harmony": qr.T3K_CHANNEL["harmony"]}
LOG = t3k.CONFIG / "TONE3000.log"
LOADED = "[Presets] Loaded preset: "


def note(st):
    return NOTES[(NOTES.index(HOME) + st) % 12]


def name(st):
    return f"{note(st)} standard"


def choices():
    return [{"st": st, "note": note(st), "name": name(st)} for st in CHOICES]


def current(path=None):
    """The rig-wide tuning in tone.csv's `*` row, in semitones from the guitar's own (0 = none)."""
    v = t3k.tone_sheet(path)["star"].get("pitch_st")
    return round(v or 0)


def write_star(st, path=None):
    """Set the `*` row's pitch_st cell and leave every other byte of tone.csv as it was."""
    if not LOWEST <= st <= HIGHEST:
        raise ValueError(f"{st} semitones is outside {LOWEST}..{HIGHEST}")
    path = Path(path or t3k.TONE_CSV)
    lines = path.read_text().splitlines(keepends=True)
    header = [h.strip() for h in next(csv.reader([lines[0]]))]
    col = header.index("pitch_st")
    for i, line in enumerate(lines):
        cells = next(csv.reader([line]), [])
        if cells and cells[0].strip() == "*":
            cells += [""] * (len(header) - len(cells))
            cells[col] = str(st)
            out = io.StringIO()
            csv.writer(out, lineterminator=line[len(line.rstrip("\r\n")):]).writerow(cells)
            lines[i] = out.getvalue()
            t3k.atomic_write(path, "".join(lines).encode())
            return
    raise ValueError(f"{path.name} has no `*` row")


PITCH_PARAMS = ("pitchEnabled", "pitchSemitones")


def write_presets(folder=None, rigs=None):
    """Set the pitch shifter in every live preset to what rigs/ + tone.csv now resolve it to,
    and touch nothing else in them: a full rebuild (`tone3000.py build`, `tune --apply`) takes
    ~12 s and would also ship any rigs/ edit not built yet. -> how many files changed."""
    folder = Path(folder or t3k.PRESETS)
    changed = 0
    for rig in rigs or t3k.load_rigs():
        path = folder / f"{t3k.preset_id(rig['name'])}.t3kpreset"
        if not path.exists():
            continue
        want = t3k.preset_params(rig)
        node = t3k.load_t3kb(path.read_bytes())
        params = node.child("Params")
        have = {q.get("id"): q for q in params.children}
        dirty = False
        for k in PITCH_PARAMS:
            v = float(want.get(k, 0.0))
            if k not in have:
                params.children.append(t3k.Node("Param").set("id", k).set("value", v))
                dirty = True
            elif have[k].get("value") != v:
                have[k].set("value", v)
                dirty = True
        if dirty:
            t3k.atomic_write(path, t3k.dump_preset(node))
            changed += 1
    return changed


def set_tuning(st):
    """tone.csv + the presets. On a failure, tone.csv goes back and the presets with it."""
    before = t3k.TONE_CSV.read_bytes()
    write_star(st)
    try:
        return write_presets()
    except Exception:
        t3k.atomic_write(t3k.TONE_CSV, before)
        write_presets()
        raise


def instances(session):
    """{label: {"preset", "on", "st"}} for each TONE3000 instance in a saved Qtractor session
    (a path or parsed root): its loaded preset and its pitch shifter, from the plugin state."""
    root = ET.parse(session).getroot() if isinstance(session, (str, Path)) else session
    out = {}
    for p in root.iter("plugin"):
        label = p.findtext("label") or ""
        cfg = next((c for c in p.iter("config") if c.get("key") == "state"), None)
        if label not in INSTANCES or cfg is None or not (cfg.text or "").strip():
            continue
        state = t3k.load_t3kb(zlib.decompress(base64.b64decode(cfg.text)[4:]))  # qCompress: 4-byte length
        params = {q.get("id"): q.get("value") for q in state.child("PARAMETERS").children}
        out[label] = {"preset": state.get("activePresetName"), "on": bool(params.get("pitchEnabled")),
                      "st": round(params.get("pitchSemitones") or 0)}
    return out


def expected(preset, rigs=None):
    """(on, st) the pitch shifter should be at with this preset loaded, from rigs/ + tone.csv."""
    rig = next((r for r in rigs or t3k.load_rigs() if r["name"] == preset), None)
    if rig is None:
        return None
    params = t3k.preset_params(rig)
    on = bool(params.get("pitchEnabled"))
    return on, round(params.get("pitchSemitones") or 0) if on else 0


def mismatches(loaded, rigs=None):
    """The instances whose pitch shifter doesn't match their preset as rigs/ now builds it."""
    rigs = rigs or t3k.load_rigs()
    bad = []
    for label, got in loaded.items():
        want = expected(got["preset"], rigs)
        have = (got["on"], got["st"] if got["on"] else 0)
        if want is not None and have != want:
            bad.append(f"{label.removeprefix('TONE3000 ')} ({got['preset']}) at {have[1]:+d} st, wants {want[1]:+d}")
    return bad


def hop(pc, rigs):
    """The preset to hop through when reloading `pc`: the lightest one that isn't it."""
    def weight(r):
        return sum(1 for chain in ("left", "right") for b in r.get(chain, []) if b.get("type") != "insert")
    return min((r for r in rigs if r["pc"] != pc), key=lambda r: (weight(r), r["pc"]))


def log_mark():
    return LOG.stat().st_size if LOG.exists() else 0


def log_loaded(mark):
    """The presets TONE3000 logged as loaded since `mark` (a log size)."""
    if not LOG.exists():
        return []
    with LOG.open("rb") as f:
        f.seek(mark)
        text = f.read().decode(errors="replace")
    return [line[len(LOADED):].strip() for line in text.splitlines() if line.startswith(LOADED)]
