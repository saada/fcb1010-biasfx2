# AGENTS.md

A free, Linux-native guitar rig that agents build and operate end to end: an FCB1010 drives
TONE3000 (NAM captures + IRs) inside a generated Qtractor session, and GuitarMood
(`guitarmood/`) shows and drives the board.

## Presets are code
The owner plays and agents change the rig, through files and generators, so the rig rebuilds
the same way every time and nobody clicks.
- Sounds live in `rigs/*.json`: edit, then `uv run tone3000.py check`, `build`, `docs` (RIGS.md is generated).
- The session is an output of `qtractor_rig.py`: change the generator and rebuild `rig.qtr`.
- The FCB layout lives in `rig.py`'s tables and changes often, so read it fresh each time from
  `uv run rig.py show` and the README practice guide.
- Free software only: Qtractor, LV2/CLAP plugins, Omarchy plugins, all native (Wine never got
  the latency right, experiments B1). `biasfx2.py`, TONES.md and the BIAS FX 2 parts of
  RIG-NOTES.md are that retired Wine rig, kept as history.
- Run Python with `uv run` (`uv run --project guitarmood` for the app), even where older docs
  say `python3`: uv resolves each script's inline dependencies.

## Fixed slots
TONE3000 maps CCs to block positions, so slot order is fixed (rigs/README.md). A Program
Change re-applies the preset's params after any CCs in the same burst (experiments D8): a
switch that sends a PC sends only CC values equal to the preset's own, and a switch that
changes a param sends CCs alone. `rig.py syx` and `send` assert this through `verify()`.

## Shared hardware
The rig is the owner's instrument, and they may be playing it right now.
- Before any `up`, `down`, `build` or GuitarMood launch, check what's live:
  `pgrep -a qtractor; pgrep -a TONE3000; pgrep -af '[g]uitarmood'`.
  If it's running, adopt it or wait for the owner; the owner's processes stay untouched.
- `tone3000.py build` and `qtractor_rig.py build` overwrite the live `~/.config/TONE3000`
  and `~/Music/fcb-rig/`, whichever worktree runs them.
- A GuitarMood that joins a running rig also owns its shutdown: closing it saves and quits
  Qtractor. `GUITARMOOD_NO_RIG=1` watches without owning.
- The owner's launcher runs GuitarMood from this worktree's checkout. `guitarmood install`
  run anywhere else repoints it.
- Leave nothing running: every process, recorder, `pw-top` and MIDI sender you start, you stop.

## Measure, then claim
Laptop speakers and intuition both mislead, so a sound change is done only when a number from
a run backs it.
- Evidence: xruns (ERR) and B/Q from `pw-top`, loudness of a `pw-record` capture,
  `journalctl --user -u qtractor-rig`, and `Loaded preset:` in `~/.config/TONE3000/TONE3000.log`.
- Reuse the notebook's bench: a dry DI WAV played into `Qtractor:Guitar/in_*` and the FCB's
  exact messages sent with `aseqsend` into the `FCB` port. Keep recorder and player nodes
  persistent, because graph churn makes its own xruns, and give `pw-play` real WAV files,
  because it plays headerless `.raw` as silence (D10).
- experiments/README.md is the lab notebook. Each finding gets the next numbered entry
  (question → hypothesis → method → result → conclusion), with raw data as a CSV beside it
  and a note on whatever was simulated or not yet measured. Cite the entry in the commit subject.

## Secrets and privacy
`~/.config/TONE3000` holds the owner's sign-in and license tokens next to the presets.
Read it through `tone3000.py`, or grep for the one key you need, so token values stay out of
output, commits and web requests. The repo is public, so write LAN addresses as placeholders.

## GuitarMood offscreen
Render the board with no rig and no display, then Read the PNG:
```
cd guitarmood && GUITARMOOD_NO_RIG=1 GUITARMOOD_MIDI_SOURCE="cat tests/fixtures/fcb-live.log" \
  QT_QPA_PLATFORM=offscreen GUITARMOOD_SNAPSHOT=/tmp/gm.png,1200x700 uv run guitarmood
```
In app code, end the event loop with `app.exit(0)`: `quit()` asks the window, which refuses
to close until the rig is down.

## Git
Commit and push every iteration on a branch, so the owner can follow and roll back. Push
with `git push origin HEAD`, because branches here may track `main`. Changes reach `main`
by PR, and the owner merges.
