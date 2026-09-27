from pathlib import Path

import pytest

from guitarmood.board import Board
from guitarmood.state import RigState, parse

LOG = Path(__file__).parent / "fixtures/fcb-live.log"  # the first live session on the final FCB layout


@pytest.fixture(scope="module")
def board():
    return Board()


def replay(board, lines):
    st, actions = RigState(board), []
    for line in lines:
        if (ev := parse(line)) and (act := st.feed(ev)):
            actions.append(act)
    return st, actions


def burst(*events):
    out = []
    for e in events:
        if e[0] == "pc":
            out.append(f" 24:0   Program change          {e[1]}, program {e[2]}")
        else:
            out.append(f" 24:0   Control change          {e[1]}, controller {e[2]}, value {e[3]}")
    return out


def by_sw(snap):
    return {s["sw"]: s for s in snap["switches"]}


def test_parse():
    assert parse(" 24:0   Program change          1, program 16") == ("pc", 1, 16)
    assert parse(" 24:0   Control change          0, controller 81, value 127") == ("cc", 0, 81, 127)
    assert parse(" 24:0   Note on                 0, note 112, velocity 100") is None


def test_board_matches_rig(board):
    assert board.start_bank == 3
    assert board.banks[3].title == "The Number of the Beast"
    assert board.banks[8].subtitle.startswith("1992 · Fear of the Dark")
    assert [board.banks[5].switches[sw].label for sw in range(1, 11)] == \
        ["Rhythm", "Solo", "Clean", "Acoustic", "Crunch", "Wah", "Harmony", "Boost", "Delay + Verb", "Tuner"]
    assert board.banks[0].switches[1].label == "Comfortably Numb"
    assert board.banks[1].switches[8].label == "Lead"


def test_scene_burst(board):
    p = board.banks[5].presets  # Powerslave
    st, actions = replay(board, burst(("pc", 0, p["heavy"]), ("pc", 1, p["acoustic"]), ("pc", 2, p["harmony"]),
                                      ("cc", 0, 80, 63), ("cc", 0, 81, 127)))
    snap = st.snapshot()
    assert (snap["bank"], snap["playing"]) == (5, "Acoustic")
    assert actions == [("song", p["heavy"])]  # the helper side sends the song's echo + key
    st2, _ = replay(board, burst(("pc", 0, p["heavy"]), ("pc", 1, p["clean"]), ("pc", 2, p["harmony"]),
                                 ("cc", 0, 80, 63), ("cc", 0, 81, 0), ("cc", 0, 80, 72), ("cc", 0, 81, 0)))
    assert st2.snapshot()["playing"] == "Solo"


def test_t3k_toggles_reset_on_preset_load(board):
    p = board.banks[5].presets
    load = burst(("pc", 0, p["heavy"]), ("pc", 1, p["clean"]), ("pc", 2, p["harmony"]), ("cc", 0, 80, 63), ("cc", 0, 81, 0))
    st, _ = replay(board, load + burst(("cc", 0, 24, 127), ("cc", 0, 25, 127)))
    sw = by_sw(st.snapshot())
    assert sw[9]["on"] and sw[7]["on"]  # delay (off in the preset) flipped on; harmony on
    for line in load:
        st.feed(parse(line))
    sw = by_sw(st.snapshot())
    assert not sw[9]["on"], "a preset load puts the delay back to the preset's state"
    assert sw[7]["on"], "harmony is a DAW plugin: it survives the preset load"


def test_tuner_action(board):
    st, actions = replay(board, burst(("cc", 0, 28, 127), ("cc", 0, 28, 127), ("cc", 0, 28, 127)))
    assert actions == [("tuner", True), ("tuner", False), ("tuner", True)]
    assert by_sw(st.snapshot())[10]["on"]


def test_live_session_replay(board):
    st, actions = replay(board, LOG.read_text().splitlines())
    snap = st.snapshot()
    # The session ended on Seventh Son (bank 7) RHYTHM: PC 27 / 28 / 40, CC 80 = 63, CC 81 = 0.
    assert (snap["bank"], snap["title"], snap["playing"]) == (7, "Seventh Son of a Seventh Son", "Rhythm")
    assert snap["synced"]
    assert {a[0] for a in actions} == {"song", "tuner"}
    assert snap["tuning"] == (sum(a[0] == "tuner" for a in actions) % 2 == 1)
    assert snap["expA"]["value"] is not None and snap["expB"]["value"] is not None
    assert snap["up"]["number"] == 8 and snap["down"]["number"] == 6


def test_screen_press_equals_pedal_burst(board):
    """A click sends what the pedal sends: the last live burst was Seventh Son RHYTHM."""
    assert board.messages(7, 1) == [("pc", 0, 27), ("pc", 1, 28), ("pc", 2, 40), ("cc", 0, 80, 63), ("cc", 0, 81, 0)]
    assert board.messages(7, 2) == [("cc", 0, 80, 72), ("cc", 0, 81, 0)]  # SOLO: no PC, never wipes CC 80


def test_every_switch_lights_itself(board):
    """For every bank and switch, the layout's own messages light that switch on screen."""
    for bank in board.banks:
        for sw in range(1, 11):
            events = board.messages(bank, sw)
            if not events:
                continue
            st = RigState(board)
            for ev in board.messages(bank, 1):  # land in the bank first, as the screen always is
                st.feed(ev)
            if sw == 1:
                st = RigState(board)
            for ev in events:
                st.feed(ev)
            snap = st.snapshot()
            assert snap["bank"] == bank, (bank, sw)
            s = by_sw(snap)[sw]
            if s["key"] in ("boost", "drive", "delay") and not s["known"]:
                continue
            if s["key"] in ("boost", "drive", "delay") and board.block_default(st.heavy_pc, s["key"]):
                assert not s["on"], (bank, sw)  # on in the preset: one press turns it off
            else:
                assert s["on"], (bank, sw, s)


def test_midi_hex():
    from guitarmood.engine import midi_hex
    assert midi_hex(("pc", 1, 22)) == ["C1", "16"]
    assert midi_hex(("cc", 0, 81, 127)) == ["B0", "51", "7F"]


def test_adopted_rig_never_guesses(board):
    st = RigState(board)
    st.adopt({"wah": True, "harmony": False, "tuner": False})
    sw = by_sw(st.snapshot())
    assert not any(s["on"] for s in sw.values() if s["kind"] == "scene"), "no scene lit before a press"
    assert not sw[1]["known"] and not sw[8]["known"], "scenes and TONE3000 blocks are unknown"
    assert sw[6]["on"] and sw[6]["known"], "wah comes from the saved Qtractor session"
    p = board.banks[4].presets
    for line in burst(("pc", 0, p["heavy"]), ("pc", 1, p["clean"]), ("pc", 2, p["harmony"]),
                      ("cc", 0, 80, 63), ("cc", 0, 81, 0)):
        st.feed(parse(line))
    snap = st.snapshot()
    assert snap["synced"] and snap["bank"] == 4 and snap["playing"] == "Rhythm" and by_sw(snap)[8]["known"]
