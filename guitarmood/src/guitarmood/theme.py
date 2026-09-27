"""The active Omarchy theme: its colors.toml palette and the system monospace font.

Omarchy rewrites ~/.local/state/omarchy/current/theme on every theme switch, so the file is
polled (cheap: one stat every two seconds) and the window recolours live.
"""

import subprocess
import tomllib
from pathlib import Path

COLORS = Path.home() / ".local/state/omarchy/current/theme/colors.toml"

# Tokyo Night, for a machine without Omarchy.
FALLBACK = {
    "accent": "#7aa2f7", "background": "#1a1b26", "dark_background": "#13141c", "darker_background": "#0e0e14",
    "lighter_background": "#24283b", "selection": "#292e42", "muted": "#414868", "foreground": "#a9b1d6",
    "dark_foreground": "#565f89", "light_foreground": "#b4bee6", "bright_foreground": "#c0caf5",
    "red": "#f7768e", "yellow": "#e0af68", "orange": "#eb927b", "green": "#9ece6a", "cyan": "#449dab",
    "blue": "#7aa2f7", "magenta": "#ad8ee6", "bright_cyan": "#0db9d7", "bright_green": "#b9f27c",
    "bright_magenta": "#bb9af7", "bright_yellow": "#ff9e64", "bright_red": "#ff7a93", "mode": "dark",
}


def palette():
    try:
        raw = tomllib.loads(COLORS.read_text())
    except (OSError, tomllib.TOMLDecodeError):
        raw = {}
    colors = FALLBACK | {k: v for k, v in raw.items() if isinstance(v, str)}
    # Older themes only define the base names; derive what the window uses from them.
    colors.setdefault("lighter_background", colors["selection"])
    return colors


def stamp():
    try:
        return COLORS.stat().st_mtime_ns, COLORS.resolve()
    except OSError:
        return None


def font_family():
    try:
        out = subprocess.run(["fc-match", "monospace", "-f", "%{family[0]}"], capture_output=True, text=True, timeout=3)
        return out.stdout.strip() or "monospace"
    except (OSError, subprocess.TimeoutExpired):
        return "monospace"
