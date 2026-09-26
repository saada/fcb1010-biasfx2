# /// script
# requires-python = ">=3.10"
# dependencies = ["python-rtmidi"]
# ///
"""FCB1010 rig for BIAS FX 2 — generate, upload and verify the full board config.

Usage:
  uv run rig.py show               Print the board layout
  uv run rig.py syx [file]         Write the config as a .syx file (default rig.syx)
  uv run rig.py send [--port S]    Upload config to the FCB1010 over MIDI
  uv run rig.py monitor [--port S] Print incoming MIDI to verify each pedal press
  uv run rig.py pull [--port S]    Receive the device's current dump and diff vs this rig
"""

import json
import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent / "lib"))
from fcb1010 import fcb1010

sys.path.insert(0, str(Path(__file__).parent))
from tone3000 import SCENE_DRIVE_CC, SCENE_SELECT_CC, SCENES

CHANNEL = 0  # MIDI channel 1 (0-based)
CLEAN_CHANNEL = 1  # PC 2 -> the DAW's clean TONE3000 (qtractor_rig.py) on MIDI channel 2

WAH_SWEEP_CC = 27
VOLUME_CC = 7

SONGS = [
    # (bank, switch, PC number, name)
    (0, 1, 0, "Comfortably Numb"),
    (0, 2, 1, "Purple Rain"),
    (0, 3, 2, "Tornado of Souls"),
    (0, 4, 3, "Dream Theater"),
    (0, 5, 4, "Slipknot"),
    (1, 1, 5, "Djent"),
    (1, 2, 6, "Radiohead"),
    (1, 3, 7, "Oasis"),
    (1, 4, 8, "Nirvana"),
    (1, 5, 9, "Foo Fighters"),
    (2, 1, 10, "Iron Maiden"),
    (2, 2, 11, "My Clean"),
    (2, 3, 12, "Petrucci Clean"),
    (2, 4, 13, "Acoustic"),
    (2, 5, 14, "Glassy Clean"),
]

TOGGLES = [
    # (switch, CC number, name) — identical row in every bank that has songs
    (6, 20, "Wah on/off"),
    (7, 21, "Octaver"),
    (8, 22, "Boost"),
    (9, 23, "Drive"),
    (10, 24, "Delay + reverb"),
]

# Scene banks (Iron Maiden, one album era per bank) come from rigs/*.json: each bank has a
# "heavy", a "clean" and an "acoustic" preset. Every switch sends both PCs and two absolute
# CCs (tone3000.SCENES), so a scene switch is instant and always lands in the same state.
SCENE_SWITCHES = ["rhythm", "solo", "clean", "acoustic", "crunch"]  # SW1..SW5


def scene_banks():
    banks = {}
    for f in sorted((Path(__file__).parent / "rigs").glob("*.json")):
        rig = json.loads(f.read_text())
        if rig.get("scene"):
            banks.setdefault(rig["bank"], {})[rig["scene"]] = (rig["pc"], rig["name"])
    for bank, presets in banks.items():
        if missing := {"heavy", "clean", "acoustic"} - set(presets):
            print(f"warning: bank {bank} has no {', '.join(sorted(missing))} preset — skipped", file=sys.stderr)
    return {bank: p for bank, p in sorted(banks.items()) if len(p) == 3}


SCENE_BANKS = scene_banks()
# Song switches also reset the scene CCs: unity drive, Solo block off, heavy rig selected.
SONG_RESET = ((SCENE_DRIVE_CC, SCENES["rhythm"][0]), (SCENE_SELECT_CC, 0))

BANKS_IN_USE = sorted({bank for bank, *_ in SONGS} | set(SCENE_BANKS))


def preset_index(bank, switch):
    return bank * 10 + switch - 1


def build():
    fcb = fcb1010()
    for name in ("pc1", "pc2", "pc3", "pc4", "pc5", "cc1", "cc2", "expA", "expB", "note"):
        setattr(fcb, f"{name}_midi_channel", CHANNEL)
    fcb.pc2_midi_channel = CLEAN_CHANNEL
    fcb.direct_select = False

    for preset in fcb.preset:
        preset.pc1_enabled = False
        preset.expA_enabled = True
        preset.expA_controller = WAH_SWEEP_CC
        preset.expA_min, preset.expA_max = 0, 127
        preset.expB_enabled = True
        preset.expB_controller = VOLUME_CC
        preset.expB_min, preset.expB_max = 0, 127

    for bank, switch, pc, _ in SONGS:
        preset = fcb.preset[preset_index(bank, switch)]
        preset.pc1_enabled = True
        preset.pc1_program = pc
        set_ccs(preset, SONG_RESET)

    for bank, presets in SCENE_BANKS.items():
        for switch, scene in enumerate(SCENE_SWITCHES, 1):
            drive, select, clean_kind = SCENES[scene]
            preset = fcb.preset[preset_index(bank, switch)]
            preset.pc1_enabled = False
            if clean_kind:  # loads the era; SOLO/CRUNCH send only CCs (a PC would reset CC 80)
                preset.pc1_enabled, preset.pc1_program = True, presets["heavy"][0]
                preset.pc2_enabled, preset.pc2_program = True, presets[clean_kind][0]
            set_ccs(preset, ((SCENE_DRIVE_CC, drive), (SCENE_SELECT_CC, select)))

    for bank in BANKS_IN_USE:
        for switch, cc, _ in TOGGLES:
            preset = fcb.preset[preset_index(bank, switch)]
            preset.cc1_enabled = True
            preset.cc1_controller = cc
            preset.cc1_value = 127

    return fcb


def set_ccs(preset, ccs):
    (preset.cc1_controller, preset.cc1_value), (preset.cc2_controller, preset.cc2_value) = ccs
    preset.cc1_enabled = preset.cc2_enabled = True


def verify(fcb):
    """Round-trip the generated sysex through the library's parser."""
    data = fcb.get_raw_sysex()
    assert len(data) == 2352, f"dump is {len(data)} bytes, expected 2352"
    parsed = fcb1010()
    assert parsed.parse_sysex(data), "generated sysex failed to parse"
    for bank, switch, pc, name in SONGS:
        p = parsed.preset[preset_index(bank, switch)]
        assert p.pc1_enabled and p.pc1_program == pc, f"{name}: PC mismatch"
        assert ((p.cc1_controller, p.cc1_value), (p.cc2_controller, p.cc2_value)) == SONG_RESET \
            and p.cc1_enabled and p.cc2_enabled, f"{name}: scene reset CCs"
        assert not any((p.pc2_enabled, p.pc3_enabled, p.pc4_enabled, p.pc5_enabled,
                        p.note_enabled)), f"{name}: stray messages"
        assert p.expA_enabled and p.expA_controller == WAH_SWEEP_CC, f"{name}: EXP A"
        assert p.expB_enabled and p.expB_controller == VOLUME_CC, f"{name}: EXP B"
    for bank in BANKS_IN_USE:
        for switch, cc, name in TOGGLES:
            p = parsed.preset[preset_index(bank, switch)]
            assert p.cc1_enabled and p.cc1_controller == cc and p.cc1_value == 127, \
                f"bank {bank} {name}: CC mismatch"
            assert not any((p.pc1_enabled, p.pc2_enabled, p.pc3_enabled,
                            p.pc4_enabled, p.pc5_enabled)), f"bank {bank} {name}: stray PC"
    assert parsed.pc2_midi_channel == CLEAN_CHANNEL, "PC 2 must go out on the clean channel"
    for bank, presets in SCENE_BANKS.items():
        for switch, scene in enumerate(SCENE_SWITCHES, 1):
            drive, select, clean_kind = SCENES[scene]
            p = parsed.preset[preset_index(bank, switch)]
            if clean_kind:
                assert (p.pc1_program, p.pc2_program) == (presets["heavy"][0], presets[clean_kind][0]) \
                    and p.pc1_enabled and p.pc2_enabled, f"bank {bank} {scene}: PCs"
                assert drive == SCENES["rhythm"][0], f"{scene}: a PC-sending scene must use the preset's drive"
            else:
                assert not (p.pc1_enabled or p.pc2_enabled), f"bank {bank} {scene}: must not send PCs"
            assert (p.cc1_controller, p.cc1_value, p.cc2_controller, p.cc2_value) == \
                (SCENE_DRIVE_CC, drive, SCENE_SELECT_CC, select), f"bank {bank} {scene}: CCs"
    return data


def show():
    print("MIDI channel 1 (PC 2: channel 2).  EXP A = wah sweep (CC 27), EXP B = volume (CC 7)\n")
    for bank in BANKS_IN_USE:
        print(f"BANK 0{bank}")
        for b, switch, pc, name in SONGS:
            if b == bank:
                print(f"  SW{switch:<2} -> PC {pc:<3} {name}")
        if bank in SCENE_BANKS:
            presets = SCENE_BANKS[bank]
            print(f"  {presets['heavy'][1]} / {presets['clean'][1]} / {presets['acoustic'][1]}")
            for switch, scene in enumerate(SCENE_SWITCHES, 1):
                drive, select, clean_kind = SCENES[scene]
                pcs = f"PC {presets['heavy'][0]} + PC {presets[clean_kind][0]} (ch 2)" if clean_kind else "no PC"
                print(f"  SW{switch:<2} -> {scene.upper():<9} {pcs:<22} CC {SCENE_DRIVE_CC}={drive} CC {SCENE_SELECT_CC}={select}")
        for switch, cc, name in TOGGLES:
            print(f"  SW{switch:<2} -> CC {cc} (127)  {name}")
        print()


def pick_port(ports, wanted):
    if wanted is not None:
        matches = [i for i, p in enumerate(ports) if wanted.lower() in p.lower()]
        if len(matches) != 1:
            sys.exit(f"--port {wanted!r} matched {len(matches)} of: {ports}")
        return matches[0]
    if len(ports) == 1:
        return 0
    print("Multiple MIDI ports — pick one with --port <name>:")
    for p in ports:
        print(f"  {p}")
    sys.exit(1)


def arg_port(args):
    if "--port" in args:
        return args[args.index("--port") + 1]
    return None


def send(args):
    import rtmidi
    data = verify(build())
    out = rtmidi.MidiOut()
    ports = out.get_ports()
    if not ports:
        sys.exit("No MIDI output ports. Is the USB MIDI interface plugged in?")
    index = pick_port(ports, arg_port(args))
    print(f"Sending {len(data)} bytes to: {ports[index]}")
    print("Put the FCB1010 in receive mode first:")
    print("  1. Hold DOWN ~2.5s while powering on (global config)")
    print("  2. TAP UP repeatedly (short presses - holding does nothing) until the")
    print("     green CONFIGURATION LED by the display lights; keep tapping past")
    print("     the MIDI FUNCTION page")
    print("  3. Tap footswitch 7 (SYSEX RCV) - its LED stays lit, waiting")
    input("Press Enter to send...")
    out.open_port(index)
    out.send_message(data)
    out.close_port()
    print("Sent. The footswitch 7 LED should have flashed during transfer, then")
    print("gone out.")
    print("")
    print("NOW HOLD DOWN ~2.5s — this SAVES the received settings and exits")
    print("config mode. Skip it and the upload is lost.")
    print("")
    print("If the LED never flashed, the data didn't arrive (port or cable).")
    print("Verify in normal mode with: uv run rig.py monitor (expect PC 0/1/2...)")
    print("\nNext: calibrate the expression pedals (power off, hold switches 1+5")
    print("while powering on, follow the heel/toe prompts) — do this AFTER every upload.")


def monitor(args):
    import rtmidi
    inp = rtmidi.MidiIn()
    ports = inp.get_ports()
    if not ports:
        sys.exit("No MIDI input ports. Connect FCB1010 MIDI OUT -> interface MIDI IN.")
    index = pick_port(ports, arg_port(args))
    inp.open_port(index)
    print(f"Monitoring {ports[index]} — press pedals, Ctrl-C to stop.\n")

    def describe(msg):
        status, *rest = msg
        kind, channel = status & 0xF0, (status & 0x0F) + 1
        if kind == 0xC0:
            return f"ch{channel}  Program Change {rest[0]}"
        if kind == 0xB0:
            return f"ch{channel}  CC {rest[0]} = {rest[1]}"
        return f"raw {[hex(b) for b in msg]}"

    inp.set_callback(lambda event, _: print(describe(event[0])))
    try:
        while True:
            time.sleep(0.5)
    except KeyboardInterrupt:
        pass


def pull(args):
    import rtmidi
    inp = rtmidi.MidiIn()
    ports = inp.get_ports()
    if not ports:
        sys.exit("No MIDI input ports. Connect FCB1010 MIDI OUT -> interface MIDI IN.")
    index = pick_port(ports, arg_port(args))
    inp.ignore_types(sysex=False)
    inp.open_port(index)
    received = []
    inp.set_callback(lambda event, _: received.append(event[0]))
    print(f"Listening on {ports[index]}.")
    print("On the FCB1010:")
    print("  1. Hold DOWN ~2.5s while powering on (global config)")
    print("  2. TAP UP repeatedly (short presses - holding does nothing) until the")
    print("     green CONFIGURATION LED by the display lights; keep tapping past")
    print("     the MIDI FUNCTION page")
    print("  3. Tap footswitch 6 (SYSEX SND) - the dump transmits immediately...")
    try:
        while not any(m and m[0] == 0xF0 for m in received):
            time.sleep(0.2)
    except KeyboardInterrupt:
        sys.exit("\nNothing received.")
    time.sleep(1)
    dump = next(m for m in received if m[0] == 0xF0)
    print(f"\nReceived {len(dump)} bytes (expected 2352).")
    device = fcb1010()
    if not device.parse_sysex(dump):
        sys.exit("Not a valid FCB1010 dump — transfer truncated? Try a different interface.")
    Path(__file__).parent.joinpath("device-backup.syx").write_bytes(bytes(dump))
    print("Saved to device-backup.syx\n")
    expected = {(b, s): ("PC", pc, n) for b, s, pc, n in SONGS} | \
               {(b, s): ("CC", cc, n) for b in BANKS_IN_USE for s, cc, n in TOGGLES}
    mismatches = 0
    for bank in BANKS_IN_USE:
        for switch in range(1, 11):
            p = device.preset[preset_index(bank, switch)]
            actual = f"PC {p.pc1_program}" if p.pc1_enabled else \
                     f"CC {p.cc1_controller}={p.cc1_value}" if p.cc1_enabled else "silent"
            kind, num, name = expected.get((bank, switch), (None, None, "unused"))
            want = f"{kind} {num}" if kind else "silent"
            if kind == "PC":
                ok = p.pc1_enabled and p.pc1_program == num and not p.cc1_enabled
            elif kind == "CC":
                ok = p.cc1_enabled and p.cc1_controller == num and not p.pc1_enabled
            else:
                ok = not p.pc1_enabled and not p.cc1_enabled
            mark = "ok " if ok else "MISMATCH"
            if not ok:
                mismatches += 1
            print(f"  bank {bank} SW{switch:<2} device: {actual:<12} rig: {want:<8} {mark}  ({name})")
    print("\nDevice matches this rig." if mismatches == 0 else
          f"\n{mismatches} mismatches — device still has a different config; re-run send.")


def main():
    args = sys.argv[1:]
    command = args[0] if args else "show"
    if command == "show":
        show()
    elif command == "syx":
        out = Path(args[1]) if len(args) > 1 else Path(__file__).parent / "rig.syx"
        out.write_bytes(bytes(verify(build())))
        print(f"Wrote {out} (2352 bytes, verified)")
    elif command == "send":
        send(args)
    elif command == "monitor":
        monitor(args)
    elif command == "pull":
        pull(args)
    else:
        sys.exit(__doc__)


if __name__ == "__main__":
    main()
