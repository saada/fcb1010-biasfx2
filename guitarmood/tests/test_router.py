"""The universal flash and the FCB router: every address the pedal can send becomes exactly
the messages rig.py's layout defines for that (bank, switch)."""

import math
import os
import subprocess
import threading
import time

import pytest

from guitarmood.board import Board, fcb, qr
from guitarmood.state import RigState

router = qr.fcb_router
LAYOUT = {(b, s): [tuple(e) for e in m] for b, s, m in fcb.layout()}


def test_universal_flash_verifies():
    data = fcb.verify(fcb.universal(), universal=True)
    assert len(data) == 2352
    # the old per-layout config still verifies too (send --legacy)
    fcb.verify(fcb.build())


def test_addresses_are_unique_and_decode():
    seen = {}
    for bank in range(10):
        for sw in range(1, 11):
            a = router.address(bank, sw)
            assert a[1] == 15  # MIDI channel 16
            assert router.decode(a) == (bank, sw)
            seen[a] = (bank, sw)
    assert len(seen) == 100


def test_every_address_sends_exactly_the_layout():
    """For every bank x switch: what build() puts on the switch, in the pedal's order and
    channels, then the per-song chorus CCs after a channel-1 PC."""
    f = fcb.build()
    t = router.Translator(LAYOUT)
    chorus = qr.chorus_settings()
    mapped = 0
    for bank in range(10):
        for sw in range(1, 11):
            want = fcb.messages(f, bank, sw)
            pc = next((e[2] for e in want if e[0] == "pc" and e[1] == 0), None)
            if pc is not None:
                want = want + fcb.extras(pc, chorus)
            assert t.translate(router.address(bank, sw)) == ((bank, sw), want), (bank, sw)
            mapped += bool(want)
    assert mapped == len(LAYOUT) >= 50


def test_song_switch_carries_its_chorus():
    t = router.Translator(LAYOUT)
    _, events = t.translate(router.address(2, 1))  # 80s Clean SW1
    pc = events[0][2]
    assert events[0] == ("pc", 0, pc)
    assert events[-3:] == [("cc", 0, cc, v) for cc, v in qr.chorus_ccs(pc)]


def test_non_addresses_pass_through_and_stray_ch16_drops():
    t = router.Translator(LAYOUT)
    assert t.translate(("cc", 0, 27, 90)) == (None, [("cc", 0, 27, 90)])  # EXP A
    assert t.translate(("cc", 0, 7, 10)) == (None, [("cc", 0, 7, router.volume_cc(10))])  # EXP B: tapered
    assert t.translate(("cc", 1, 7, 10)) == (None, [("cc", 1, 7, 10)])  # CC 7 on another channel isn't EXP B
    assert t.translate(("pc", 0, 3)) == (None, [("pc", 0, 3)])  # the old flash, before the one-time flash
    assert t.translate(("cc", 15, 50, 1)) == (None, [])
    assert t.translate(("pc", 15, 7)) == (None, [])  # x5-x9 are not switches


def test_parse_aseqdump():
    assert router.parse_aseqdump(" 24:0   Program change         15, program 41") == ("pc", 15, 41)
    assert router.parse_aseqdump(" 24:0   Control change         15, controller 104, value 3") == ("cc", 15, 104, 3)
    assert router.parse_aseqdump(" 24:0   Note on                 0, note 112, velocity 100") is None


def test_address_tells_the_board_its_bank():
    board = Board()
    st = RigState(board)
    st.feed(("addr", 3, 9))  # a toggle press in bank 3: CC values alone could not say which bank
    for ev in LAYOUT[(3, 9)]:
        st.feed(ev)
    assert st.bank == 3 and st.last_sw == 9
    st.feed(("addr", 7, 1))  # not in the layout: say so, keep the bank
    assert st.bank == 3 and "nothing on it" in st.last


@pytest.mark.skipif(not os.path.exists("/dev/snd/seq"), reason="no ALSA sequencer")
def test_router_on_test_ports_with_aseqsend():
    """A real router between two test ports: inject universal addresses with aseqsend, read
    what comes out, compare with the layout. Exercises the ALSA path and the connections."""
    from alsa_midi import PortCaps, SequencerClient

    sink = SequencerClient("FCB Router test sink")
    rx = sink.create_port("rx", PortCaps.WRITE | PortCaps.SUBS_WRITE)
    src = SequencerClient("FCB Router test source")
    src.create_port("tx", PortCaps.READ | PortCaps.SUBS_READ)
    got = []
    r = router.Router(on_events=lambda addr, events: got.append((addr, events)),
                      source=("FCB Router test source", "tx"), dest=("FCB Router test sink", "rx"),
                      name="FCB Router (test)", table=LAYOUT, watch=False, restore_direct=False)
    threading.Thread(target=r.run, daemon=True).start()
    assert r.ready.wait(5)
    port = f"{r.client.client_id}:{r.inp.port_id}"
    try:
        for (bank, sw), want in sorted(LAYOUT.items()):
            a = router.address(bank, sw)
            msg = ["CF", f"{a[2]:02X}"] if a[0] == "pc" else ["BF", f"{a[2]:02X}", f"{a[3]:02X}"]
            subprocess.run(["aseqsend", "-p", port, *msg], check=True)
            out = []
            deadline = time.monotonic() + 2
            while len(out) < len(want) and time.monotonic() < deadline:
                e = sink.event_input(timeout=0.5)
                if e is not None and e.type.name == "PGMCHANGE":
                    out.append(("pc", e.channel, e.value))
                elif e is not None and e.type.name == "CONTROLLER":
                    out.append(("cc", e.channel, e.param, e.value))
            assert out == want, (bank, sw)
        subprocess.run(["aseqsend", "-p", port, "B0", "1B", "40"], check=True)  # EXP A passes through
        e = None
        while e is None or e.type.name != "CONTROLLER":
            e = sink.event_input(timeout=2)
        assert (e.channel, e.param, e.value) == (0, 27, 64)
        assert [a for a, _ in got[:len(LAYOUT)]] == sorted(LAYOUT)
    finally:
        r.stop()
        r.closed.wait(2)
        sink.close()
        src.close()


# ---------------------------------------------------------------- EXP B volume (experiments D22)
def test_volume_taper():
    """Heel is silent, toe is exactly 0 dB (unity: the presets' YouTube-matched level), the curve
    never steps back down, and the session starts at 0 dB."""
    db = lambda raw: router.volume_db(router.volume_cc(raw))
    assert router.volume_cc(0) == 0 and db(0) is None
    assert router.volume_cc(127) == router.VOLUME_NOMINAL_CC == 127 and abs(db(127)) < 1e-9
    assert -43 < db(1) < -35  # just off the heel: the floor (CC 1, the stage's smallest step, is -42 dB)
    ccs = [router.volume_cc(raw) for raw in range(128)]
    assert ccs == sorted(ccs)  # monotonic
    for raw in range(1, 128):  # the board reads the travel back from the CC it got
        assert abs(router.volume_travel(router.volume_cc(raw)) - raw / 127) < 0.1


def test_volume_is_a_daw_stage_no_preset_touches():
    """CC 7 reaches no TONE3000 (its MIDI maps), only the Rig bus's Volume stage, which sits
    before the limiter and starts at the nominal level."""
    assert all(cc != 7 for _, cc in qr.t3k.MIDI_MAP + qr.HEAVY_MIDI_MAP + qr.TONE3000_MIDI_MAP)
    assert "outputLevel" not in {t for t, _ in qr.t3k.MIDI_MAP}
    labels = [spec[0] for spec in qr.BUS_CHAIN]
    assert labels.index("Volume") == labels.index("Limiter") - 1
    label, uri, active, params, ccs = qr.VOLUME
    assert active and ccs == {7: (15, "hook")}
    gain = params[15][1] * params[21][1]
    assert abs(20 * math.log10(gain)) < 0.01  # starts at 0 dB
    bound = [spec for spec in qr.HEAVY_CHAIN + qr.CLEAN_CHAIN + qr.HARMONY_CHAIN + qr.BUS_CHAIN
             if spec[1] != "tone3000" and 7 in spec[4]]
    assert bound == [qr.VOLUME]  # Qtractor allows one observer per CC


def test_pc_burst_does_not_change_volume():
    """No switch's burst (pedal or screen) carries CC 7, so a preset change or a GuitarMood
    click leaves the volume where EXP B put it."""
    t = router.Translator(LAYOUT)
    for (bank, sw) in LAYOUT:
        _, events = t.translate(router.address(bank, sw))
        assert not any(e[0] == "cc" and e[2] == 7 for e in events), (bank, sw, events)
    board = Board()
    for bank in board.banks:
        for sw in range(1, 11):
            assert not any(e[0] == "cc" and e[2] == 7 for e in board.messages(bank, sw)), (bank, sw)
    st = RigState(board)
    st.feed(("cc", 0, 7, router.volume_cc(100)))
    level = st.snapshot()["expB"]["readout"]
    for e in board.messages(0, 1) + board.messages(1, 2):  # a scene burst, then a song's PC
        st.feed(e)
    assert st.snapshot()["expB"]["readout"] == level


def test_board_shows_volume_db():
    st = RigState(Board())
    snap = st.snapshot()["expB"]
    assert snap["value"] is None and snap["readout"] == "+0.0 dB"  # unmoved: 0 dB, as at the toe
    st.feed(("cc", 0, 7, 0))
    assert st.snapshot()["expB"]["readout"] == "mute" and st.snapshot()["expB"]["value"] == 0
    st.feed(("cc", 0, 7, router.volume_cc(127)))
    assert st.snapshot()["expB"]["readout"] == "+0.0 dB"
    assert st.snapshot()["expB"]["value"] > 0.95
