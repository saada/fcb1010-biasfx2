import xml.etree.ElementTree as ET

import pytest

from guitarmood import engine as engine_mod
from guitarmood import retune
from guitarmood.board import qr, t3k


def test_names_count_from_the_guitars_b():
    assert [c["name"] for c in retune.choices()] == \
        ["A standard", "A# standard", "B standard", "C standard", "C# standard"]
    assert retune.name(-1) == "A# standard" and retune.name(1) == "C standard"


@pytest.fixture
def sheet(tmp_path):
    path = tmp_path / "tone.csv"
    path.write_bytes(t3k.TONE_CSV.read_bytes())
    return path


def test_write_star_touches_one_cell(sheet):
    before = sheet.read_bytes()
    retune.write_star(-1, sheet)
    assert retune.current(sheet) == -1
    old, new = before.decode().splitlines(), sheet.read_text().splitlines()
    changed = [(a, b) for a, b in zip(old, new) if a != b]
    assert len(old) == len(new) and len(changed) == 1
    assert changed[0][0].startswith("*,") and changed[0][1].endswith(",-1")
    assert not t3k.tone_sheet(sheet)["errors"]
    retune.write_star(0, sheet)
    assert sheet.read_bytes() == before  # back to B: byte for byte what it was


def test_write_star_refuses_out_of_range(sheet):
    with pytest.raises(ValueError):
        retune.write_star(13, sheet)


def test_every_preset_follows_the_star_row(sheet):
    retune.write_star(-1, sheet)
    rigs = t3k.load_rigs(t3k.tone_sheet(sheet))
    assert {retune.expected(r["name"], rigs) for r in rigs} == {(True, -1)}
    retune.write_star(0, sheet)
    rigs = t3k.load_rigs(t3k.tone_sheet(sheet))
    assert {retune.expected(r["name"], rigs) for r in rigs} == {(False, 0)}


def session(loaded):
    """A Qtractor session with TONE3000 instances: {label: (preset, enabled, semitones)}."""
    root = ET.Element("session")
    for label, (preset, on, st) in loaded.items():
        params = [t3k.Node("PARAM").set("id", "pitchEnabled").set("value", float(on)),
                  t3k.Node("PARAM").set("id", "pitchSemitones").set("value", float(st)),
                  t3k.Node("PARAM").set("id", "inputLevel").set("value", 0.5)]
        state = t3k.Node("T3KState").set("activePresetName", preset)
        state.children = [t3k.Node("PARAMETERS", children=params)]
        p = ET.SubElement(root, "plugin")
        ET.SubElement(p, "label").text = label
        cfg = ET.SubElement(ET.SubElement(p, "configs"), "config", key="state")
        cfg.text = qr.qcompress(t3k.dump_t3kb(state))
    lv2 = ET.SubElement(root, "plugin")
    ET.SubElement(lv2, "label").text = "Wah"
    return root


MAIDEN = {"TONE3000 Heavy": "Maiden Heavy", "TONE3000 Clean": "Maiden Clean", "TONE3000 Harmony": "Maiden Harmony"}


def test_instances_reads_the_plugin_state():
    root = session({"TONE3000 Heavy": ("Maiden Heavy", True, -1), "TONE3000 Clean": ("Maiden Clean", False, 0)})
    assert retune.instances(root) == {"TONE3000 Heavy": {"preset": "Maiden Heavy", "on": True, "st": -1},
                                      "TONE3000 Clean": {"preset": "Maiden Clean", "on": False, "st": 0}}


def test_mismatches_name_the_instance_that_missed(sheet):
    retune.write_star(1, sheet)
    rigs = t3k.load_rigs(t3k.tone_sheet(sheet))
    good = retune.instances(session({k: (v, True, 1) for k, v in MAIDEN.items()}))
    assert retune.mismatches(good, rigs) == []
    stale = retune.instances(session({**{k: (v, True, 1) for k, v in MAIDEN.items()},
                                      "TONE3000 Clean": ("Maiden Clean", False, 0)}))
    assert retune.mismatches(stale, rigs) == ["Clean (Maiden Clean) at +0 st, wants +1"]


def test_write_presets_sets_only_the_pitch_params(sheet, tmp_path):
    rigs0 = t3k.load_rigs(t3k.tone_sheet(sheet))
    path = t3k.build_presets(["Maiden Clean"], tmp_path)["Maiden Clean"]

    def read():
        return {q.get("id"): q.get("value") for q in t3k.load_t3kb(path.read_bytes()).child("Params").children}

    before = read()
    retune.write_star(-1, sheet)
    rigs = t3k.load_rigs(t3k.tone_sheet(sheet))
    assert retune.write_presets(tmp_path, rigs) == 1
    after = read()
    assert (after["pitchEnabled"], after["pitchSemitones"]) == (1.0, -1.0)
    assert {k: v for k, v in after.items() if k not in retune.PITCH_PARAMS} == \
        {k: v for k, v in before.items() if k not in retune.PITCH_PARAMS}
    assert retune.write_presets(tmp_path, rigs) == 0  # already there: nothing rewritten
    assert retune.write_presets(tmp_path, rigs0) == 1 and read() == before  # and back to B


def test_hop_never_picks_the_preset_itself():
    rigs = t3k.load_rigs()
    for r in rigs:
        assert retune.hop(r["pc"], rigs)["pc"] != r["pc"]


def test_log_loaded_reads_only_what_came_after_the_mark(tmp_path, monkeypatch):
    log = tmp_path / "TONE3000.log"
    log.write_text("[Presets] Loaded preset: Old\n")
    monkeypatch.setattr(retune, "LOG", log)
    mark = retune.log_mark()
    with log.open("a") as f:
        f.write("[ModelLoader] NAM model prepared\n[Presets] Loaded preset: Maiden Heavy\n")
    assert retune.log_loaded(mark) == ["Maiden Heavy"]


def test_retune_reloads_every_instance_and_checks_it(sheet, tmp_path, monkeypatch):
    """The whole live sequence against a fake rig: each instance hops away and back (the PC it
    already has would not reload, D5), the scene's CCs return, and the saved state is checked."""
    retune.write_star(-1, sheet)
    rigs = t3k.load_rigs(t3k.tone_sheet(sheet))
    by_pc = {r["pc"]: r["name"] for r in rigs}
    log = tmp_path / "TONE3000.log"
    log.write_text("")
    sent, saves, statuses = [], [], []

    def send(_self, port, *events):
        for ev in events:
            sent.append(ev)
            if ev[0] == "pc":
                with log.open("a") as f:
                    f.write(f"[Presets] Loaded preset: {by_pc[ev[2]]}\n")

    def save():
        saves.append(1)
        return session({k: (v, len(saves) > 1, -1 if len(saves) > 1 else 0) for k, v in MAIDEN.items()})

    monkeypatch.setattr(retune, "LOG", log)
    monkeypatch.setattr(retune, "set_tuning", lambda st: 39)
    monkeypatch.setattr(t3k, "load_rigs", lambda sheet=None: rigs)
    monkeypatch.setattr(engine_mod, "save_session", save)
    monkeypatch.setattr(engine_mod.Engine, "_send", send)
    eng = engine_mod.Engine(statuses.append, lambda ev: None, lambda daw: None, no_rig=True)
    eng._retune("0:0", -1, (72, 0))

    pcs = [ev for ev in sent if ev[0] == "pc"]
    assert len(pcs) == 6 and len(saves) == 2
    for (ch, name), (away, back) in zip(((0, "Maiden Heavy"), (1, "Maiden Clean"), (2, "Maiden Harmony")),
                                        zip(pcs[::2], pcs[1::2])):
        assert away[1] == back[1] == ch
        assert by_pc[back[2]] == name and away[2] != back[2]
    assert sent[-2:] == [("cc", 0, t3k.SCENE_DRIVE_CC, 72), ("cc", 0, t3k.SCENE_SELECT_CC, 0)]
    pitch = [s["pitch"] for s in statuses if "pitch" in s]
    assert pitch[-1]["phase"] == "live" and pitch[-1]["name"] == "A# standard"
    assert any("pitchReloaded" in s for s in statuses)


def test_retune_goes_back_even_when_the_hop_never_logs(sheet, tmp_path, monkeypatch):
    rigs = t3k.load_rigs(t3k.tone_sheet(sheet))
    by_pc = {r["pc"]: r["name"] for r in rigs}
    log = tmp_path / "TONE3000.log"
    log.write_text("")
    sent, statuses = [], []

    def send(_self, port, *events):  # TONE3000 logs only the way back, never the hop
        for ev in events:
            sent.append(ev)
            if ev[0] == "pc" and by_pc[ev[2]] == "Maiden Heavy":
                with log.open("a") as f:
                    f.write("[Presets] Loaded preset: Maiden Heavy\n")

    monkeypatch.setattr(retune, "LOG", log)
    monkeypatch.setattr(retune, "set_tuning", lambda st: 39)
    monkeypatch.setattr(t3k, "load_rigs", lambda sheet=None: rigs)
    monkeypatch.setattr(engine_mod, "save_session",
                        lambda: session({"TONE3000 Heavy": ("Maiden Heavy", False, 0)}))
    monkeypatch.setattr(engine_mod.Engine, "_send", send)
    monkeypatch.setattr(engine_mod.Engine._load, "__defaults__", (0.2,))
    eng = engine_mod.Engine(statuses.append, lambda ev: None, lambda daw: None, no_rig=True)
    with pytest.raises(TimeoutError):
        eng._retune("0:0", 1, None)
    assert [by_pc[ev[2]] for ev in sent][-1] == "Maiden Heavy"  # the hop timed out, the way back still went


def test_retune_says_so_when_an_instance_missed(sheet, tmp_path, monkeypatch):
    retune.write_star(-1, sheet)
    rigs = t3k.load_rigs(t3k.tone_sheet(sheet))
    by_pc = {r["pc"]: r["name"] for r in rigs}
    log = tmp_path / "TONE3000.log"
    log.write_text("")
    statuses = []

    def send(_self, port, *events):
        for ev in events:
            if ev[0] == "pc":
                with log.open("a") as f:
                    f.write(f"[Presets] Loaded preset: {by_pc[ev[2]]}\n")

    monkeypatch.setattr(retune, "LOG", log)
    monkeypatch.setattr(retune, "set_tuning", lambda st: 39)
    monkeypatch.setattr(t3k, "load_rigs", lambda sheet=None: rigs)
    monkeypatch.setattr(engine_mod, "save_session", lambda: session({k: (v, False, 0) for k, v in MAIDEN.items()}))
    monkeypatch.setattr(engine_mod.Engine, "_send", send)
    eng = engine_mod.Engine(statuses.append, lambda ev: None, lambda daw: None, no_rig=True)
    eng._retune("0:0", -1, None)
    last = [s["pitch"] for s in statuses if "pitch" in s][-1]
    assert last["phase"] == "error" and "Heavy (Maiden Heavy) at +0 st, wants -1" in last["detail"]
    assert not any("pitchReloaded" in s for s in statuses)
