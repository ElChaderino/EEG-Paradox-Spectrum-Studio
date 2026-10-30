"""Opt-in project handoff for Decoder, Topo, Detection, Sleep and other callers.

SPDX-License-Identifier: GPL-3.0-only
"""
from __future__ import annotations

import json
from pathlib import Path

import numpy as np

from .adapters import analyze_raw, analyze_topo_data
from .export import export_studio
from .review import artifact_review_events, attach_review, normalize_events
from .spectral import SpectrumResult


def event_table_events(table, result, *, sample_clock_origin_seconds=None):
    """Import Decoder EventTable with its recording identity and clock intact.

    A sample-clock stream needs an explicit origin because cropped Raw and
    recording-relative events need not have the same zero. No alignment guessed.
    """
    payload = table.to_dict() if hasattr(table, "to_dict") else table
    if (payload.get("schema") != "eeg-paradox.event-table.v1" or
            not result.metadata.get("source_sha256") or
            payload.get("input_sha256") != result.metadata["source_sha256"] or
            payload.get("sfreq") != result.metadata["sample_rate_hz"]):
        raise ValueError("Event table must match recording hash and sample rate")
    import hashlib
    digest = hashlib.sha256(json.dumps(payload["events"], sort_keys=True, separators=(",", ":"), default=str).encode()).hexdigest()
    if payload.get("content_hash") and digest != payload["content_hash"]:
        raise ValueError("Event table content hash mismatch")
    events = []
    for event in payload["events"]:
        clock = event.get("clock", "recording_relative")
        onset = float(event["onset_s"])
        if clock == "sample_clock":
            if sample_clock_origin_seconds is None or not np.isfinite(sample_clock_origin_seconds) or sample_clock_origin_seconds < 0:
                raise ValueError("Sample-clock events require an explicit original-time origin")
            onset = float(event["sample"]) / payload["sfreq"] + sample_clock_origin_seconds
        elif clock != "recording_relative":
            raise ValueError("Unsupported event clock; supply an explicitly aligned canonical event list")
        events.append({"id": event["event_id"], "onset_seconds": onset,
                       "duration_seconds": event.get("duration_s", 0), "label": event["code"],
                       "kind": "trigger", "status": "recorded",
                       "channels": [name for name in event.get("channels", []) if name in result.metadata["channel_names"]],
                       "origin": "event-table:" + str(payload.get("revision_id")) + ":" + event.get("source", "unknown")})
    return normalize_events(events, result.metadata)


def stage_events(rows, result, *, input_sha256, origin):
    """Overlay supplied sleep-stage intervals; this function does not stage EEG.

    Each row explicitly supplies onset_seconds, duration_seconds, label and a
    reviewed boolean. Unreviewed classifications remain proposed in the viewer.
    """
    if not input_sha256 or input_sha256 != result.metadata.get("source_sha256") or not origin:
        raise ValueError("Stage intervals need matching recording identity and a declared source")
    events = []
    for idx, row in enumerate(rows):
        if not isinstance(row.get("reviewed"), bool):
            raise ValueError("Every imported stage must declare reviewed true or false")
        events.append({"id": "stage:" + str(idx), "onset_seconds": row["onset_seconds"],
                       "duration_seconds": row["duration_seconds"], "label": row["label"], "kind": "sleep_stage",
                       "status": "accepted" if row["reviewed"] else "proposed", "channels": [], "origin": str(origin)})
    return normalize_events(events, result.metadata)


def export_project_review(data, output, *, project, analysis=None, events=None, artifact_review=None,
                          montage=None, positions=None, position_source=None, comparison=None):
    """Export a portable linked review from a calibrated project data object.

    data is SpectrumResult, Raw, or continuous TopoData. Existing processing,
    reports and project defaults are untouched. The returned handoff can be
    linked in any project's report/UI. Browser workspaces use the same schema.
    """
    if isinstance(data, SpectrumResult):
        result = data
    elif hasattr(data, "get_data") and hasattr(data, "info"):
        result = analyze_raw(data, **(analysis or {}))
    elif hasattr(data, "validate") and hasattr(data, "channels"):
        result = analyze_topo_data(data, **(analysis or {}))
    else:
        raise ValueError("Pass a calibrated SpectrumResult, Raw or continuous TopoData")
    attach_review(result, events=events, montage=montage, positions=positions, position_source=position_source, project=project)
    if artifact_review is not None:
        combined = result.review["events"] + artifact_review_events(artifact_review, result)
        attach_review(result, events=combined, project=project)
    page = export_studio(result, output, comparison=comparison)
    handoff = {"schema": "eeg-paradox.spectrum-handoff.v1", "project": str(project),
               "fingerprint": result.review["fingerprint"], "entry": page.name,
               "source_sha256": result.metadata.get("source_sha256"),
               "review_context": "review_context.json", "manifest": "spectrum_manifest.json",
               "workspace_schema": "eeg-paradox.spectrum-workspace.v1"}
    handoff_path = page.parent / "spectrum_handoff.json"
    handoff_path.write_text(json.dumps(handoff, indent=2, allow_nan=False), encoding="utf-8")
    # Add the final integration artifact to the export's hash inventory.
    import hashlib
    manifest_path = page.parent / "spectrum_manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["files_sha256"][handoff_path.name] = hashlib.sha256(handoff_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return handoff_path
