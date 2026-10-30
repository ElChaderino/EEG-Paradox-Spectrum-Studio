"""Portable EEG review contracts. No filtering, classification or raw mutation.

SPDX-License-Identifier: GPL-3.0-only
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import asdict, dataclass

import numpy as np


@dataclass(frozen=True)
class QualityConfig:
    flat_peak_to_peak_uv: float = 0.1
    high_peak_to_peak_uv: float = 150.0
    line_ratio_threshold: float = 0.25
    # Only declare rails from calibrated hardware metadata; never infer them.
    physical_rails_uv: tuple[float, float] | None = None

    def validate(self):
        if not np.isfinite([self.flat_peak_to_peak_uv, self.high_peak_to_peak_uv,
                            self.line_ratio_threshold]).all():
            raise ValueError("Quality thresholds must be finite")
        if not 0 <= self.flat_peak_to_peak_uv < self.high_peak_to_peak_uv or not 0 < self.line_ratio_threshold <= 1:
            raise ValueError("Invalid quality thresholds")
        if self.physical_rails_uv is not None:
            lo, hi = self.physical_rails_uv
            if not np.isfinite([lo, hi]).all() or lo >= hi:
                raise ValueError("Physical rails need finite lower < upper in uV")
        return self


QUALITY_BITS = {"nonfinite": 1, "annotated_bad_edge": 2, "flat": 4,
                "high_amplitude": 8, "at_declared_rail": 16, "line_noise": 32}
WORKSPACE_MAX_BYTES = 32_000_000


def quality_contract(config):
    return {"schema": "eeg-paradox.signal-quality.v1", "thresholds": asdict(config),
            "bits": QUALITY_BITS, "exclusions_applied": False,
            "line_ratio": "power within +/-1 Hz of 50 or 60 Hz / 1 Hz through Nyquist; full un-cropped periodogram",
            "rail_check_available": config.physical_rails_uv is not None,
            "meaning": "technical proposals, not accepted artifact exclusions"}


def fingerprint(result):
    digest = hashlib.sha256()
    for value in (result.frequencies_hz, result.times_seconds, result.psd_uv2_hz, result.valid):
        array = np.ascontiguousarray(value)
        digest.update(str(array.dtype).encode())
        digest.update(str(array.shape).encode())
        digest.update(array.tobytes())
    context = {key: result.metadata.get(key) for key in
               ("source_sha256", "source", "channel_names", "reference", "condition", "simultaneous",
                "sample_rate_hz", "window_samples", "hop_samples", "filters_applied")}
    # Preserve old-run fingerprints; detected context belongs to new runs only.
    if 'recording_context' in result.metadata:
        context['recording_context'] = result.metadata['recording_context']
    digest.update(json.dumps(context, sort_keys=True, allow_nan=False).encode())
    return digest.hexdigest()


def normalize_events(events, metadata, *, origin="caller"):
    """Canonical original-time events, including explicitly reviewed sidecars.

    Merely attaching events never changes the PSD validity mask. Native BAD/EDGE
    masking is applied by the signal adapter before this presentation step.
    """
    if not isinstance(events, (list, tuple)) or len(events) > 10000:
        raise ValueError("Events must be a list of at most 10000 entries")
    names = metadata["channel_names"]
    normalized, ids = [], set()
    for idx, item in enumerate(events):
        if not isinstance(item, dict):
            raise ValueError("Every event must be an object")
        onset = float(item.get("onset_seconds", item.get("onset", -1)))
        duration = float(item.get("duration_seconds", item.get("duration", 0)))
        label = str(item.get("label", item.get("description", "")))
        status = item.get("status", "recorded")
        channels = item.get("channels", [])
        event_id = str(item.get("id", f"{origin}:{idx}"))
        if (not np.isfinite([onset, duration]).all() or onset < 0 or duration < 0 or
                not label.strip() or len(label) > 500 or status not in ("recorded", "proposed", "accepted", "rejected") or
                not isinstance(channels, (list, tuple)) or not set(channels) <= set(names) or
                not event_id or len(event_id) > 200 or event_id in ids):
            raise ValueError("Invalid event time, label, review status, channel or ID")
        ids.add(event_id)
        normalized.append({"id": event_id, "onset_seconds": onset, "duration_seconds": duration,
                           "label": label, "kind": str(item.get("kind", "annotation"))[:80],
                           "status": status, "channels": list(channels),
                           "origin": str(item.get("origin", origin))[:200]})
    return sorted(normalized, key=lambda x: (x["onset_seconds"], x["id"]))


def attach_review(result, *, events=None, montage=None, positions=None, position_source=None, project="Spectrum Studio"):
    """Attach review context to any calibrated SpectrumResult for any project.

    montage='standard_1020' is an explicit template choice, never measured
    anatomy. Provided positions are normalized scalp XY coordinates, with a
    mandatory origin label. Maps require declared or file-backed time alignment.
    """
    meta = result.metadata
    updated = dict(result.review)
    raw_events = events if events is not None else updated.get("events", meta.get("annotations", []))
    updated["events"] = normalize_events(raw_events, meta)
    updated["project"] = str(project)[:200]
    if montage is not None and positions is not None:
        raise ValueError("Choose a template montage or supplied positions")
    if montage is not None:
        if montage != "standard_1020":
            raise ValueError("Supported template montage: standard_1020")
        import mne
        xyz = mne.channels.make_standard_montage(montage).get_positions()["ch_pos"]
        lookup = {name.casefold(): value for name, value in xyz.items()}
        positions = {}
        for name in meta["channel_names"]:
            canonical = re.sub(r"^(?:EEG[ _-]+)", "", name, flags=re.I)
            canonical = re.sub(r"[-_ ]REF$", "", canonical, flags=re.I)
            point = lookup.get(canonical.casefold())
            if point is None:
                continue  # Bipolar pairs and unknown labels never get invented sites.
            x, y, z = map(float, point)
            radial = np.hypot(x, y)
            radius = np.arctan2(radial, z) / (np.pi / 2)
            positions[name] = [float(radius * x / radial) if radial else 0.,
                               float(radius * y / radial) if radial else 0.]
        position_source = "standard_1020 template; schematic azimuthal projection; not measured positions"
    if positions is not None:
        if not position_source or not isinstance(positions, dict) or not set(positions) <= set(meta["channel_names"]):
            raise ValueError("Positions need existing channel labels and a declared source")
        clean = {}
        for name, point in positions.items():
            if len(point) != 2 or not np.isfinite(point).all() or np.linalg.norm(point) > 1.5:
                raise ValueError("Positions must be finite scalp XY within radius 1.5")
            clean[name] = list(map(float, point))
        from .recording_context import scalp_time_aligned
        updated["montage"] = {"positions_xy": clean, "source": str(position_source),
                                    "simultaneous": meta.get("simultaneous") is True,
                                    "time_aligned": scalp_time_aligned(meta),
                                    "interpolation": "display-only inverse-distance weighting, power in uV^2; no source localization"}
    updated["fingerprint"] = fingerprint(result)
    result.review = updated
    return result


def artifact_review_events(sidecar, result):
    """Preserve review decisions from Decoder ArtifactReviewSidecar objects/dicts."""
    payload = sidecar.to_dict() if hasattr(sidecar, "to_dict") else sidecar
    if (not isinstance(payload, dict) or payload.get("schema") != "eeg-paradox.artifact-review.v1" or
            not isinstance(payload.get("annotations"), list) or payload.get("raw_input_modified", False)):
        raise ValueError("Expected an artifact review sidecar dictionary")
    annotation_hash = hashlib.sha256(json.dumps(payload["annotations"], sort_keys=True,
                                               separators=(",", ":"), ensure_ascii=True).encode()).hexdigest()
    if payload.get("annotation_hash") and annotation_hash != payload["annotation_hash"]:
        raise ValueError("Artifact sidecar annotation hash mismatch")
    if (not result.metadata.get("source_sha256") or payload.get("input_sha256") != result.metadata["source_sha256"] or
            payload.get("sfreq") != result.metadata["sample_rate_hz"]):
        raise ValueError("Artifact sidecar must match the recording hash and sample rate")
    events = []
    for item in payload["annotations"]:
        channels = [name for name in item.get("channels", []) if name in result.metadata["channel_names"]]
        if item.get("channels") and not channels:
            continue
        events.append({"id": item["annotation_id"], "onset_seconds": item["onset_s"],
                       "duration_seconds": item["duration_s"], "label": item["reason"],
                       "kind": "artifact", "status": item["status"], "channels": channels,
                       "origin": "artifact-review:" + str(payload.get("revision_id", "unknown"))})
    return normalize_events(events, result.metadata)


def comparison_contract(metadata):
    return {key: metadata.get(key) for key in ("reference", "sample_rate_hz", "window_samples",
            "taper", "detrend", "zero_padding", "filters_applied", "reference_changed", "psd_unit")}


def load_workspace(path, result, *, comparison=None):
    """Validate a portable review handoff against the exact retained recording."""
    from pathlib import Path
    path = Path(path)
    if path.stat().st_size > WORKSPACE_MAX_BYTES:
        raise ValueError("Workspace exceeds 32 MB")
    workspace = json.loads(path.read_text(encoding="utf-8"))
    if workspace.get("schema") != "eeg-paradox.spectrum-workspace.v1" or workspace.get("fingerprint") != fingerprint(result):
        raise ValueError("Workspace belongs to different data or recording context")
    view, review = workspace.get("view"), workspace.get("review")
    if not isinstance(view, dict) or not isinstance(review, dict):
        raise ValueError("Workspace needs view and review objects")
    channels = result.metadata["channel_names"]
    def numeric_pair(value):
        return isinstance(value,list) and len(value)==2 and all(type(x) in (int,float) and np.isfinite(x) for x in value)
    def indices(values, limit):
        return (isinstance(values, list) and 0 < len(values) <= limit
                and all(type(x) is int and 0 <= x < len(channels) for x in values)
                and len(set(values)) == len(values))
    if (type(view.get("ch")) is not int or not 0 <= view["ch"] < len(channels) or
            type(view.get("row")) is not int or not 0 <= view["row"] < len(result.times_seconds)):
        raise ValueError("Invalid workspace cursor")
    for key, lower, upper in (("low", result.frequencies_hz[0], result.frequencies_hz[-1]),
                              ("high", result.frequencies_hz[0], result.frequencies_hz[-1]),
                              ("floor", -90, 20), ("range", 10, 90), ("perspective", .2, .65), ("lift", .4, 1.6)):
        value = view.get(key)
        if type(value) not in (int, float) or not np.isfinite(value) or not lower <= value <= upper:
            raise ValueError("Invalid workspace display value: " + key)
    if (view["low"] >= view["high"] or view.get("palette") not in ("sdr", "thermal", "ice") or
            view.get("quality") not in ("balanced", "high") or view.get("depth") not in (32, 64, 96, 128) or
            view.get("dock") not in ("summary", "compare", "map", "ab", "events", "trend") or
            type(view.get("scopeCentered")) is not bool or type(view.get("focus")) is not bool or
            type(view.get("mapExpanded",False)) is not bool or
            (view.get("mapExpanded",False) and view.get("focus")) or
            view.get("speed") not in (1, 2, 4) or not indices(review.get("panels"), 4)):
        raise ValueError("Invalid workspace display/layout settings")
    compare = view.get("compare")
    if compare != [] and not indices(compare, 8):
        raise ValueError("Invalid comparison channels")
    band = review.get("band")
    if (not numeric_pair(band) or
            not result.frequencies_hz[0] <= band[0] < band[1] <= result.frequencies_hz[-1] or
            type(review.get("linked")) is not bool or review.get("fps") not in (15, 30, 60)):
        raise ValueError("Invalid workspace band or display rate")
    maximum = review.get("mapMax")
    if (type(maximum) not in (int, float) or not np.isfinite(maximum) or not 0 < maximum <= 1e9 or
            review.get("mapBand") not in ("custom", *result.metadata["bands_hz"]) or
            review.get("bSource") not in ("primary", "imported")):
        raise ValueError("Invalid map or comparison settings")
    if review["bSource"] == "imported":
        if comparison is None or workspace.get("external_comparison", {}).get("fingerprint") != fingerprint(comparison):
            raise ValueError("Workspace requires the matching external comparison result")
    for key in ("a", "b"):
        target = comparison if key == "b" and review["bSource"] == "imported" else result
        interval = review.get(key)
        if (not numeric_pair(interval) or
                not target.metadata["selected_start_seconds"] <= interval[0] < interval[1] <= target.metadata["selected_stop_seconds"]):
            raise ValueError("Invalid workspace interval: " + key)
    normalize_events(review.get("events"), result.metadata)
    interval=review.get("trend",[result.metadata["selected_start_seconds"],result.metadata["selected_stop_seconds"]])
    if (not numeric_pair(interval) or
            not result.metadata["selected_start_seconds"]<=interval[0]<interval[1]<=result.metadata["selected_stop_seconds"]):
        raise ValueError("Invalid workspace trend interval")
    marker_seconds=review.get("markerSeconds",0)
    if type(marker_seconds) not in (int,float) or marker_seconds not in (0,5,10,15,30,60,120,300,600,900,1800,3600):
        raise ValueError("Invalid workspace waterfall clock interval")
    return workspace
