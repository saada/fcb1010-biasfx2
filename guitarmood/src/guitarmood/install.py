"""Put GuitarMood in the Omarchy launcher (SUPER+SPACE) and teach Hyprland about it.

  - ~/.local/share/applications/guitarmood.desktop: launching focuses the window if it is
    already open (omarchy-launch-or-focus), so a second launch never starts a second rig.
  - ~/.local/share/icons/hicolor/scalable/apps/guitarmood.svg
  - ~/.config/hypr/hyprland.lua: Qtractor's window goes to a hidden special workspace
    (SUPER+CTRL+G shows it); GuitarMood itself tiles like any Omarchy app.
"""

import os
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
PROJECT = HERE.parents[1]
APPS = Path.home() / ".local/share/applications"
ICONS = Path.home() / ".local/share/icons/hicolor/scalable/apps"
HYPR = Path.home() / ".config/hypr/hyprland.lua"
BEGIN, END = "-- GuitarMood (begin): managed by `uv run guitarmood install`", "-- GuitarMood (end)"

HYPR_RULES = r'''
-- The rig's Qtractor runs out of sight; GuitarMood is its face. SUPER+CTRL+G peeks at it.
o.window("^(org\\.rncbc\\.qtractor|qtractor)$", { workspace = "special:rig silent" })
o.bind("SUPER + CTRL + G", "Show the rig's Qtractor", hl.dsp.workspace.toggle_special("rig"))
'''


def uv_path():
    shim = Path.home() / ".local/share/mise/shims/uv"  # Omarchy manages dev tools with mise
    return str(shim) if shim.exists() else os.environ.get("UV") or shutil.which("uv") or "uv"


def desktop_entry():
    launch = f"{uv_path()} run --project {PROJECT} guitarmood"
    return f"""[Desktop Entry]
Type=Application
Name=GuitarMood
GenericName=Guitar rig
Comment=Start the FCB1010 guitar rig and see every pedal live; close it to shut the rig down
Exec=omarchy-launch-or-focus "^guitarmood$" "{launch}"
Icon=guitarmood
Terminal=false
Categories=AudioVideo;Audio;Music;
Keywords=guitar;fcb1010;tone3000;qtractor;pedal;maiden;rig;
StartupWMClass=guitarmood
"""


def hypr_block_present(text):
    return BEGIN in text and END in text


def install():
    APPS.mkdir(parents=True, exist_ok=True)
    ICONS.mkdir(parents=True, exist_ok=True)
    (APPS / "guitarmood.desktop").write_text(desktop_entry())
    shutil.copyfile(HERE / "guitarmood.svg", ICONS / "guitarmood.svg")
    print(f"launcher: {APPS / 'guitarmood.desktop'}")
    if HYPR.exists():
        text = HYPR.read_text()
        block = f"{BEGIN}{HYPR_RULES}{END}\n"
        if hypr_block_present(text):
            text = re.sub(re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", lambda _: block, text, flags=re.S)
        else:
            text = text.rstrip("\n") + "\n\n" + block
        HYPR.write_text(text)
        print(f"hyprland: rules in {HYPR} (Qtractor -> special:rig, SUPER+CTRL+G)")
    subprocess.run(["update-desktop-database", str(APPS)], capture_output=True)
    print("Open it from the Omarchy launcher (SUPER+SPACE): GuitarMood. Close it (SUPER+W) to stop the rig.")


def uninstall():
    for f in (APPS / "guitarmood.desktop", ICONS / "guitarmood.svg"):
        f.unlink(missing_ok=True)
    if HYPR.exists() and hypr_block_present(text := HYPR.read_text()):
        HYPR.write_text(re.sub(r"\n*" + re.escape(BEGIN) + r".*?" + re.escape(END) + r"\n?", "\n", text, flags=re.S))
    subprocess.run(["update-desktop-database", str(APPS)], capture_output=True)
    print("GuitarMood removed from the launcher and Hyprland config.")
