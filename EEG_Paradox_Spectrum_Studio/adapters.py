"""Opt-in bridges for MNE/Decoder Raw, TopoData and calibrated EDF/BDF/FIF.

SPDX-License-Identifier: GPL-3.0-only
"""
from __future__ import annotations

import re

import numpy as np

from .spectral import SpectrumConfig, analyze_array


_AUXILIARY = re.compile(
    r"^\s*(?:EEG[\s_-]+)?(?:EOG|ECG|EKG|EMG|TRIG(?:GER)?|STATUS|STIM|AUX|ACC|COUNTER|"
    r"REF(?:ERENCE)?|VSYN|ASYN|FOTO|PHOTO(?:DIODE)?|LABEL|MARKER|EVENT|ANNOTATIONS?)(?:\d|\b)", re.I)


def _eeg_picks(raw, picks):
    kinds = dict(zip(raw.ch_names, raw.get_channel_types()))
    if picks is None:
        bads = raw.info.get("bads", [])
        picks = [name for name in raw.ch_names if kinds[name] == "eeg"
                 and name not in bads and not _AUXILIARY.search(name)]
    picks = list(picks)
    if not picks or len(set(picks)) != len(picks) or not set(picks) <= set(raw.ch_names):
        raise ValueError("Choose unique existing EEG channel names")
    if any(kinds[name] != "eeg" or _AUXILIARY.search(name) for name in picks):
        raise ValueError("Non-EEG or auxiliary channels cannot be selected as EEG")
    return picks


def analyze_raw(raw, *, picks=None, start=0.0, duration=120.0, config=None,
                reference="unknown", condition="UNKNOWN", simultaneous=None, source="Raw"):
    """Preserve Raw reference/annotations and reader-supplied display geometry.

    start/duration are relative to this Raw's first retained sample. For cropped
    MNE Raw, first_time is added to exported original-time coordinates.
    """
    rate = float(raw.info["sfreq"])
    if not np.isfinite(start) or start < 0 or (duration is not None and (not np.isfinite(duration) or duration <= 0)):
        raise ValueError("start must be nonnegative and duration positive/finite")
    picks = _eeg_picks(raw, picks)
    first = float(getattr(raw, "first_time", 0.0))
    begin = int(round(start * rate))
    # Whole remaining data ends at the retained sample boundary. Converting a
    # rounded start back through a floating duration can drop the final sample
    # (and a complete FFT window) at half-sample starts.
    end = raw.n_times if duration is None else min(raw.n_times, begin + int(round(duration * rate)))
    if begin >= end:
        raise ValueError("Time selection lies outside the recording")
    # MNE Raw annotations use the acquisition time origin, including first_time.
    intervals = []
    markers = []
    for annotation in raw.annotations:
        onset, span = float(annotation["onset"]), float(annotation["duration"])
        description = str(annotation["description"])
        markers.append({"onset_seconds": onset, "duration_seconds": span, "description": description})
        if description.lower().startswith(("bad", "edge")):
            lo = max(0., onset)
            intervals.append((lo, max(lo + 1 / rate, onset + span)))
    unit = getattr(raw, "_eeg_paradox_data_unit", "V")
    result = analyze_array(raw.get_data(picks=picks, start=begin, stop=end), rate, picks,
                           unit=unit, config=config, start_seconds=first + begin / rate,
                           bad_intervals=intervals, reference=reference, condition=condition,
                           simultaneous=simultaneous, source=source)
    result.metadata["annotations"] = markers
    result.metadata["adapter"] = "MNE_or_Decoder_Raw"
    result.metadata["excluded_channels"] = [name for name in raw.ch_names if name not in picks]
    result.metadata["recording_duration_seconds"] = raw.n_times / rate
    from .recording_context import raw_recording_context, attach_recording_context
    attach_recording_context(result,raw_recording_context(raw))
    return result


def analyze_topo_data(dataset, **context):
    """Use Topo Studio's continuous TopoData; precomputed maps lack a timebase."""
    # TopoData.validate normalizes labels in place. Work on a copy so the shared
    # adapter cannot change the caller's project dataset.
    import copy
    dataset = copy.deepcopy(dataset)
    dataset.validate()
    if dataset.data is None:
        raise ValueError("Spectrum history needs continuous signal samples, not topographic maps")
    return analyze_array(dataset.data, dataset.sfreq, dataset.channels, unit=dataset.unit, **context)


def analyze_file(path, **kwargs):
    """Use calibrated EDF/BDF/FIF, MNE formats, tabular signals or native intake."""
    from .intake import analyze_recording
    return analyze_recording(path,**kwargs)
