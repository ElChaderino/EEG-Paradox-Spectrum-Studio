"""Deterministic engineering fixture, visibly labelled synthetic throughout."""
import numpy as np

from .spectral import analyze_array


def make_demo(config=None, *, gain=1.0):
    if not np.isfinite(gain) or gain <= 0:
        raise ValueError("Synthetic gain must be positive and finite")
    rate = 256.
    t = np.arange(int(90 * rate)) / rate
    rng = np.random.default_rng(20261026)
    names = ["Fp1", "Fp2", "F3", "F4", "C3", "C4", "O1", "O2"]
    data = []
    for idx in range(len(names)):
        alpha = (8 + idx * 1.6) * (1 + .55 * np.sin(t / 13 + idx))
        chirp = 9.5 * t + .018 * t ** 2
        signal = alpha * np.sin(2 * np.pi * chirp + idx * .16)
        signal += 7 * np.sin(2 * np.pi * (5 + idx * .05) * t)
        signal += 3 * np.sin(2 * np.pi * 22 * t + idx)
        signal += 2 * rng.normal(size=t.shape)
        if idx < 2:
            signal += 60 * np.exp(-((t - 26) / .3) ** 2)
        data.append(signal)
    result = analyze_array(np.asarray(data)*gain, rate, names, unit="uV", config=config,
                           bad_intervals=[(41., 44.)], reference="synthetic common reference",
                           condition="SYNTHETIC ENGINEERING DEMO", simultaneous=True,
                           source=f"Synthetic signals · gain {gain:g} · no patient recording")
    result.metadata["synthetic"] = True
    result.metadata["synthetic_amplitude_gain"] = float(gain)
    result.metadata["annotations"] = [
        {"onset_seconds": 8., "duration_seconds": 20., "description": "Synthetic task A", "kind": "task"},
        {"onset_seconds": 41., "duration_seconds": 3., "description": "Synthetic BAD interval", "kind": "artifact", "status": "accepted"},
        {"onset_seconds": 50., "duration_seconds": 20., "description": "Synthetic task B", "kind": "task"},
        {"onset_seconds": 75., "duration_seconds": 1., "description": "Synthetic review proposal", "kind": "artifact", "status": "proposed"},
    ]
    return result
