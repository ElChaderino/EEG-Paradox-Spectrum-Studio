"""Audited WinEEG intake through the installed, maintained binary converter.

Original .EEG recordings remain authoritative. The installed fixed-range export
would clip these DC-coupled recordings. This adapter uses its EDF serializer
with full-range channel calibration and verifies every sample independently,
retains native event groups separately, and never infers recording reference
from a saved display montage. No patient name or date of birth is exported.
"""
from __future__ import annotations

from collections import Counter
from datetime import datetime
from decimal import Decimal
import hashlib
import importlib
import json
import math
from pathlib import Path
import re
import sys
import struct
from typing import Any

import numpy as np

SUITE_ROOT = Path(__file__).resolve().parents[2]
CONVERTER_ROOT = SUITE_ROOT / "vendor_sources" / "wineeg_converter"
PROFILE_EVIDENCE = SUITE_ROOT / "provenance" / "discovery" / "SOURCE_REFERENCE_INDEX.json"
QUANTIZATION_NOTICE = (
    "Native calibrated samples, including their large DC offsets, are preserved "
    "in range-adaptive EDF using the maintained converter's serializer. Per-channel "
    "physical minima/maxima enclose all samples; nearest-integer 16-bit quantization "
    "is measured separately for each channel. This is not bit-lossless EDF. No DC "
    "subtraction, filtering or rereferencing was applied at intake. Original "
    "integer samples and native calibration are also retained in an NPZ archive."
)


def _converter(root: Path = CONVERTER_ROOT):
    root = root.resolve()
    if not (root / "wineeg_converter/core.py").is_file():
        raise FileNotFoundError(f"Maintained WinEEG converter unavailable: {root}")
    sys.path.insert(0, str(root))
    core = importlib.import_module("wineeg_converter.core")
    service = importlib.import_module("wineeg_converter.service")
    if not Path(core.__file__).resolve().is_relative_to(root):
        raise RuntimeError("Another WinEEG converter is already imported; use an isolated process")
    return core, service


def _profile_metadata(raw: bytes, source_name: str, profile_offset: int = 0x80) -> dict:
    """Derive age in memory only, requiring an independently matching study date."""
    out = {"age_years": None, "sex": "U", "recording_date": None,
           "demographic_status": "unverified", "demographic_evidence": str(PROFILE_EVIDENCE)}
    if profile_offset != 0x80 or len(raw) < 0x11C:
        return out
    def field(offset, length):
        return raw[offset:offset + length].split(b"\0", 1)[0].decode("ascii", "strict").strip()
    try:
        study = datetime.strptime(field(0xC4, 12), "%d/%m/%Y")
        filename_date = re.search(r"(?<!\d)(20\d{6})(?!\d)", source_name)
        if not filename_date or filename_date.group(1) != study.strftime("%Y%m%d"):
            return out
        sex = field(0x11A, 2).upper()
        out.update(sex=sex if sex in {"M", "F"} else "U", recording_date=study.date().isoformat())
        birth = datetime.strptime(field(0x10E, 12), "%d/%m/%Y")
        age = (study - birth).days / 365.2425
        if not 0 <= age <= 120:
            return out
        out.update(age_years=age, demographic_status="verified_native_profile",
                   date_order_evidence="Native day/month/year study date agrees with filename YYYYMMDD")
    except (ValueError, UnicodeError):
        pass
    return out


def _digital_matrix(eeg):
    import numpy as np
    frames, channels = eeg.physical_frame_count, len(eeg.channels)
    width = eeg.header["sample_width_bytes"]
    start = eeg.header["sample_data_offset"]
    end = start + frames * channels * width
    data = memoryview(eeg.raw)[start:end]
    if width == 2:
        return np.frombuffer(data, dtype="<i2").reshape(frames, channels).astype(np.int32)
    if width != 3:
        raise ValueError(f"Unsupported WinEEG sample width {width}")
    packed = np.frombuffer(data, dtype=np.uint8).reshape(frames, channels, 3).astype(np.int32)
    unsigned = packed[..., 0] | packed[..., 1] << 8 | packed[..., 2] << 16
    return (unsigned ^ 0x800000) - 0x800000


def _physical_matrix(eeg):
    import numpy as np
    if eeg.trailing_sample_bytes or eeg.physical_frame_count != eeg.header["logical_frame_count"]:
        raise ValueError("Native logical/physical frame mismatch or partial frame; automatic export would lose data")
    if eeg.physical_frame_count <= 0 or eeg.header["sample_rate_hz"] <= 0:
        raise ValueError("Native recording has no valid samples or sample rate")
    scales = np.array([c.scale for c in eeg.channels], dtype=np.float64)
    zeros = np.array([c.zero_offset for c in eeg.channels], dtype=np.float64)
    if not np.isfinite(scales).all() or not np.isfinite(zeros).all() or np.any(scales == 0):
        raise ValueError("Native channel calibration is nonfinite or zero; refusing converter fallback calibration")
    physical = (_digital_matrix(eeg) - zeros) * scales
    if not np.isfinite(physical).all():
        raise ValueError("Native calibrated samples contain nonfinite values")
    return physical


def _adaptive_calibration(values, is_label=False):
    """Return exact integer EDF header extrema, never truncated ASCII decimals."""
    import numpy as np
    lo, hi = math.floor(float(np.min(values))), math.ceil(float(np.max(values)))
    if is_label and lo >= -32767 and hi <= 32767:
        lo, hi = -32767, 32767
    elif lo == hi:
        lo, hi = lo - 1, hi + 1
    if len(str(lo)) > 8 or len(str(hi)) > 8:
        raise ValueError("Native calibrated extent cannot fit exact EDF physical header fields")
    quantum = (hi - lo) / 65534.0
    digital = np.rint((values - lo) / quantum - 32767).astype(np.int32)
    if np.any(digital < -32767) or np.any(digital > 32767):
        raise ValueError("Adaptive EDF calibration would clip samples")
    return lo, hi, quantum, digital


def _exact_annotation_records(annotations, record_count, samples_per_record, rate, core, *, timekeeping):
    """Assign each TAL once using exact decimal timing, not float boundaries.

    The maintained converter's interval comparisons can duplicate or omit an
    onset exactly on a short EDF record boundary. Native events have integer-ms
    timing; Decimal(str(onset)) retains that when selecting a record.
    """
    records = [bytearray() for _ in range(record_count)]
    if timekeeping:
        for index, payload in enumerate(records):
            onset = index * samples_per_record / rate
            text = f"{onset:+.6f}".rstrip("0").rstrip(".")
            payload.extend(text.encode("ascii") + b"\x14\x14\0")
    for annotation in annotations:
        index = int((Decimal(str(annotation.onset_seconds)) * rate) // samples_per_record)
        if annotation.onset_seconds < 0 or index >= record_count:
            raise ValueError("Native annotation lies outside the retained sample timeline")
        records[index].extend(core._annotation_tal(annotation))
    samples = max(64, (max((len(payload) for payload in records), default=0) + 1) // 2)
    return records, samples


def _write_range_preserving_edf(path: Path, eeg, physical, core) -> dict:
    """Reuse proven native annotation serialization with non-clipping EDF ranges."""
    rate, frames = eeg.header["sample_rate_hz"], eeg.header["logical_frame_count"]
    samples_per_record = core._largest_divisor_at_most(frames, rate * 60)
    record_count = frames // samples_per_record
    record_duration = samples_per_record / rate
    if float(core._edf_field(record_duration, 8)) != record_duration:
        raise ValueError("EDF record duration would change when serialized")
    signals, digital_arrays, ranges = [], [], []
    for index, descriptor in enumerate(eeg.channels):
        is_label = descriptor.kind == 4 or descriptor.label.strip().upper() == "LABEL"
        lo, hi, quantum, digital = _adaptive_calibration(physical[:, index], is_label)
        signals.append(core.EDFSignal(descriptor.label, "" if is_label else "AgAgCl electrode",
                                      "" if is_label else "uV", lo, hi, -32767, 32767, "", samples_per_record))
        digital_arrays.append(digital.tolist())
        ranges.append({"label": descriptor.label, "physical_min": lo, "physical_max": hi,
                       "quantum": quantum, "rounding": "nearest integer (ties to even)"})
    lanes = [[] for _ in range(5)]
    marks = core.parse_legacy_marks(eeg, include_text=False)
    for index, mark in enumerate(marks):
        lane = 3 if mark.family == "extended_type_0" else 2 if mark.family.startswith("extended_type_") else 1
        label = f"BAD_native_artifact_{index + 1:04d}" if lane == 3 else f"EVENT_{index + 1:04d}"
        lanes[lane].append(core.EDFAnnotation(mark.onset_ms / 1000,
                                            mark.duration_ms / 1000 if mark.duration_ms else None, label, lane))
    groups = core.parse_legacy_event_groups(eeg)
    for event in core.parse_legacy_mapped_events(eeg):
        lanes[4].append(core.EDFAnnotation(event.onset_ms / 1000, groups[event.group_index].duration_ms / 1000,
                                           str(event.event_code), 4))
    for lane_index, annotations in enumerate(lanes):
        records, samples = _exact_annotation_records(annotations, record_count, samples_per_record, rate,
                                                      core, timekeeping=lane_index == 0)
        signals.append(core.EDFSignal("EDF Annotations", "", "", -1, 1, -32768, 32767, "", samples))
        values = []
        for payload in records:
            values.extend(struct.unpack("<" + "h" * samples, payload.ljust(samples * 2, b"\0")))
        digital_arrays.append(values)
    edf = core.EDFFile("0", "X X X X", "Startdate X X X WinEEG-range-adaptive",
                       "01.01.85", "00.00.00", "EDF+C", record_count, record_duration,
                       signals, digital_arrays, [a for lane in lanes for a in lane])
    core.write_edf(path, edf)
    return {"format": "wineeg_range_preserving_edf_adapter_v1", "channel_ranges": ranges,
            "converted_frames": frames, "sample_rate_hz": rate, "data_record_count": record_count,
            "data_record_duration": record_duration, "annotation_lane_counts": [len(x) for x in lanes],
            "fixed_range_converter_used": False,
            "annotation_assignment": "Exact decimal onset-to-record assignment; native floating-interval boundary bug avoided.",
            "fixed_range_would_clip": bool(np.any(np.abs(physical) > 8191.75)),
            "reason": "Range-adaptive calibration encloses every native sample and exposes the measured channel-specific quantization error.",
            "signal_transform": "Calibration and nearest-integer quantization only; no DC removal, filtering or rereferencing."}


def _event_inventory(eeg, core) -> dict:
    from dataclasses import asdict
    rate = eeg.header["sample_rate_hz"]
    groups = core.parse_legacy_event_groups(eeg)
    mapped = core.parse_legacy_mapped_events(eeg)
    marks = core.parse_legacy_marks(eeg, include_text=True)
    events = []
    for index, event in enumerate(mapped):
        group = groups[event.group_index]
        entries = []
        for entry_index, entry in enumerate(group.entries):
            onset = event.onset_ms + entry.relative_ms
            entries.append({**asdict(entry), "entry_index": entry_index,
                            "onset_ms": onset, "sample_index_exact": onset * rate / 1000.0,
                            "entry_role": "zero_code_boundary" if entry.stimulus_code == 0 else "native_nonzero_stimulus_code"})
        events.append({**asdict(event), "event_index": index,
                       "duration_ms": group.duration_ms,
                       "group_start_sample_exact": event.onset_ms * rate / 1000.0,
                       "entries": entries})
    return {"schema": "eeg_paradox_native_wineeg_events_v1",
            "sample_rate_hz": rate, "protocol": "VCPT" if any(re.search(r"\bVCPT\b", m.label) for m in marks) else None,
            "primary_marks": [{"onset_ms": m.onset_ms, "duration_ms": m.duration_ms,
                               "family": m.family, "label": f"EVENT_{i + 1:04d}"} for i, m in enumerate(marks)],
            "mapped_event_count": len(mapped), "class_counts": dict(Counter(str(e.event_code) for e in mapped)),
            "groups": [{"group_index": i, **asdict(g)} for i, g in enumerate(groups)],
            "mapped_events": events,
            "timing_notice": "Mapped onset is native group start, not stimulus onset. Use actual group-entry relative times; zero stimulus code is retained as a boundary, not classified as a stimulus. No behavioral class meanings are inferred.",
            "privacy": "Free-text marks redacted. Numeric native codes and exact group selection retained; no name or DOB."}


def _validate_edf(edf_path: Path, eeg, physical, events: dict, core) -> dict:
    import numpy as np
    import pyedflib
    expected_labels = [c.label for c in eeg.channels]
    rate, frames = eeg.header["sample_rate_hz"], eeg.header["logical_frame_count"]
    rows = []
    with pyedflib.EdfReader(str(edf_path)) as reader:
        if reader.getSignalLabels() != expected_labels:
            raise ValueError("Independent EDF reader found changed channel labels/order")
        if reader.getNSamples().tolist() != [frames] * len(expected_labels):
            raise ValueError("Independent EDF reader found sample loss or padding")
        if not np.allclose(reader.getSampleFrequencies(), rate, rtol=0, atol=1e-8):
            raise ValueError("Independent EDF reader found changed sample rates")
        for index, descriptor in enumerate(eeg.channels):
            is_label = descriptor.kind == 4 or descriptor.label.strip().upper() == "LABEL"
            lo, hi, quantum, expected = _adaptive_calibration(physical[:, index], is_label)
            if reader.getPhysicalMinimum(index) != lo or reader.getPhysicalMaximum(index) != hi:
                raise ValueError("Independent EDF reader found changed physical calibration headers")
            actual = reader.readSignal(index, digital=True)
            mismatches = int(np.count_nonzero(actual != expected))
            decoded = reader.readSignal(index)
            errors = decoded - physical[:, index]
            max_error = float(np.abs(errors).max())
            if mismatches or not np.isfinite(decoded).all() or max_error > quantum / 2 + 1e-8:
                raise ValueError(f"Independent calibrated-sample audit failed for {descriptor.label}")
            rows.append({"label": descriptor.label, "native_scale": descriptor.scale,
                         "native_zero_offset": descriptor.zero_offset,
                         "unit": "native LABEL units" if is_label else "uV",
                         "native_min": float(physical[:, index].min()), "native_max": float(physical[:, index].max()),
                         "native_rms": float(np.sqrt(np.mean(physical[:, index] ** 2))),
                         "maximum_absolute_quantization_error": max_error,
                         "rms_quantization_error": float(np.sqrt(np.mean(errors ** 2))),
                         "edf_quantization_step": quantum, "permitted_half_step_error": quantum / 2,
                         "digital_mismatch_count": mismatches, "clipping_count": 0})
        onsets, durations, texts = reader.readAnnotations()
        independent_annotations = sorted((round(float(t), 6), round(max(float(d), 0), 6), str(label))
                                         for t, d, label in zip(onsets, durations, texts))
        independent_mapped = sorted((round(float(t), 6), round(float(d), 6), str(label))
                                    for t, d, label in zip(onsets, durations, texts) if str(label).isdigit())
    expected_mapped = sorted((round(e["onset_ms"] / 1000, 6), round(e["duration_ms"] / 1000, 6), str(e["event_code"]))
                             for e in events["mapped_events"])
    if independent_mapped != expected_mapped:
        raise ValueError("Independent EDF annotation audit found mapped-event loss or changed timing/codes")
    expected_primary = [(round(m["onset_ms"] / 1000, 6), round(m["duration_ms"] / 1000, 6),
                         f"BAD_native_artifact_{i + 1:04d}" if m["family"] == "extended_type_0" else m["label"])
                        for i, m in enumerate(events["primary_marks"])]
    if independent_annotations != sorted(expected_primary + expected_mapped):
        raise ValueError("Independent EDF annotation audit found changed primary or artifact marks")
    # Reader diversity: the native converter parser retains lane ordinals, which
    # pyedflib intentionally combines when presenting semantic annotations.
    parsed = core.read_edf(edf_path)
    lane4 = sorted((round(a.onset_seconds, 6), round(a.duration_seconds or 0, 6), a.text)
                   for a in parsed.annotations if a.lane_index == 4)
    if lane4 != expected_mapped:
        raise ValueError("EDF mapped-event lane provenance changed")
    return {"status": "passed", "independent_reader": f"pyedflib {pyedflib.__version__}",
            "all_samples_checked": True, "samples_per_channel": frames, "sample_rate_hz": rate,
            "channels": rows, "mapped_events_checked": len(expected_mapped),
            "all_semantic_annotations_checked": len(independent_annotations),
            "mapped_event_timing_code_duration_and_lane_preserved": True,
            "quantization_notice": QUANTIZATION_NOTICE}


def _write_json(path: Path, value: dict) -> None:
    path.write_text(json.dumps(value, indent=2, allow_nan=False), encoding="utf-8")


def verify_native_sample_archive(manifest: Path | dict[str, Any]) -> dict[str, Any]:
    """Independently recheck a retained NPZ against its authoritative native file."""
    item = (json.loads(Path(manifest).read_text(encoding="utf-8"))
            if isinstance(manifest, (str, Path)) else dict(manifest))
    source = Path(item["native_source_path"]).resolve()
    archive_path = Path(item["native_sample_archive_path"]).resolve()
    core, _service = _converter()
    eeg = core.read_legacy_eeg(source)
    with source.open("rb") as stream:
        source_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    with archive_path.open("rb") as stream:
        archive_sha = hashlib.file_digest(stream, "sha256").hexdigest()
    with np.load(archive_path, allow_pickle=False) as archive:
        checks = {
            "source_sha256": source_sha == item.get("native_source_sha256")
                             == str(archive["source_sha256"].item()),
            "archive_sha256": archive_sha == item.get("native_sample_archive_sha256"),
            "digital_samples": np.array_equal(archive["digital_samples"], _digital_matrix(eeg)),
            "native_scales": np.array_equal(
                archive["native_scales"], np.array([channel.scale for channel in eeg.channels])
            ),
            "native_zero_offsets": np.array_equal(
                archive["native_zero_offsets"], np.array([channel.zero_offset for channel in eeg.channels])
            ),
            "channel_labels": archive["channel_labels"].tolist()
                              == [channel.label for channel in eeg.channels],
            "sample_rate_hz": float(archive["sample_rate_hz"].item())
                              == float(eeg.header["sample_rate_hz"]),
        }
    if not all(checks.values()):
        raise ValueError("Native sample archive recheck failed: "
                         + ", ".join(name for name, ok in checks.items() if not ok))
    return {"status": "passed", "condition": item.get("condition"), "source": str(source),
            "archive": str(archive_path), "source_sha256": source_sha,
            "archive_sha256": archive_sha, "checks": checks}


def _normalize_empty_mapped_event_sentinel(eeg, *, allowed: bool = False):
    """Opt-in handling of the narrow empty-index/settings sentinel contract."""
    h = eeg.header
    if (allowed and h.get("index_count") == 65535
            and h.get("event_map_count") == 0 and h.get("event_group_count") == 0
            and h.get("index_offset") == h.get("settings_offset")
            and h["index_offset"] + h["index_count"] * 8 > len(eeg.raw)):
        from dataclasses import replace
        notice = {
            "status": "unavailable_stale_empty_table_sentinel",
            "original_index_count": h["index_count"],
            "original_index_offset": h["index_offset"],
            "event_map_count": 0, "event_group_count": 0,
            "reason": "FFFF index count points to settings, exceeds file extent, and has no parallel mapped-event/group tables. No mapped events reconstructed. Primary/artifact marks retained.",
            "original_source_modified": False,
        }
        return replace(eeg, header={**h, "index_count": 0}), notice
    return eeg, None


def convert_native_recording(source: Path, output_dir: Path, *,
                             client_code: str | None = None,
                             condition: str | None = None,
                             converter_root: Path | None = None,
                             allow_empty_mapped_event_sentinel: bool = False) -> dict[str, Any]:
    """Export, independently validate, and retain privacy-safe native provenance.

    Requires a new output directory or one containing none of this recording's
    target names. An audit failure is raised; its EDF is not eligible for routing.
    """
    source, output_dir = Path(source).resolve(), Path(output_dir).resolve()
    active_converter_root = Path(converter_root or CONVERTER_ROOT)
    core, service = _converter(active_converter_root)
    eeg = core.read_legacy_eeg(source)
    # Some non-ERP legacy files use FFFF in the index-count slot while the
    # index offset actually points at settings. Never interpret that as 65,535
    # events, and never relax checks for a file containing actual mapped events.
    eeg, mapped_event_notice = _normalize_empty_mapped_event_sentinel(
        eeg, allowed=allow_empty_mapped_event_sentinel)
    physical = _physical_matrix(eeg)
    demographics = _profile_metadata(eeg.raw, source.name, eeg.header["profile_offset"])
    match = re.fullmatch(r"(?P<client>.+)_(?P<date>20\d{6})_(?P<condition>EO|EC|ERP)", source.stem, re.I)
    if (client_code is None) != (condition is None):
        raise ValueError("An explicit intake requires both client_code and condition")
    if client_code is not None:
        if not re.fullmatch(r"[A-Za-z0-9_-]{1,64}", client_code):
            raise ValueError("Explicit client code must be a simple deidentified code")
        if str(condition).upper() not in {"EO", "EC", "ERP", "SLEEP", "UNKNOWN"}:
            raise ValueError("Unsupported explicit recording condition")
    elif not match:
        raise ValueError("Native intake requires an explicit client_YYYYMMDD_EO/EC/ERP source name")
    edf_path = output_dir / f"{source.stem}.edf"
    manifest_path = output_dir / f"{source.stem}.intake.json"
    event_path = output_dir / f"{source.stem}.native_events.json"
    audit_path = output_dir / f"{source.stem}.conversion_audit.json"
    samples_path = output_dir / f"{source.stem}.native_samples.npz"
    if any(p.exists() for p in (edf_path, manifest_path, event_path, audit_path, samples_path)):
        raise FileExistsError("Native intake targets already exist; use a fresh output folder")
    output_dir.mkdir(parents=True, exist_ok=True)
    events = _event_inventory(eeg, core)
    if mapped_event_notice:
        events["mapped_event_table_notice"] = mapped_event_notice
    source_sha = hashlib.sha256(eeg.raw).hexdigest()
    events["native_source_sha256"] = source_sha
    _write_json(event_path, events)
    montage_block = eeg.raw[eeg.header["montage_offset"]:eeg.header["montage_offset"] + 512]
    montage_match = re.search(rb"Database version \d+ \(Av\)", montage_block)
    from .reference_evidence import build_reference_evidence
    saved_display_montage = montage_match.group().decode() if montage_match else None
    descriptor_reference_codes = sorted({c.reference_code for c in eeg.channels})
    reference_evidence = build_reference_evidence(
        [c.label for c in eeg.channels],
        saved_display_montage_label=saved_display_montage,
        descriptor_reference_codes=descriptor_reference_codes,
    )
    result = {"schema": "eeg_paradox_native_intake_v1", "status": "validating", **demographics,
              "client_code": client_code if client_code is not None else match.group("client"),
              "condition": str(condition).upper() if condition is not None else match.group("condition").upper(),
              "condition_basis": "explicit_operator_context" if condition is not None else "explicit_source_filename",
              "native_source_path": str(source), "native_source_sha256": source_sha,
              "edf_path": str(edf_path), "audit_path": str(audit_path),
              "event_inventory_path": str(event_path), "manifest_path": str(manifest_path),
              "mapped_event_table_notice": mapped_event_notice,
              "event_inventory_sha256": hashlib.sha256(event_path.read_bytes()).hexdigest(),
              "native_sample_archive_path": str(samples_path),
              "reference": "auto", "reference_status": "unknown_recording_reference",
              "saved_display_montage_label": saved_display_montage,
              "reference_notice": "Saved display montage and numeric descriptor reference codes do not establish the reference of the stored signal samples.",
              "native_descriptor_reference_codes": descriptor_reference_codes,
              "reference_evidence": reference_evidence,
              "sample_rate_hz": eeg.header["sample_rate_hz"], "samples_per_channel": eeg.physical_frame_count,
              "duration_seconds": eeg.physical_frame_count / eeg.header["sample_rate_hz"],
              "native_bits_per_sample": eeg.header["bits_per_sample"], "channels": [c.label for c in eeg.channels],
              "quantization_notice": QUANTIZATION_NOTICE,
              "converter_root": str(active_converter_root.resolve()),
              "converter_core_sha256": hashlib.sha256(Path(core.__file__).read_bytes()).hexdigest(),
              "derived_edf_date_notice": "Converter EDF date is a fixed de-identification placeholder, not the recording date; use this verified native manifest."}
    try:
        import numpy as np
        np.savez_compressed(samples_path, digital_samples=_digital_matrix(eeg),
                            native_scales=np.array([c.scale for c in eeg.channels]),
                            native_zero_offsets=np.array([c.zero_offset for c in eeg.channels]),
                            channel_labels=np.array([c.label for c in eeg.channels]),
                            sample_rate_hz=np.array(eeg.header["sample_rate_hz"]),
                            source_sha256=np.array(source_sha))
        with np.load(samples_path, allow_pickle=False) as archive:
            if (not np.array_equal(archive["digital_samples"], _digital_matrix(eeg))
                    or not np.array_equal(archive["native_scales"], np.array([c.scale for c in eeg.channels]))
                    or not np.array_equal(archive["native_zero_offsets"], np.array([c.zero_offset for c in eeg.channels]))
                    or archive["channel_labels"].tolist() != [c.label for c in eeg.channels]
                    or int(archive["sample_rate_hz"]) != eeg.header["sample_rate_hz"]
                    or str(archive["source_sha256"]) != source_sha):
                raise ValueError("Native sample archive did not preserve exact integers and calibration")
        conversion = _write_range_preserving_edf(edf_path, eeg, physical, core)
        audit = _validate_edf(edf_path, eeg, physical, events, core)
        if hashlib.sha256(source.read_bytes()).hexdigest() != source_sha:
            raise ValueError("Source changed during conversion")
        _write_json(audit_path, audit)
        result.update(status="validated", conversion_report=conversion,
                      native_sample_archive_verified_exact=True,
                      native_sample_archive_sha256=hashlib.sha256(samples_path.read_bytes()).hexdigest(),
                      edf_sha256=hashlib.sha256(edf_path.read_bytes()).hexdigest())
    except Exception as exc:
        result.update(status="failed_validation", failure=str(exc))
        _write_json(manifest_path, result)
        raise
    _write_json(manifest_path, result)
    return result
