# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Pickup voicings for the owner's JP70 (experiments D29): the pre-model EQ that makes its hot
ceramic humbucker feed an amp capture like the record's pickup.

  uv run experiments/pickup_voicing.py [OUT.csv]

Each pickup is the textbook lumped model: the string's EMF (the same for every pickup, so it
drops out of a ratio) through the coil's DC resistance R and inductance L, into the coil's own
capacitance plus the cable's (500 pF), loaded by the guitar's pots and a 1 MOhm amp input.
H(f) = Zp / (R + j w L + Zp), Zp = Rload || 1/(j w C). The voicing is H_target / H_JP70, in dB,
re-referenced to its 650 Hz value (the level is re-trimmed with out_db anyway), then fitted to
the six TONE3000 EQ bands (tone3000.EQ_BANDS: RBJ shelves/bells) by least squares over 60 Hz-10 kHz.

What it leaves out: eddy-current damping (it lowers a humbucker's resonance peak, more so in a
hot ceramic one with steel parts), the two coils' aperture comb (a humbucker senses the string
at two points, which costs some upper harmonics), the pickup's position on the string, and the
pots' taper and tone setting. So the raw model overstates the treble gap above the JP70's
resonance; the curves the rig uses cap each band at +-6 dB (see D29).
"""

import csv
import math
import sys

import numpy as np

RATE = 48000.0
BANDS = [("lowshelf", 100.0, 0.71), ("bell", 250.0, 1.0), ("bell", 650.0, 1.0),
         ("bell", 1600.0, 1.0), ("bell", 3500.0, 1.4), ("highshelf", 8000.0, 0.71)]  # = tone3000.EQ_BANDS
CABLE_PF = 500.0
AMP_OHM = 1e6

# name: (R ohm, L henry, self-capacitance pF, volume || tone pot ohm, source)
PICKUPS = {
    "jp70-bridge": (18.0e3, 8.0, 150.0, 250e3, "DCR 17.97/18.39 kOhm measured on two used JP70s (Chicago Music Exchange); "
                    "L estimated from hot ceramic humbuckers of that DCR (Seymour Duncan JB class, ~8 H); 500k pots"),
    "jp70-neck": (10.2e3, 4.5, 120.0, 250e3, "DCR 10.17/10.33 kOhm (same listings); L ~4.5 H (PAF-class); 500k pots"),
    "single-coil": (6.2e3, 2.2, 100.0, 125e3, "Strat/Tele: L 1.8-2.5 H (Planet Z), ~2 H + 100 pF (Blackstone); 250k pots"),
    "p90": (8.0e3, 6.0, 120.0, 250e3, "P-90: ~6 H (Blackstone Appliances); 500k pots"),
    "filtertron": (4.5e3, 1.6, 90.0, 250e3, "Filter'Tron: 1.4/1.8 H neck/bridge (TV Jones Classic, per forum), ~4-5 kOhm (WGS); 500k pots"),
    "paf": (8.0e3, 4.0, 120.0, 250e3, "PAF: 3.2-4.6 H (Planet Z), 7.5-9.5 kOhm (Wikipedia); 500k pots"),
}


def pickup_db(name, f):
    R, L, c_pf, pots, _ = PICKUPS[name]
    w = 2 * math.pi * f
    C = (c_pf + CABLE_PF) * 1e-12
    rload = 1 / (1 / pots + 1 / AMP_OHM)
    zp = 1 / (1 / rload + 1j * w * C)
    h = zp / (R + 1j * w * L + zp)
    return 20 * np.log10(np.abs(h))


def biquad_db(kind, f0, q, gain, f):
    """RBJ cookbook magnitude (dB) at frequencies f."""
    A, w0 = 10 ** (gain / 40), 2 * math.pi * f0 / RATE
    cw, sw = math.cos(w0), math.sin(w0)
    alpha = sw / (2 * q)
    if kind == "bell":
        b = [1 + alpha * A, -2 * cw, 1 - alpha * A]
        a = [1 + alpha / A, -2 * cw, 1 - alpha / A]
    else:
        sa = 2 * math.sqrt(A) * alpha
        s = 1 if kind == "lowshelf" else -1
        b = [A * ((A + 1) - s * (A - 1) * cw + sa), s * 2 * A * ((A - 1) - s * (A + 1) * cw), A * ((A + 1) - s * (A - 1) * cw - sa)]
        a = [(A + 1) + s * (A - 1) * cw + sa, -s * 2 * ((A - 1) + s * (A + 1) * cw), (A + 1) + s * (A - 1) * cw - sa]
    z = np.exp(-1j * 2 * np.pi * f / RATE)
    return 20 * np.log10(np.abs((b[0] + b[1] * z + b[2] * z * z) / (a[0] + a[1] * z + a[2] * z * z)))


def eq_db(gains, f):
    return sum(biquad_db(k, f0, q, g, f) for (k, f0, q), g in zip(BANDS, gains))


def fit(target, f, cap=None):
    """Six band gains whose EQ curve best matches target (dB) on the log grid f."""
    g = np.zeros(6)
    for _ in range(8):  # Gauss-Newton on the (nearly linear) band responses
        base = eq_db(g, f)
        J = np.stack([(eq_db(g + np.eye(6)[i] * 0.5, f) - base) / 0.5 for i in range(6)], 1)
        step, *_ = np.linalg.lstsq(J, target - base, rcond=None)
        g = g + step
        if cap is not None:
            g = np.clip(g, -cap, cap)
    return g


def main(out=None):
    f = np.geomspace(60, 10000, 200)
    probe = np.array([62.0, 100, 250, 650, 1600, 3500, 5000, 8000])
    rows = []
    for ref in ("jp70-bridge", "jp70-neck"):
        for tgt in ("single-coil", "p90", "filtertron", "paf"):
            diff = lambda x: pickup_db(tgt, x) - pickup_db(ref, x) - (pickup_db(tgt, np.array([650.0])) - pickup_db(ref, np.array([650.0])))[0]
            raw = fit(diff(f), f)
            capped = fit(diff(f), f, cap=6.0)
            rows.append(dict(reference=ref, target=tgt, **{f"diff_{int(p)}": round(float(v), 1) for p, v in zip(probe, diff(probe))},
                             fit=" ".join(f"{v:+.1f}" for v in raw), fit_capped6=" ".join(f"{v:+.1f}" for v in capped)))
            print(f"{ref:12} -> {tgt:12} diff@{'/'.join(str(int(p)) for p in probe)} Hz: "
                  + " ".join(f"{v:+5.1f}" for v in diff(probe)) + f" | fit {rows[-1]['fit']} | capped {rows[-1]['fit_capped6']}")
    for name in PICKUPS:
        db = pickup_db(name, f)
        print(f"{name:12} resonance {f[np.argmax(db)]:6.0f} Hz, peak {db.max() - pickup_db(name, np.array([300.0]))[0]:+.1f} dB over 300 Hz;  {PICKUPS[name][4]}")
    if out:
        with open(out, "w", newline="") as fh:
            w = csv.DictWriter(fh, fieldnames=list(rows[0]))
            w.writeheader()
            w.writerows(rows)


if __name__ == "__main__":
    main(*sys.argv[1:])
