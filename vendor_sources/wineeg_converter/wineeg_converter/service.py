"""High-level, atomic conversion service.

This layer deliberately writes a new container from parsed structures. It
never copies a header from another recording, concatenates a template, or
splices sample bytes into an existing file.
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
from pathlib import Path
from typing import Any, Callable, Iterable, Sequence

from . import core


class ConversionError(RuntimeError):
    """A user-facing conversion or validation failure."""


EVENT_POLICIES = {"redact", "preserve", "drop"}
SIDECAR_FORMAT = "wineeg_edf_to_legacy_conversion"
BATCH_DIRECTIONS = {"auto", "edf-to-eeg", "eeg-to-edf"}


def detect_format(path: Path | str) -> str:
    source = Path(path)
    if not source.is_file():
        raise ConversionError(f"Input file does not exist: {source}")
    with source.open("rb") as handle:
        prefix = handle.read(8)
    if len(prefix) >= 2 and int.from_bytes(prefix[:2], "little") == core.LEGACY_MAGIC:
        return "wineeg-eeg"
    if len(prefix) == 8 and prefix[:1] == b"0":
        return "edf"
    if len(prefix) == 8 and prefix[:1] == b"\xff":
        raise ConversionError("BDF is not EDF and is not supported by this exact 16-bit EDF converter")
    raise ConversionError("Input is neither an EDF/EDF+ file nor a WinEEG 0x0f0e EEG file")


def _prepare(source: Path, destination: Path, suffix: str, overwrite: bool) -> None:
    if not source.is_file():
        raise ConversionError(f"Input file does not exist: {source}")
    source_resolved = source.resolve()
    destination_resolved = destination.resolve()
    if source_resolved == destination_resolved:
        raise ConversionError("Input and output paths must be different")
    if destination.suffix.lower() != suffix:
        raise ConversionError(f"Output must use the {suffix} extension")
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists() and not overwrite:
        raise ConversionError(f"Output already exists; pass --overwrite to replace it: {destination}")


def _temporary_destination(destination: Path) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=f".{destination.stem}.", suffix=destination.suffix, dir=destination.parent
    )
    os.close(descriptor)
    temporary = Path(name)
    temporary.unlink()
    return temporary


def _commit_temporary(temporary: Path, destination: Path) -> None:
    os.replace(temporary, destination)


def _write_json_atomic(path: Path, value: dict[str, Any], overwrite: bool = True) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists() and not overwrite:
        raise ConversionError(f"Report already exists: {path}")
    temporary = _temporary_destination(path)
    try:
        temporary.write_text(json.dumps(value, indent=2), encoding="utf-8")
        _commit_temporary(temporary, path)
    finally:
        if temporary.exists():
            temporary.unlink()


def _report_path(destination: Path, requested: Path | None, write_report: bool) -> Path | None:
    if not write_report:
        return None
    return requested or destination.with_suffix(destination.suffix + ".conversion.json")


def _read_json_object(path: Path) -> dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError) as exc:
        raise ConversionError(f"Cannot read conversion sidecar JSON: {exc}") from exc
    if not isinstance(value, dict):
        raise ConversionError("Conversion sidecar must contain one JSON object")
    return value


def _validate_conversion_sidecar(
    value: dict[str, Any], eeg: core.LegacyEEG
) -> dict[str, Any]:
    if value.get("format") != SIDECAR_FORMAT:
        raise ConversionError("JSON is not an EDF-to-WinEEG conversion sidecar")

    exact_fields = {
        "converted_channel_count": eeg.header["channel_count"],
        "target_sample_rate_hz": eeg.header["sample_rate_hz"],
        "target_frame_count": eeg.header["logical_frame_count"],
    }
    for field, expected in exact_fields.items():
        try:
            actual = int(value[field])
        except (KeyError, TypeError, ValueError) as exc:
            raise ConversionError(f"Conversion sidecar has no valid {field}") from exc
        if actual != expected:
            raise ConversionError(
                f"Conversion sidecar {field} does not match the selected EEG container"
            )

    labels = value.get("full_channel_labels")
    if not isinstance(labels, list) or len(labels) != eeg.header["channel_count"]:
        raise ConversionError("Conversion sidecar channel-label count does not match the EEG")
    if any(not isinstance(label, str) for label in labels):
        raise ConversionError("Conversion sidecar channel labels must be strings")

    events = value.get("events")
    if not isinstance(events, list) or any(not isinstance(event, dict) for event in events):
        raise ConversionError("Conversion sidecar events must be a JSON object array")
    primary_events = [event for event in events if event.get("family") == "primary"]
    if len(primary_events) != eeg.header["primary_mark_count"]:
        raise ConversionError("Conversion sidecar primary-event count does not match the EEG")
    primary_marks = [
        mark for mark in core.parse_legacy_marks(eeg, include_text=False)
        if mark.family == "primary"
    ]
    for event, mark in zip(primary_events, primary_marks):
        try:
            onset_ms = int(event["onset_ms"])
            duration_ms = int(event.get("duration_ms", 0))
        except (KeyError, TypeError, ValueError) as exc:
            raise ConversionError("Conversion sidecar contains an invalid primary event") from exc
        if onset_ms != mark.onset_ms or duration_ms < 0 or not isinstance(event.get("label"), str):
            raise ConversionError("Conversion sidecar primary events do not match the EEG")
    expected_digest = value.get("target_eeg_sha256")
    if expected_digest is not None:
        actual_digest = hashlib.sha256(eeg.raw).hexdigest()
        if not isinstance(expected_digest, str) or expected_digest.lower() != actual_digest:
            raise ConversionError("Conversion sidecar SHA-256 does not match the selected EEG")
    return value


def discover_conversion_sidecar(
    source: Path | str,
    eeg: core.LegacyEEG,
    *,
    requested: Path | str | None = None,
) -> tuple[dict[str, Any] | None, str]:
    """Locate and validate converter metadata without guessing from arbitrary JSON."""
    source_path = Path(source)
    if requested is not None:
        requested_path = Path(requested)
        if not requested_path.is_file():
            raise ConversionError(f"Requested conversion sidecar does not exist: {requested_path}")
        return _validate_conversion_sidecar(_read_json_object(requested_path), eeg), "explicit"

    adjacent = source_path.with_suffix(source_path.suffix + ".conversion.json")
    if adjacent.is_file():
        return _validate_conversion_sidecar(_read_json_object(adjacent), eeg), "adjacent_report"

    alternate = source_path.with_suffix(".json")
    if alternate.is_file():
        try:
            candidate = json.loads(alternate.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError):
            candidate = None
        if isinstance(candidate, dict) and candidate.get("format") == SIDECAR_FORMAT:
            return _validate_conversion_sidecar(candidate, eeg), "adjacent_json"
    return None, "none"


def convert_edf_to_eeg(
    source: Path | str,
    destination: Path | str,
    *,
    event_policy: str = "redact",
    report: Path | str | None = None,
    write_report: bool = True,
    overwrite: bool = False,
) -> dict[str, Any]:
    source_path = Path(source)
    destination_path = Path(destination)
    if event_policy not in EVENT_POLICIES:
        raise ConversionError(f"Unknown event policy: {event_policy}")
    if detect_format(source_path) != "edf":
        raise ConversionError("EDF-to-EEG conversion requires an EDF/EDF+ input")
    _prepare(source_path, destination_path, ".eeg", overwrite)
    temporary = _temporary_destination(destination_path)
    try:
        result = core.edf_to_legacy(
            source_path.resolve(), temporary, event_policy=event_policy, sidecar=None
        )
        result["target_eeg_sha256"] = hashlib.sha256(temporary.read_bytes()).hexdigest()
        check = core.read_legacy_eeg(temporary)
        result["post_write_validation"] = {
            "magic": f"0x{check.header['magic']:04x}",
            "channel_count": check.header["channel_count"],
            "sample_rate_hz": check.header["sample_rate_hz"],
            "logical_frames": check.header["logical_frame_count"],
            "physical_frames": check.physical_frame_count,
            "primary_mark_count": check.header["primary_mark_count"],
            "mapped_event_count": check.header["event_map_count"],
            "event_group_count": check.header["event_group_count"],
            "identity_profile_zero_filled": not any(check.raw[0x80:0x280]),
        }
        _commit_temporary(temporary, destination_path)
    except Exception as exc:
        raise ConversionError(f"EDF-to-EEG conversion failed: {exc}") from exc
    finally:
        if temporary.exists():
            temporary.unlink()
    report_path = _report_path(
        destination_path, Path(report) if report is not None else None, write_report
    )
    if report_path is not None:
        _write_json_atomic(report_path, result)
    return result


def convert_eeg_to_edf(
    source: Path | str,
    destination: Path | str,
    *,
    event_policy: str = "redact",
    physical_frames: bool = False,
    report: Path | str | None = None,
    write_report: bool = True,
    overwrite: bool = False,
    source_sidecar: Path | str | None = None,
    use_sidecar: bool = True,
) -> dict[str, Any]:
    source_path = Path(source)
    destination_path = Path(destination)
    if event_policy not in EVENT_POLICIES:
        raise ConversionError(f"Unknown event policy: {event_policy}")
    if detect_format(source_path) != "wineeg-eeg":
        raise ConversionError("EEG-to-EDF conversion requires a proprietary WinEEG 0x0f0e input")
    if source_sidecar is not None and not use_sidecar:
        raise ConversionError("An explicit sidecar cannot be combined with sidecar restoration disabled")
    try:
        eeg = core.read_legacy_eeg(source_path)
        conversion_sidecar: dict[str, Any] | None = None
        sidecar_mode = "disabled"
        if use_sidecar:
            conversion_sidecar, sidecar_mode = discover_conversion_sidecar(
                source_path, eeg, requested=source_sidecar
            )
    except ConversionError:
        raise
    except Exception as exc:
        raise ConversionError(f"EEG-to-EDF preflight failed: {exc}") from exc
    _prepare(source_path, destination_path, ".edf", overwrite)
    temporary = _temporary_destination(destination_path)
    try:
        result = core.legacy_to_edf(
            source_path.resolve(),
            temporary,
            event_policy=event_policy,
            physical_frames=physical_frames,
            conversion_sidecar=conversion_sidecar,
        )
        result["sidecar_mode"] = sidecar_mode
        check = core.read_edf(temporary)
        data_signals = [
            signal
            for signal in check.signals
            if signal.label.strip().lower() != core.EDF_ANNOTATION_LABEL.lower()
        ]
        lane_counts = {
            str(lane): sum(annotation.lane_index == lane for annotation in check.annotations)
            for lane in range(
                sum(
                    signal.label.strip().lower() == core.EDF_ANNOTATION_LABEL.lower()
                    for signal in check.signals
                )
            )
        }
        result["post_write_validation"] = {
            "data_signal_count": len(data_signals),
            "annotation_count": len(check.annotations),
            "annotation_lane_counts": lane_counts,
            "data_record_count": check.data_record_count,
            "data_record_duration": check.data_record_duration,
            "deidentified": check.patient_id == "X X X X",
        }
        _commit_temporary(temporary, destination_path)
    except Exception as exc:
        raise ConversionError(f"EEG-to-EDF conversion failed: {exc}") from exc
    finally:
        if temporary.exists():
            temporary.unlink()
    report_path = _report_path(
        destination_path, Path(report) if report is not None else None, write_report
    )
    if report_path is not None:
        _write_json_atomic(report_path, result)
    return result


def convert(
    source: Path | str,
    destination: Path | str,
    **options: Any,
) -> dict[str, Any]:
    detected = detect_format(source)
    if detected == "edf":
        eeg_only = {"physical_frames", "source_sidecar", "use_sidecar"}
        allowed = {key: value for key, value in options.items() if key not in eeg_only}
        return convert_edf_to_eeg(source, destination, **allowed)
    return convert_eeg_to_edf(source, destination, **options)


def _expand_batch_inputs(
    inputs: Sequence[Path | str], *, recursive: bool
) -> tuple[list[Path], int]:
    sources: list[Path] = []
    json_companions = 0
    for value in inputs:
        path = Path(value)
        if path.is_dir():
            iterator: Iterable[Path] = path.rglob("*") if recursive else path.glob("*")
            sources.extend(
                item for item in sorted(iterator, key=lambda item: str(item).lower())
                if item.is_file() and item.suffix.lower() in {".edf", ".eeg"}
            )
        elif path.is_file() and path.suffix.lower() == ".json":
            json_companions += 1
        else:
            sources.append(path)

    unique: list[Path] = []
    seen: set[str] = set()
    for source in sources:
        key = str(source.resolve()).casefold()
        if key not in seen:
            seen.add(key)
            unique.append(source)
    return unique, json_companions


def _allocate_batch_destination(
    source: Path,
    output_dir: Path,
    suffix: str,
    reserved: set[str],
    source_paths: set[str],
) -> Path:
    ordinal = 1
    while True:
        stem_suffix = "" if ordinal == 1 else f"__{ordinal}"
        candidate = output_dir / f"{source.stem}{stem_suffix}{suffix}"
        key = str(candidate.resolve()).casefold()
        if key not in reserved and key not in source_paths:
            reserved.add(key)
            return candidate
        ordinal += 1


def _safe_batch_error(exc: Exception, source: Path, destination: Path | None) -> str:
    message = str(exc)
    replacements = [str(source), str(source.resolve()), source.name]
    if destination is not None:
        replacements.extend([str(destination), str(destination.resolve()), destination.name])
    for value in sorted(set(replacements), key=len, reverse=True):
        if value:
            message = message.replace(value, "<path>")
    return message


def convert_batch(
    inputs: Sequence[Path | str],
    output_dir: Path | str,
    *,
    direction: str = "auto",
    event_policy: str = "redact",
    physical_frames: bool = False,
    write_reports: bool = True,
    overwrite: bool = False,
    use_sidecars: bool = True,
    recursive: bool = False,
    report: Path | str | None = None,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    """Convert a mixed file/folder queue with isolated, atomic per-file commits."""
    if direction not in BATCH_DIRECTIONS:
        raise ConversionError(f"Unknown batch direction: {direction}")
    if event_policy not in EVENT_POLICIES:
        raise ConversionError(f"Unknown event policy: {event_policy}")
    sources, json_companions = _expand_batch_inputs(inputs, recursive=recursive)
    if not sources:
        raise ConversionError("Batch contains no EDF or WinEEG EEG inputs")

    destination_root = Path(output_dir)
    destination_root.mkdir(parents=True, exist_ok=True)
    source_paths = {str(source.resolve()).casefold() for source in sources}
    reserved: set[str] = set()
    items: list[dict[str, Any]] = []
    total = len(sources)

    for index, source in enumerate(sources, 1):
        source_id = f"input_{index:04d}"
        destination: Path | None = None
        detected: str | None = None
        item_direction: str | None = None
        if progress is not None:
            progress({"phase": "starting", "index": index, "total": total, "name": source.name})
        try:
            detected = detect_format(source)
            detected_direction = "edf-to-eeg" if detected == "edf" else "eeg-to-edf"
            if direction != "auto" and direction != detected_direction:
                raise ConversionError("Input format does not match the selected batch direction")
            item_direction = detected_direction
            suffix = ".eeg" if item_direction == "edf-to-eeg" else ".edf"
            destination = _allocate_batch_destination(
                source, destination_root, suffix, reserved, source_paths
            )
            common = {
                "event_policy": event_policy,
                "write_report": write_reports,
                "overwrite": overwrite,
            }
            if item_direction == "edf-to-eeg":
                converted = convert_edf_to_eeg(source, destination, **common)
            else:
                converted = convert_eeg_to_edf(
                    source,
                    destination,
                    physical_frames=physical_frames,
                    use_sidecar=use_sidecars,
                    **common,
                )
            items.append({
                "source_id": source_id,
                "status": "converted",
                "input_format": detected,
                "direction": item_direction,
                "output_extension": suffix,
                "sidecar_used": bool(converted.get("sidecar_used", False)),
                "converted_channels": converted.get(
                    "converted_channel_count", converted.get("channel_count")
                ),
                "converted_frames": converted.get(
                    "target_frame_count", converted.get("converted_frames")
                ),
            })
            if progress is not None:
                progress({
                    "phase": "converted", "index": index, "total": total,
                    "name": source.name, "output_name": destination.name,
                })
        except Exception as exc:
            items.append({
                "source_id": source_id,
                "status": "failed",
                "input_format": detected or "unrecognized",
                "direction": item_direction or direction,
                "error": _safe_batch_error(exc, source, destination),
            })
            if progress is not None:
                progress({
                    "phase": "failed", "index": index, "total": total,
                    "name": source.name, "error": str(exc),
                })

    success_count = sum(item["status"] == "converted" for item in items)
    failed_count = total - success_count
    manifest = {
        "format": "wineeg_batch_conversion",
        "identity_policy": "source paths and filenames are omitted from this aggregate report",
        "event_policy": event_policy,
        "direction": direction,
        "recursive": recursive,
        "sidecar_restoration_enabled": use_sidecars,
        "selected_json_companion_count": json_companions,
        "input_count": total,
        "success_count": success_count,
        "failed_count": failed_count,
        "items": items,
    }
    if write_reports:
        report_path = Path(report) if report is not None else destination_root / "batch_conversion.json"
        _write_json_atomic(report_path, manifest)
    return manifest


def inspect_safe(path: Path | str, include_event_text: bool = False) -> dict[str, Any]:
    try:
        return core.inspect_file(Path(path).resolve(), include_event_text=include_event_text)
    except Exception as exc:
        raise ConversionError(f"Inspection failed: {exc}") from exc


def validate_roundtrip(
    source_edf: Path | str,
    intermediate_eeg: Path | str,
    output_edf: Path | str,
    *,
    report: Path | str | None = None,
) -> dict[str, Any]:
    try:
        return core.validate_reference_roundtrip(
            Path(source_edf).resolve(),
            Path(intermediate_eeg).resolve(),
            Path(output_edf).resolve(),
            report=Path(report).resolve() if report is not None else None,
        )
    except Exception as exc:
        raise ConversionError(f"Round-trip validation failed: {exc}") from exc
