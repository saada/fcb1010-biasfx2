# /// script
# requires-python = ">=3.10"
# ///
"""Wire BIAS FX 2 preset files to the FCB1010 rig (see rig.py for the board side).

Usage:
  uv run biasfx2.py map     Show the intended PC->preset map and CC wiring
  uv run biasfx2.py wire    Build the FCB1010 bank (copies, PC order), write
                            per-preset and global MIDI mappings.
                            BIAS FX 2 must NOT be running.
  uv run biasfx2.py pedals  Insert missing pedals (wah/octaver/harmonizer/
                            volume per ADDITIONS) into FCB-bank chains with
                            scene snapshots, then re-wire everything.

Everything the pedal triggers lives in a dedicated "FCB1010" bank, ordered by
PC number. Sources are copied in; edit the FCB1010 bank copies to tweak the rig.

CC scheme (must match rig.py): 20 wah, 21 modulation/octaver, 22 delay,
23 drive/boost, 27 wah sweep (EXP A), 7 volume (EXP B, wired when a Volume
module exists in the chain).
"""

import json
import shutil
import subprocess
import sys
import uuid as uuidlib
from pathlib import Path

ROOT = Path.home() / "Documents/PositiveGrid/BIAS_FX2"
PRESETS = ROOT / "GlobalPresets"
USER_BANK = "39C71559-73FE-4A88-B802-EF3C15C29F3B"
FCB_BANK_NAME = "FCB1010"

# (pc, source_bank, source_preset_uuid, rig_name)
TARGETS = [
    (0, "6683CD2A-39D9-EEC0-6510-369203A24FFE", "F49C8741-6750-4D39-9794-75CB06A72514", "Comfortably Numb"),
    (1, USER_BANK, "A79F2059-28E8-4341-92F5-D5DDE6E6C41A", "Purple Rain"),
    (2, USER_BANK, "CF1F2AE2-F43E-4F84-8F5F-7D6CF8B3BE58", "Tornado of Souls"),
    (3, "6683CD2A-39D9-EEC0-6510-369203A24FFE", "99D09016-C6D1-4DD7-9C1C-C9342ED13C55", "Dream Theater"),
    (4, "6683CD2A-39D9-EEC0-6510-369203A24FFE", "5331D47F-045E-463B-BC98-9EF15060292A", "Slipknot"),
    (5, USER_BANK, "04F24260-4B40-4403-968E-1C762BDBCA9E", "Djent"),
    (6, "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA", "07F87767-D1B0-4C79-AD9B-096BD704B432", "Radiohead"),
    (7, "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA", "073EF34E-4526-4D44-9B02-2B3CDBFD8576", "Oasis"),
    (8, "6683CD2A-39D9-EEC0-6510-369203A24FFE", "F569A1A3-18C8-45BD-BFE6-A93A20927B9E", "Nirvana"),
    (9, "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA", "06977430-37AA-41D2-BD69-B593AD81EB3B", "Foo Fighters"),
    (10, "6683CD2A-39D9-EEC0-6510-369203A24FFE", "0C1821C3-09B0-487B-9FE8-1515FFD335CA", "Iron Maiden"),
    (11, "6683CD2A-39D9-EEC0-6510-369203A24FFE", "D908D125-9F88-4AC0-85E1-8F741A549B1F", "My Clean"),
    (12, "6683CD2A-39D9-EEC0-6510-369203A24FFE", "34E7236D-D805-4CCA-B2C1-217BF838D1AD", "Petrucci Clean"),
    (13, USER_BANK, "8ADA8499-DA29-4F6B-AC35-84492BC9239A", "Acoustic"),
    (14, "AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAA", "40349BE9-A568-4DEA-ACB4-B84E000B90E6", "Glassy Clean"),
]

WAH_CC, MOD_CC, DELAY_CC, DRIVE_CC = 20, 21, 22, 23
WAH_SWEEP_CC, VOLUME_CC = 27, 7

DRIVE_PRIORITY = ["Distortion", "HarmonixBigMuff", "TS9", "FulltoneOCD",
                  "OverdrivePro", "KlonCentaur", "Booster", "SparkBooster",
                  "FuzzFactory", "TrebleBooster"]
MOD_PRIORITY = ["Octaver", "PitchShifter", "Harmonizer", "DelayHarmonizer",
                "TriChorus", "CE1", "ChorusStereo", "TremoloStereo",
                "OrangePhaser", "PhaserPro"]
DELAY_IDS = ["Delay", "EchorecDelay", "BiasDelay"]
WAH_IDS = ["CryWah", "Wah", "FunkyWah"]
VOLUME_IDS = ["Volume", "VolumePedal"]

# Chain additions: pedals inserted into FCB-bank presets that lack them.
# Octaver params from the factory 'Air Jimmy' instance; harmonizer params are
# copied at runtime from the factory preset that uses it.
HARMONIZER_SOURCE = ("AAAAAAAA-AAAA-AAAA-AAAA-AAAAAAAAAAAD",
                     "90E1DA82-CE8E-4327-A84D-35F89F6262A7", "FX2.DelayHarmonizer")
ADDITIONS = [
    # (dspId, active, params, placement, applies-to PCs)
    ("FX2.CryWah", False, [0.5], "after_gate", set(range(15)) - {13}),
    ("FX2.Octaver", False, [0.5, 0.45, 0.36], "after_wah", {2, 3, 4, 5}),
    ("FX2.DelayHarmonizer", False, None, "after_wah", {10}),
    ("FX2.Volume", True, [1.0], "end", set(range(15))),
]


def read(path):
    return json.loads(Path(path).read_text())


def write(path, obj):
    Path(path).write_text(json.dumps(obj, indent=3))


def short(dsp_id):
    return dsp_id.removeprefix("FX2.").removeprefix("bias.")


def chain_modules(data):
    out = []

    def walk(x):
        if isinstance(x, dict):
            if "dspId" in x and "id" in x:
                out.append((x["dspId"], x["id"]))
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(data["sigPath"])
    return out


def pick(mods, names):
    for name in names:
        for dsp_id, mod_id in mods:
            if short(dsp_id) == name:
                return (dsp_id, mod_id)
    return None


def wiring_for(mods):
    entries = []

    def add(module, cc, params=None):
        dsp_id, mod_id = module
        entries.append({
            "dspId": dsp_id,
            "midi_param": params or [],
            "power_midi": {"midi_cc": cc},
            "uuid": mod_id,
        })

    if wah := pick(mods, WAH_IDS):
        add(wah, WAH_CC, [{"index": 0, "midi_cc": WAH_SWEEP_CC}])
    if mod := pick(mods, MOD_PRIORITY):
        add(mod, MOD_CC)
    for dsp_id, mod_id in mods:
        if short(dsp_id) in DELAY_IDS:
            add((dsp_id, mod_id), DELAY_CC)
    if drive := pick(mods, DRIVE_PRIORITY) or pick(mods, ["EQ10"]):
        add(drive, DRIVE_CC)
    if vol := pick(mods, VOLUME_IDS):
        add(vol, 0, [{"index": 0, "midi_cc": VOLUME_CC}])
        entries[-1].pop("power_midi")
    return entries


def fcb_bank_id(create=False):
    registry = read(PRESETS / "bank.json")
    for bank in registry["LiveBanks"]:
        if bank["bank_name"] == FCB_BANK_NAME:
            return bank["bank_folder"]
    if not create:
        return None
    bank_id = str(uuidlib.uuid4()).upper()
    (PRESETS / bank_id).mkdir()
    write(PRESETS / bank_id / "preset.json", {"LivePresets": []})
    registry["LiveBanks"].append({
        "bank_folder": bank_id,
        "bank_name": FCB_BANK_NAME,
        "display_order": max(b["display_order"] for b in registry["LiveBanks"]) + 1,
        "is_cloud": False,
        "is_factory": False,
        "user_id": "",
    })
    write(PRESETS / "bank.json", registry)
    print(f"created bank '{FCB_BANK_NAME}' ({bank_id})")
    return bank_id


def bank_index_find(bank_id, name):
    for e in read(PRESETS / bank_id / "preset.json")["LivePresets"]:
        if e["preset_name"] == name:
            return e["preset_uuid"]
    return None


def deregister(bank_id, pid):
    index_path = PRESETS / bank_id / "preset.json"
    index = read(index_path)
    index["LivePresets"] = [e for e in index["LivePresets"] if e["preset_uuid"] != pid]
    write(index_path, index)


def ensure_in_fcb_bank(bank_id, src_bank, src_pid, name):
    """Return preset uuid inside the FCB bank, moving our old user-bank clone
    or copying the source preset as needed."""
    if existing := bank_index_find(bank_id, name):
        return existing
    old_clone = bank_index_find(USER_BANK, name)
    if old_clone and old_clone != src_pid:
        shutil.move(PRESETS / USER_BANK / old_clone, PRESETS / bank_id / old_clone)
        deregister(USER_BANK, old_clone)
        print(f"moved '{name}' from user bank")
        return old_clone
    new_id = str(uuidlib.uuid4()).upper()
    shutil.copytree(PRESETS / src_bank / src_pid, PRESETS / bank_id / new_id)
    for fname, key in (("meta.json", "name"), ("data.json", "name")):
        obj = read(PRESETS / bank_id / new_id / fname)
        obj[key] = name
        obj.pop("cloud_id", None)
        write(PRESETS / bank_id / new_id / fname, obj)
    print(f"copied '{name}'")
    return new_id


def resolve_targets(create):
    bank_id = fcb_bank_id(create)
    if not bank_id:
        sys.exit("FCB1010 bank doesn't exist yet — run: uv run biasfx2.py wire")
    resolved = []
    for pc, src_bank, src_pid, name in TARGETS:
        if create:
            pid = ensure_in_fcb_bank(bank_id, src_bank, src_pid, name)
        elif not (pid := bank_index_find(bank_id, name)):
            sys.exit(f"'{name}' missing from FCB1010 bank — run: uv run biasfx2.py wire")
        resolved.append((pc, bank_id, pid, name))
    return resolved


def harmonizer_params():
    bank, pid, dsp_id = HARMONIZER_SOURCE
    data = read(PRESETS / bank / pid / "data.json")
    found = []

    def walk(x):
        if isinstance(x, dict):
            if x.get("dspId") == dsp_id and "param" in x:
                found.append(x["param"])
            for v in x.values():
                walk(v)
        elif isinstance(x, list):
            for v in x:
                walk(v)

    walk(data["sigPath"])
    if not found:
        sys.exit(f"no {dsp_id} template found in factory preset")
    return [p["value"] for p in found[0]]


def insert_position(chain, placement):
    shorts = [short(m["dspId"]) for m in chain]
    if placement == "end":
        return len(chain)
    if placement == "after_wah":
        for i, s in enumerate(shorts):
            if s in WAH_IDS:
                return i + 1
    for i, s in enumerate(shorts):
        if s == "NoiseGate":
            return i + 1
    return 0


def add_pedals():
    """Insert missing pedals into FCB-bank preset chains (+ scene snapshots)."""
    resolved = resolve_targets(create=False)
    harmonizer = None
    for pc, bank_id, pid, name in resolved:
        path = PRESETS / bank_id / pid / "data.json"
        data = read(path)
        chain = data["sigPath"]
        present = {short(m["dspId"]) for m in chain}
        added = []
        for dsp_id, active, params, placement, pcs in ADDITIONS:
            if pc not in pcs or short(dsp_id) in present:
                continue
            if params is None:
                harmonizer = harmonizer or harmonizer_params()
                params = harmonizer
            module = {
                "ModulePresetName": "",
                "active": active,
                "dspId": dsp_id,
                "id": str(uuidlib.uuid4()).upper(),
                "param": [{"id": i, "value": v} for i, v in enumerate(params)],
                "selected": False,
            }
            chain.insert(insert_position(chain, placement), module)
            snapshot = {
                "Parameters": [{"index": i, "value": v} for i, v in enumerate(params)],
                "active": active,
                "uniqueid": module["id"],
            }
            for slot in data.get("scenes", {}).get("slot", []):
                lanes = slot.get("iTonesPreset", {}).get("Fxs")
                if lanes:
                    lanes[0]["Fxs"].append(dict(snapshot))
            added.append(short(dsp_id))
        if added:
            write(path, data)
        print(f"PC {pc:<3} {name:<17} + {', '.join(added) if added else '(complete)'}")


def show_map():
    for pc, bank_id, pid, name in resolve_targets(create=False):
        data = read(PRESETS / bank_id / pid / "data.json")
        print(f"PC {pc:<3} {name}")
        for e in wiring_for(chain_modules(data)):
            cc = e.get("power_midi", {}).get("midi_cc")
            extra = "".join(f" + CC {p['midi_cc']} sweep" for p in e["midi_param"])
            print(f"         CC {cc if cc is not None else '--'}: {short(e['dspId'])}{extra}")


def wire():
    if subprocess.run(["pgrep", "-f", "BIAS FX 2.app"], capture_output=True).stdout:
        sys.exit("BIAS FX 2 is running — quit it first.")
    resolved = resolve_targets(create=True)
    bank_id = resolved[0][1]
    for pc, _, pid, name in resolved:
        data = read(PRESETS / bank_id / pid / "data.json")
        entries = wiring_for(chain_modules(data))
        write(PRESETS / bank_id / pid / "midi.json", entries)
        print(f"PC {pc:<3} {name:<17} -> {len(entries)} CC mappings written")
    write(PRESETS / bank_id / "preset.json", {"LivePresets": [
        {"display_order": pc, "is_favorite": False, "preset_name": name, "preset_uuid": pid}
        for pc, _, pid, name in resolved
    ]})
    gm_path = ROOT / "midi.json"
    gm = read(gm_path)
    gm["enabled"] = True
    rig_ccs = {WAH_CC, MOD_CC, DELAY_CC, DRIVE_CC, WAH_SWEEP_CC, VOLUME_CC, 24}
    for k in [k for k in gm.get("control_number", {}) if int(k) in rig_ccs]:
        del gm["control_number"][k]
        print(f"removed conflicting global CC {k} mapping")
    gm.setdefault("control_number", {})["24"] = ["utility.tuner"]
    gm["program_change"] = {
        str(pc): [{"bank_id": bank_id, "behavior": "preset.goto", "preset_id": pid}]
        for pc, _, pid, name in resolved
    }
    write(gm_path, gm)
    print(f"\nFCB1010 bank holds {len(resolved)} presets in PC order; global PC map written.")
    print("Start BIAS FX 2 and stomp through the banks.")


if __name__ == "__main__":
    cmd = sys.argv[1] if len(sys.argv) > 1 else "map"
    if cmd == "map":
        show_map()
    elif cmd == "wire":
        wire()
    elif cmd == "pedals":
        if subprocess.run(["pgrep", "-f", "BIAS FX 2.app"], capture_output=True).stdout:
            sys.exit("BIAS FX 2 is running — quit it first.")
        add_pedals()
        print()
        wire()
    else:
        sys.exit(__doc__)
