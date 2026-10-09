#!/usr/bin/env bash
# Bootstrap the FCB1010 rig on a fresh Linux box (PipeWire + JACK).
#
#   ./bootstrap.sh [path/to/TONE3000-*-linux-x64.tar.gz]   (default: download T3K_VERSION)
#
# 1. Installs (or upgrades to) the pinned TONE3000 release, T3K_VERSION, from its tarball (given,
#    in ~/Downloads, or downloaded from GitHub and checksum-verified).
# 2. Opens + closes the standalone once so it writes its settings file.
# 3. `tone3000.py configure` — audio/MIDI/calibration/oversampling for this rig.
# 4. `tone3000.py build`     — every rigs/*.json preset, PC order, FCB MIDI map.
# 5. If BIAS FX 2 is installed, `biasfx2.py wire` for the BIAS side too.
# 6. If Qtractor is installed, `qtractor_rig.py build` — the DAW rig session.
#
# Hardware-specific values (interface names, guitar input, MIDI port) live at
# the top of tone3000.py (AUDIO, MIDI_PORT, CALIBRATION_DBU) — edit for your gear.
# The FCB1010 itself is programmed separately: `uv run rig.py send` (RIG-NOTES.md).
set -euo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"
T3K_BIN="$HOME/.local/bin/TONE3000"
# The plugin release the rig is built and measured against (README "TONE3000 side").
# Its install.sh rewrites the desktop entry; `tone3000.py configure` below restores the quantum.
T3K_VERSION="v0.0.12"
T3K_SHA256="c45ea5d64e6eef991b14f1883a4249ed1a883253db1a9d9f0605e0540aba5fc7"
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

# 1. install (or upgrade to) the pinned TONE3000 release
if [[ ! -x "$T3K_BIN" ]] || ! grep -aq "${T3K_VERSION#v}" "$T3K_BIN"; then
  tarball="${1:-$HOME/Downloads/TONE3000-$T3K_VERSION-linux-x64.tar.gz}"
  if [[ ! -f "$tarball" ]]; then
    say "Downloading TONE3000 $T3K_VERSION"
    mkdir -p "$(dirname "$tarball")"
    curl -fL --retry 3 -o "$tarball" \
      "https://github.com/tone-3000/tone3000-plugin/releases/download/$T3K_VERSION/TONE3000-$T3K_VERSION-linux-x64.tar.gz"
  fi
  if [[ -z "${1:-}" ]] && ! echo "$T3K_SHA256  $tarball" | sha256sum -c --quiet; then
    echo "Checksum mismatch for $tarball — delete it and re-run." >&2
    exit 1
  fi
  close_tone3000
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

# 6. DAW rig (optional): generated Qtractor session with pedals, dynamics, recording
if command -v qtractor >/dev/null; then
  if pgrep -x qtractor >/dev/null; then
    echo "Qtractor is running — skipping (run: python3 qtractor_rig.py down && python3 qtractor_rig.py build)"
  else
    say "Generating the Qtractor rig session"
    python3 "$HERE/qtractor_rig.py" build
  fi
fi

say "Done. Launch TONE3000 from the app menu (256-sample buffer), or the DAW rig: python3 qtractor_rig.py up"
