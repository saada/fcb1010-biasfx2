#!/usr/bin/env bash
# Bootstrap the FCB1010 rig on a fresh Linux box (PipeWire + JACK).
#
#   ./bootstrap.sh [path/to/TONE3000-*-linux-x64.tar.gz]
#
# 1. Installs the TONE3000 plugin/standalone from its release tarball (if given or
#    found in ~/Downloads and not installed yet).
# 2. Opens + closes the standalone once so it writes its settings file.
# 3. `tone3000.py configure` — audio/MIDI/calibration/oversampling for this rig.
# 4. `tone3000.py build`     — every rigs/*.json preset, PC order, FCB MIDI map.
# 5. If BIAS FX 2 is installed, `biasfx2.py wire` for the BIAS side too.
#
# Hardware-specific values (interface names, guitar input, MIDI port) live at
# the top of tone3000.py (AUDIO, MIDI_PORT, CALIBRATION_DBU) — edit for your gear.
# The FCB1010 itself is programmed separately: `uv run rig.py send` (RIG-NOTES.md).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
T3K_BIN="$HOME/.local/bin/TONE3000"
SETTINGS="$HOME/.config/TONE3000/TONE3000.settings"

say() { printf '\n==> %s\n' "$*"; }

close_tone3000() {
  pgrep -x TONE3000 >/dev/null || return 0
  if command -v hyprctl >/dev/null; then
    hyprctl clients -j | python3 -c \
      "import json,sys;[print(c['address']) for c in json.load(sys.stdin) if c['class']=='TONE3000']" |
      while read -r addr; do
        hyprctl dispatch "hl.dsp.window.close({ window = \"address:$addr\" })" >/dev/null ||
          hyprctl dispatch closewindow "address:$addr" >/dev/null || true
      done
  else
    echo "Close the TONE3000 window (it saves its state on exit)..."
  fi
  for _ in $(seq 60); do pgrep -x TONE3000 >/dev/null || return 0; sleep 0.5; done
  echo "TONE3000 is still running — close it and re-run." >&2
  exit 1
}

# 1. install
if [[ ! -x "$T3K_BIN" ]]; then
  tarball="${1:-$(ls -t "$HOME"/Downloads/TONE3000-*-linux-x64.tar.gz 2>/dev/null | head -1 || true)}"
  [[ -n "$tarball" && -f "$tarball" ]] || {
    echo "TONE3000 isn't installed and no release tarball was found." >&2
    echo "Download the Linux build from https://www.tone3000.com and pass its path." >&2
    exit 1
  }
  say "Installing TONE3000 from $tarball"
  tmp="$(mktemp -d)"
  tar xzf "$tarball" -C "$tmp"
  (cd "$tmp"/TONE3000-*-linux-x64 && ./install.sh)
  rm -rf "$tmp"
fi

# 2. first run: the standalone only writes its settings file on a clean exit
if ! grep -q 'name="filterState"' "$SETTINGS" 2>/dev/null; then
  say "First run of TONE3000 to create its settings"
  setsid "$T3K_BIN" >/dev/null 2>&1 </dev/null &
  sleep 10
  close_tone3000
fi

close_tone3000

# 3 + 4. configure + build
say "Configuring TONE3000 for this rig"
python3 "$HERE/tone3000.py" configure
say "Building presets from rigs/*.json"
python3 "$HERE/tone3000.py" build

# 5. BIAS FX 2 side (optional)
if [[ -d "$HOME/Documents/PositiveGrid/BIAS_FX2/GlobalPresets" ]]; then
  if pgrep -f 'BIAS FX 2(\.app|_x64\.exe)' >/dev/null; then
    echo "BIAS FX 2 is running — skipping its wiring (quit it and run: python3 biasfx2.py wire)"
  else
    say "Wiring BIAS FX 2 presets"
    python3 "$HERE/biasfx2.py" wire
  fi
fi

say "Done. Launch TONE3000 from the app menu (it runs at a 256-sample buffer) and stomp away."
