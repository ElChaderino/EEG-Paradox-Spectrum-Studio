"""Calibrated, original-time Hann periodograms; display never changes estimates.

SPDX-License-Identifier: GPL-3.0-only
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from typing import Any, Sequence

import numpy as np
from scipy.signal import periodogram

from .review import QualityConfig, quality_contract


# Match the Decoder visualization convention: SMR is separate, beta starts at 15.
BANDS = {"delta": (0.5, 4.0), "theta": (4.0, 8.0), "alpha": (8.0, 13.0),
         "smr": (12.0, 15.0), "beta": (15.0, 30.0), "high_beta": (22.0, 30.0),
         "gamma_30_45": (30.0, 45.0)}
UNIT_TO_UV = {"V": 1e6, "mV": 1e3, "uV": 1.0, "µV": 1.0, "μV": 1.0, "nV": 1e-3}


@dataclass(frozen=True)
class SpectrumConfig:
    window_seconds: float = 4.0
    hop_seconds: float = 0.5
    fmin_hz: float = 0.5
    fmax_hz: float = 45.0
    max_cells: int = 8_000_000


@dataclass
class SpectrumResult:
    frequencies_hz: np.ndarray
    times_seconds: np.ndarray
    psd_uv2_hz: np.ndarray  # channel, original-time window, frequency; NaN = missing
    valid: np.ndarray  # channel, window
    band_power_uv2: dict[str, np.ndarray]
    trace_times_seconds: np.ndarray
    trace_uv: np.ndarray
    metadata: dict[str, Any] = field(default_factory=dict)
    review: dict[str, Any] = field(default_factory=dict)
    quality: dict[str, np.ndarray] = field(default_factory=dict)


def analyze_array(data: Any, sfreq: float, channel_names: Sequence[str], *, unit: str,
                  config: SpectrumConfig | None = None, start_seconds: float = 0.0,
                  bad_intervals: Sequence[tuple[float, float]] = (),
                  reference: str = "unknown", condition: str = "UNKNOWN",
                  simultaneous: bool | None = None, source: str = "array",
                  quality_config: QualityConfig | None = None) -> SpectrumResult:
    """Analyze channels × samples with a declared unit and original-time offset.

    BAD intervals use the same original-time clock as start_seconds. Every
    intersecting window is invalid, never concatenated or interpolated. NaN/Inf
    invalidate a channel's complete window. No filters or re-reference applied.
    """
    cfg = config or SpectrumConfig()
    qc = (quality_config or QualityConfig()).validate()
    rate = float(sfreq)
    if not np.isfinite([rate, start_seconds, cfg.window_seconds, cfg.hop_seconds,
                        cfg.fmin_hz, cfg.fmax_hz]).all():
        raise ValueError("Sample rate, time and frequency settings must be finite")
    if rate <= 0 or start_seconds < 0:
        raise ValueError("Sample rate must be positive and start time nonnegative")
    if not 0 <= cfg.fmin_hz < cfg.fmax_hz <= rate / 2:
        raise ValueError(f"Need 0 <= fmin < fmax <= Nyquist ({rate / 2:g} Hz)")
    if cfg.window_seconds <= 0 or not 0 < cfg.hop_seconds <= cfg.window_seconds:
        raise ValueError("Need 0 < hop_seconds <= window_seconds")
    if not isinstance(cfg.max_cells, int) or cfg.max_cells < 1:
        raise ValueError("max_cells must be a positive integer")
    if unit not in UNIT_TO_UV:
        raise ValueError("Declare a calibrated signal unit: V, mV, uV or nV")
    values = np.asarray(data, dtype=np.float64)
    names = [str(x) for x in channel_names]
    if values.ndim != 2 or values.shape[0] != len(names) or not names or len(names) > 64:
        raise ValueError("Data must be channels × samples, with 1–64 matching names")
    if len(set(names)) != len(names) or any(not x.strip() for x in names):
        raise ValueError("Channel names must be unique and nonempty")
    if simultaneous is not None and not isinstance(simultaneous, bool):
        raise ValueError("simultaneous must be true, false or unknown (None)")
    n = int(round(cfg.window_seconds * rate))
    hop = int(round(cfg.hop_seconds * rate))
    if n < 8 or hop < 1 or values.shape[1] < n:
        raise ValueError("Need at least one full window (>=8 samples) and a >=1 sample hop")
    starts = np.arange(0, values.shape[1] - n + 1, hop, dtype=np.int64)
    frequencies = np.fft.rfftfreq(n, 1 / rate)
    select = (frequencies >= cfg.fmin_hz) & (frequencies <= cfg.fmax_hz)
    if np.count_nonzero(select) < 2:
        raise ValueError("Frequency range must contain at least two unpadded FFT bins")
    if len(names) * len(starts) * np.count_nonzero(select) > cfg.max_cells:
        raise ValueError("Spectral export exceeds max_cells; shorten duration or increase hop")
    intervals = []
    for interval in bad_intervals:
        if len(interval) != 2:
            raise ValueError("BAD intervals must have (start, stop) seconds")
        lo, hi = map(float, interval)
        if not np.isfinite([lo, hi]).all() or not 0 <= lo < hi:
            raise ValueError("BAD intervals need finite 0 <= start < stop")
        intervals.append((lo, hi))
    times = start_seconds + (starts + n / 2) / rate
    psd = np.full((len(names), len(starts), select.sum()), np.nan, dtype=np.float32)
    valid = np.zeros((len(names), len(starts)), dtype=bool)
    band_power = {name: np.full(valid.shape, np.nan, dtype=np.float64) for name in BANDS}
    quality = {"flags": np.zeros(valid.shape, dtype=np.uint8),
               **{key: np.full(valid.shape, np.nan, dtype=np.float32)
                  for key in ("peak_to_peak_uv", "rms_ac_uv", "line_50_ratio", "line_60_ratio")}}
    # Bands use half-open bins and actual df. A band beyond Nyquist is absent,
    # never a partial estimate bearing the original full-band label.
    band_masks = {name: (frequencies >= lo) & (frequencies < hi)
                  for name, (lo, hi) in BANDS.items() if hi <= rate / 2}
    df = rate / n
    for idx, start in enumerate(starts):
        lo, hi = start_seconds + start / rate, start_seconds + (start + n) / rate
        annotated = any(lo < end and hi > begin for begin, end in intervals)
        chunk = values[:, start:start + n]
        good = np.isfinite(chunk).all(axis=1)
        quality["flags"][~good, idx] |= 1
        if annotated:
            quality["flags"][:, idx] |= 2
        if not good.any():
            continue
        calibrated = chunk[good] * UNIT_TO_UV[unit]
        ptp = np.ptp(calibrated, axis=1)
        quality["peak_to_peak_uv"][good, idx] = ptp
        quality["rms_ac_uv"][good, idx] = np.std(calibrated, axis=1)
        channels = np.flatnonzero(good)
        quality["flags"][channels[ptp <= qc.flat_peak_to_peak_uv], idx] |= 4
        quality["flags"][channels[ptp >= qc.high_peak_to_peak_uv], idx] |= 8
        if qc.physical_rails_uv is not None:
            lower, upper = qc.physical_rails_uv
            at_rail = ((calibrated <= lower) | (calibrated >= upper)).any(axis=1)
            quality["flags"][channels[at_rail], idx] |= 16
        with np.errstate(over="ignore", invalid="ignore"):
            _, power = periodogram(calibrated, fs=rate,
                                   window="hann", detrend="constant", scaling="density")
        total = power[:, frequencies >= 1].sum(axis=1)
        for line in (50, 60):
            if line + 1 > rate / 2:
                continue
            line_power = power[:, (frequencies >= line - 1) & (frequencies <= line + 1)].sum(axis=1)
            ratio = np.divide(line_power, total, out=np.zeros_like(total), where=total > 0)
            quality[f"line_{line}_ratio"][channels, idx] = ratio
            quality["flags"][channels[ratio >= qc.line_ratio_threshold], idx] |= 32
        if annotated:
            continue
        finite = np.isfinite(power).all(axis=1) & (power.max(axis=1) <= np.finfo(np.float32).max)
        channels, power = channels[finite], power[finite]
        valid[channels, idx] = True
        psd[channels, idx] = power[:, select]
        for name, mask in band_masks.items():
            if mask.any():
                band_power[name][channels, idx] = power[:, mask].sum(axis=1) * df
    # A short, unresampled raw scope. It is separate from the PSD window history.
    trace_n = min(values.shape[1], int(round(rate * 12)))
    if len(names) * trace_n > 2_000_000:
        trace_n = max(1, 2_000_000 // len(names))
    trace = (values[:, :trace_n] * UNIT_TO_UV[unit]).astype(np.float64)
    trace[~np.isfinite(trace)] = np.nan
    trace_t = start_seconds + np.arange(trace_n) / rate
    for lo, hi in intervals:
        trace[:, (trace_t >= lo) & (trace_t < hi)] = np.nan
    metadata = {
        "schema": "eeg-paradox.spectrum-studio.v1", "channel_names": names,
        "source": str(source), "condition": str(condition), "reference": str(reference),
        "simultaneous": simultaneous, "source_unit": unit, "psd_unit": "uV^2/Hz",
        "db_reference": "1 uV^2/Hz", "band_power_unit": "uV^2",
        "sample_rate_hz": rate, "nyquist_hz": rate / 2, "config": asdict(cfg),
        "window_samples": n, "hop_samples": hop, "actual_window_seconds": n / rate,
        "actual_hop_seconds": hop / rate, "bin_spacing_hz": df,
        "hann_enbw_hz": 1.5 * df, "taper": "periodic Hann", "detrend": "constant per window",
        "zero_padding": False, "filters_applied": [], "reference_changed": False,
        "selected_start_seconds": start_seconds,
        "selected_stop_seconds": start_seconds + values.shape[1] / rate,
        "trailing_samples_not_windowed": int(values.shape[1] - starts[-1] - n),
        "window_count": len(starts), "invalid_windows_per_channel": (~valid).sum(axis=1).tolist(),
        "bad_intervals_seconds": intervals, "bands_hz": BANDS,
        "unavailable_bands": [name for name in BANDS if name not in band_masks],
        "trace_stop_seconds": start_seconds + trace_n / rate,
        "display_only": True, "clinical_classification": "not_performed",
        "quality_contract": quality_contract(qc),
    }
    return SpectrumResult(frequencies[select], times, psd, valid, band_power, trace_t, trace, metadata,
                          quality=quality)
