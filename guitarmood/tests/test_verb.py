"""tone.csv verb_db (experiments D30): one dB knob moves every always-on reverb of a preset,
in TONE3000 and in the DAW tails, and the DAW tail kernels carry their block's mix."""

import json
import math
import struct

import pytest

from guitarmood.board import qr, t3k


def rig(stem, sheet_rows=None, star=None):
    sheet = t3k.empty_sheet()
    sheet["rows"] = sheet_rows or {}
    sheet["star"] = star or {}
    sheet["tags"] = {s: [] for s in sheet["rows"]}
    raw = json.loads((t3k.RIGS / f"{stem}.json").read_text()) | {"_file": f"{stem}.json"}
    return t3k.apply_tone(raw, sheet)


def sites(r):
    return [b for side in ("left", "right") for b in r.get(side, []) if t3k.is_verb(b)]


def ratio_db(m):
    return 20 * math.log10(m / (1 - m))


def test_verb_db_scales_every_reverb_site():
    """Maiden Clean has both kinds: an ambience hall (mix) and an echo with a summed reverb
    (level_db, with the mix holding the repeats). `*` and the preset cell add."""
    base = rig("01-maiden-clean")
    moved = rig("01-maiden-clean", {"01-maiden-clean": {"verb_db": -3}}, star={"verb_db": -3})
    assert moved["_verb_db"] == -6
    a, b = sites(base), sites(moved)
    assert len(a) == len(b) == 4  # echo + ambience on both chains
    for old, new in zip(a, b):
        if old["type"] == "ir":
            assert ratio_db(new["mix"]) - ratio_db(old["mix"]) == pytest.approx(-6, abs=0.01)
        else:
            assert new["reverb"]["level_db"] == old["reverb"]["level_db"] - 6
            key = (old["delay_ms"], old["feedback"], old.get("cutoff_hz", 3500.0),
                   json.dumps({k: v for k, v in old["reverb"].items() if k != "level_db"}, sort_keys=True))
            echo_old = old["mix"] * t3k._echo_scale(*key, old["reverb"]["level_db"])
            echo_new = new["mix"] * t3k._echo_scale(*key, new["reverb"]["level_db"])
            assert echo_new == pytest.approx(echo_old, rel=1e-3)  # the repeats keep their level


def test_verb_db_reaches_the_daw_tails():
    """The moved reverbs take the scaled mix, and each zeroconvolv kernel is the mix times
    TONE3000's unit-energy kernel (dry 1 - mix, wet 0 dB in the plugin)."""
    base = rig("01-maiden-clean")
    moved = rig("01-maiden-clean", {"01-maiden-clean": {"verb_db": -6}})
    _, before = t3k.build_preset_and_tails(base)
    _, after = t3k.build_preset_and_tails(moved)
    assert before and len(before) == len(after)
    amb_old = next(s for s in before if "Room" in s["labels"][0])
    amb_new = next(s for s in after if "Room" in s["labels"][0])
    assert ratio_db(amb_new["mix"]) - ratio_db(amb_old["mix"]) == pytest.approx(-6, abs=0.01)
    for st in after:
        raw = qr.tail_wav(st)
        i = raw.index(b"data") + 8
        x = struct.unpack(f"<{(len(raw) - i) // 4}f", raw[i:])
        energy_l = sum(v * v for v in x[0::2])
        unit = sum(v * v for v in st["irs"][0])
        assert energy_l == pytest.approx(st["mix"] ** 2 * unit, rel=1e-4)


def test_verb_db_leaves_footswitched_echoes_and_errors_without_reverb():
    heavy = rig("00-maiden-heavy", {"00-maiden-heavy": {"verb_db": -6}})
    for b in heavy["left"] + heavy["right"]:
        if b["type"] == "echo":  # SW8/SW9 echoes, off at load: the owner's to switch
            assert b["reverb"]["level_db"] == -8.0 and b["mix"] == 0.25
    with pytest.raises(t3k.ToneError, match="verb_db"):
        rig("10-nirvana", {"10-nirvana": {"verb_db": -3}})
