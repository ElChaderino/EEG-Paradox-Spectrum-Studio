#!/usr/bin/env python
"""Inspect and convert WinEEG legacy ``.eeg`` files and EDF/EDF+ files.

The implementation is intentionally conservative about the proprietary
container.  Fields are named only where the bundled decompiler evidence
proves their use.  Identity-bearing legacy profile bytes are never decoded or
copied to EDF; generated legacy profiles are zero-filled.

No WineEG executable, driver, device, or IOCTL is used by this tool.
"""

from __future__ import annotations

import argparse
import csv
import json
import math
import re
import struct
from dataclasses import asdict, dataclass, replace
from pathlib import Path
from typing import Any, Iterable, Sequence


LEGACY_MAGIC = 0x0F0E
LEGACY_HEADER_SIZE = 0x80
LEGACY_PROFILE_OFFSET = 0x80
LEGACY_PROFILE_SIZE = 0x200
LEGACY_MONTAGE_OFFSET = 0x280
LEGACY_MONTAGE_SIZE = 0x200
LEGACY_CHANNEL_TABLE_OFFSET = 0x480
LEGACY_CHANNEL_DESCRIPTOR_SIZE = 0x40
LEGACY_SETTINGS_SIZE = 0x840
LEGACY_OPTIONAL_BLOCK_SIZE = 0x8000
LEGACY_FLOAT_TABLE_SIZE = 0x40000
LEGACY_PRIMARY_MARK_SIZE = 0x20
LEGACY_EXTENDED_MARK_DISK_SIZE = 0x20
LEGACY_VIEW_DESCRIPTOR_SIZE = 0x40
LEGACY_INDEX_RECORD_SIZE = 8
LEGACY_EVENT_MAP_SIZE = 8
LEGACY_EVENT_GROUP_SIZE = 8
LEGACY_AUX_RECORD_SIZE = 0x10

EDF_FIXED_HEADER_SIZE = 256
EDF_SIGNAL_HEADER_SIZE = 256
EDF_ANNOTATION_LABEL = "EDF Annotations"
NATIVE_EDF_ANNOTATION_LANES: tuple[str, ...] = (
    "timekeeping",
    "primary_marks",
    "extended_marks",
    "artifact_marks",
    "mapped_events",
)

ASCII_SAFE = re.compile(r"[^ -~]")


HEADER_FIELDS: tuple[tuple[str, int, str, str], ...] = (
    ("magic", 0x00, "H", "exact"),
    ("channel_count", 0x02, "H", "exact"),
    ("sample_rate_hz", 0x04, "H", "exact"),
    ("primary_mark_count", 0x06, "H", "exact"),
    ("text_blob_size", 0x08, "H", "exact"),
    ("sample_width_bytes", 0x0A, "H", "exact"),
    ("bits_per_sample", 0x0C, "H", "exact"),
    ("extended_mark_count", 0x0E, "H", "exact"),
    ("logical_frame_count", 0x10, "I", "exact"),
    ("profile_offset", 0x14, "I", "exact"),
    ("channel_table_offset", 0x18, "I", "exact"),
    ("sample_data_offset", 0x1C, "I", "exact"),
    ("primary_marks_offset", 0x20, "I", "exact"),
    ("extended_marks_offset", 0x24, "I", "exact"),
    ("text_blob_offset", 0x28, "I", "exact"),
    ("word_0x2c", 0x2C, "H", "unresolved"),
    ("word_0x2e", 0x2E, "H", "unresolved"),
    ("word_0x30", 0x30, "H", "unresolved"),
    ("index_count", 0x32, "H", "exact"),
    ("index_offset", 0x34, "I", "exact"),
    ("text_line_count", 0x38, "H", "exact"),
    ("montage_offset", 0x3A, "I", "exact"),
    ("format_flags", 0x3E, "H", "observed"),
    ("alternate_block_count", 0x48, "H", "exact_structure"),
    ("alternate_block_offset", 0x4A, "I", "exact_structure"),
    ("alternate_block_frames", 0x4E, "H", "exact_structure"),
    ("event_map_count", 0x50, "H", "exact_structure"),
    ("event_group_count", 0x52, "H", "exact_structure"),
    ("event_map_offset", 0x54, "I", "exact_structure"),
    ("event_group_offset", 0x58, "I", "exact_structure"),
    ("settings_offset", 0x5C, "I", "exact"),
    ("aux_record_offset", 0x60, "I", "exact_structure"),
    ("aux_record_count", 0x64, "H", "exact_structure"),
    ("float_table_present", 0x66, "H", "exact_structure"),
    ("float_table_offset", 0x68, "I", "exact_structure"),
    ("view_descriptor_count", 0x6C, "H", "exact_structure"),
    ("view_descriptor_offset", 0x6E, "I", "exact_structure"),
    ("extra_descriptor_count", 0x72, "H", "exact_structure"),
    ("extra_descriptor_offset", 0x74, "I", "exact_structure"),
    ("optional_block_offset", 0x78, "I", "exact_structure"),
    ("optional_block_size", 0x7C, "H", "exact_structure"),
)


STANDARD_CHANNELS: dict[str, tuple[int, int, int, int]] = {
    # label: (WinEEG code, kind, x, y); coordinates are the bundled 10-20 map.
    "FP1": (0, 0, 72, 108),
    "FP2": (1, 0, 72, 72),
    "F7": (2, 0, 72, 144),
    "F3": (3, 0, 48, 135),
    "FZ": (4, 0, 36, 90),
    "F4": (5, 0, 48, 45),
    "F8": (6, 0, 72, 36),
    "T3": (7, 0, 72, 180),
    "T7": (7, 0, 72, 180),
    "C3": (8, 0, 36, 180),
    "CZ": (9, 0, 0, 0),
    "C4": (10, 0, 36, 0),
    "T4": (11, 0, 72, 0),
    "T8": (11, 0, 72, 0),
    "T5": (12, 0, 72, 216),
    "P7": (12, 0, 72, 216),
    "P3": (13, 0, 48, 225),
    "PZ": (14, 0, 36, 270),
    "P4": (15, 0, 48, 315),
    "T6": (16, 0, 72, 324),
    "P8": (16, 0, 72, 324),
    "O1": (17, 0, 72, 252),
    "O2": (18, 0, 72, 288),
    "CB": (21, 0, 90, 270),
    "A1": (36, 0, 105, 195),
    "A2": (37, 0, 105, 345),
    "BIO1-2": (40, 1, 137, 230),
    "BIO3-4": (41, 1, 137, 310),
    "ECG": (42, 1, 105, 270),
    "VSYN": (78, 5, 0, 0),
    "ASYN": (79, 5, 0, 0),
    "AA": (31, 2, 90, 306),
    "LABEL": (81, 4, 0, 0),
}


@dataclass(frozen=True)
class LegacyChannel:
    index: int
    label: str
    source_code: int
    reference_code: int
    field_0x0c: int
    field_0x0e: int
    field_0x10: int
    x: int
    y: int
    scale: float
    kind: int
    correction: float
    zero_offset: float


@dataclass(frozen=True)
class LegacyMark:
    onset_ms: int
    label: str
    duration_ms: int = 0
    family: str = "primary"


@dataclass(frozen=True)
class LegacyEventGroupEntry:
    relative_ms: int
    stimulus_code: int
    reserved: int = 0


@dataclass(frozen=True)
class LegacyEventGroup:
    entries: tuple[LegacyEventGroupEntry, ...]
    duration_ms: int


@dataclass(frozen=True)
class LegacyMappedEvent:
    onset_ms: int
    group_index: int
    event_code: int
    lane_code: int = 4
    flags: int = 0


@dataclass
class LegacyEEG:
    header: dict[str, int]
    channels: list[LegacyChannel]
    raw: bytes
    physical_frame_count: int
    trailing_sample_bytes: int


@dataclass(frozen=True)
class EDFSignal:
    label: str
    transducer: str
    physical_dimension: str
    physical_min: float
    physical_max: float
    digital_min: int
    digital_max: int
    prefilter: str
    samples_per_record: int
    reserved: str = ""


@dataclass(frozen=True)
class EDFAnnotation:
    onset_seconds: float
    duration_seconds: float | None
    text: str
    lane_index: int | None = None


@dataclass
class EDFFile:
    version: str
    patient_id: str
    recording_id: str
    start_date: str
    start_time: str
    reserved: str
    data_record_count: int
    data_record_duration: float
    signals: list[EDFSignal]
    digital_samples: list[list[int]]
    annotations: list[EDFAnnotation]


def _unpack(raw: bytes, offset: int, fmt: str) -> int:
    return int(struct.unpack_from("<" + fmt, raw, offset)[0])


def _pack_into(buffer: bytearray, offset: int, fmt: str, value: int | float) -> None:
    struct.pack_into("<" + fmt, buffer, offset, value)


def _decode_fixed(raw: bytes, *, encoding: str = "cp1252") -> str:
    value = raw.split(b"\0", 1)[0].rstrip(b" ")
    return value.decode(encoding, errors="replace")


def _safe_ascii(value: str, width: int, fallback: str = "") -> bytes:
    clean = ASCII_SAFE.sub("?", value).strip()
    if not clean:
        clean = fallback
    return clean.encode("ascii", errors="replace")[:width].ljust(width, b" ")


def _legacy_label_bytes(value: str) -> bytes:
    clean = ASCII_SAFE.sub("?", value).strip() or "CH"
    return clean.encode("ascii", errors="replace")[:7].ljust(8, b"\0")


def parse_legacy_header(raw: bytes) -> dict[str, int]:
    if len(raw) < LEGACY_HEADER_SIZE:
        raise ValueError("legacy EEG is shorter than the 128-byte header")
    values = {name: _unpack(raw, offset, fmt) for name, offset, fmt, _ in HEADER_FIELDS}
    if values["magic"] != LEGACY_MAGIC:
        raise ValueError(f"unexpected legacy EEG magic 0x{values['magic']:04x}")
    if not 1 <= values["channel_count"] <= 256:
        raise ValueError("legacy EEG channel count is outside 1..256")
    if not 1 <= values["sample_rate_hz"] <= 20000:
        raise ValueError("legacy EEG sample rate is outside 1..20000 Hz")
    if values["sample_width_bytes"] not in {2, 3}:
        raise ValueError("only the recovered 16-bit and 24-bit legacy sample widths are supported")
    return values


def parse_legacy_channels(raw: bytes, header: dict[str, int]) -> list[LegacyChannel]:
    start = header["channel_table_offset"]
    end = start + header["channel_count"] * LEGACY_CHANNEL_DESCRIPTOR_SIZE
    if start < LEGACY_HEADER_SIZE or end > len(raw):
        raise ValueError("legacy EEG channel table is outside the file")
    channels: list[LegacyChannel] = []
    for index in range(header["channel_count"]):
        offset = start + index * LEGACY_CHANNEL_DESCRIPTOR_SIZE
        channels.append(
            LegacyChannel(
                index=index,
                label=_decode_fixed(raw[offset : offset + 8], encoding="ascii") or f"CH{index + 1}",
                source_code=_unpack(raw, offset + 0x08, "h"),
                reference_code=_unpack(raw, offset + 0x0A, "h"),
                field_0x0c=_unpack(raw, offset + 0x0C, "h"),
                field_0x0e=_unpack(raw, offset + 0x0E, "h"),
                field_0x10=_unpack(raw, offset + 0x10, "h"),
                x=_unpack(raw, offset + 0x14, "h"),
                y=_unpack(raw, offset + 0x16, "h"),
                scale=float(struct.unpack_from("<f", raw, offset + 0x18)[0]),
                kind=_unpack(raw, offset + 0x1C, "h"),
                correction=float(struct.unpack_from("<f", raw, offset + 0x26)[0]),
                zero_offset=float(struct.unpack_from("<f", raw, offset + 0x2A)[0]),
            )
        )
    return channels


def parse_legacy_eeg(raw: bytes) -> LegacyEEG:
    header = parse_legacy_header(raw)
    channels = parse_legacy_channels(raw, header)
    start = header["sample_data_offset"]
    end = header["primary_marks_offset"]
    if not (0 <= start <= end <= len(raw)):
        raise ValueError("legacy EEG sample interval is not inside the file")
    frame_bytes = header["channel_count"] * header["sample_width_bytes"]
    sample_bytes = end - start
    return LegacyEEG(
        header=header,
        channels=channels,
        raw=raw,
        physical_frame_count=sample_bytes // frame_bytes,
        trailing_sample_bytes=sample_bytes % frame_bytes,
    )


def read_legacy_eeg(path: Path) -> LegacyEEG:
    return parse_legacy_eeg(path.read_bytes())


def _legacy_digital_frames(eeg: LegacyEEG) -> Iterable[tuple[int, ...]]:
    header = eeg.header
    start = header["sample_data_offset"]
    channels = header["channel_count"]
    width = header["sample_width_bytes"]
    if width == 2:
        fmt = "<" + "h" * channels
        stride = channels * 2
        for offset in range(start, start + eeg.physical_frame_count * stride, stride):
            yield struct.unpack_from(fmt, eeg.raw, offset)
        return
    stride = channels * 3
    for offset in range(start, start + eeg.physical_frame_count * stride, stride):
        frame = []
        for channel in range(channels):
            pos = offset + channel * 3
            value = int.from_bytes(eeg.raw[pos : pos + 3], "little", signed=True)
            frame.append(value)
        yield tuple(frame)


def legacy_digital_channels(eeg: LegacyEEG, *, logical_only: bool = True) -> list[list[int]]:
    frame_count = eeg.physical_frame_count
    if logical_only:
        frame_count = min(frame_count, eeg.header["logical_frame_count"])
    result = [[] for _ in eeg.channels]
    for frame_index, frame in enumerate(_legacy_digital_frames(eeg)):
        if frame_index >= frame_count:
            break
        for channel, value in enumerate(frame):
            result[channel].append(value)
    return result


def legacy_physical_channels(eeg: LegacyEEG, *, logical_only: bool = True) -> list[list[float]]:
    digital = legacy_digital_channels(eeg, logical_only=logical_only)
    result: list[list[float]] = []
    for descriptor, values in zip(eeg.channels, digital):
        scale = descriptor.scale if math.isfinite(descriptor.scale) and descriptor.scale != 0 else 1.0
        zero = descriptor.zero_offset if math.isfinite(descriptor.zero_offset) else 0.0
        result.append([(value - zero) * scale for value in values])
    return result


def parse_legacy_marks(
    eeg: LegacyEEG, *, include_text: bool = True
) -> list[LegacyMark]:
    header = eeg.header
    marks: list[LegacyMark] = []
    offset = header["primary_marks_offset"]
    count = header["primary_mark_count"]
    if count and offset + count * LEGACY_PRIMARY_MARK_SIZE <= len(eeg.raw):
        for index in range(count):
            pos = offset + index * LEGACY_PRIMARY_MARK_SIZE
            onset = _unpack(eeg.raw, pos, "I")
            length = min(_unpack(eeg.raw, pos + 4, "H"), 24)
            label = (
                eeg.raw[pos + 6 : pos + 6 + length].decode("cp1252", errors="replace")
                if include_text
                else "<redacted>"
            )
            marks.append(LegacyMark(onset, label, family="primary"))

    offset = header["extended_marks_offset"]
    count = header["extended_mark_count"]
    if count and offset + count * LEGACY_EXTENDED_MARK_DISK_SIZE <= len(eeg.raw):
        for index in range(count):
            pos = offset + index * LEGACY_EXTENDED_MARK_DISK_SIZE
            kind = _unpack(eeg.raw, pos, "H")
            onset = _unpack(eeg.raw, pos + 2, "I")
            duration = _unpack(eeg.raw, pos + 6, "I") if kind == 0 else 0
            label = _decode_fixed(eeg.raw[pos + 0x0E : pos + 0x20]) if include_text else "<redacted>"
            if not label and kind == 0:
                label = "Artifact" if include_text else "<redacted>"
            marks.append(LegacyMark(onset, label, duration, f"extended_type_{kind}"))
    return marks


def parse_legacy_event_groups(eeg: LegacyEEG) -> list[LegacyEventGroup]:
    """Parse the native event-group descriptors and their absolute entry lists."""
    header = eeg.header
    count = header["event_group_count"]
    offset = header["event_group_offset"]
    if not count:
        return []
    if offset <= 0 or offset + count * LEGACY_EVENT_GROUP_SIZE > len(eeg.raw):
        raise ValueError("legacy event-group descriptor table is outside the file")
    groups: list[LegacyEventGroup] = []
    for index in range(count):
        pos = offset + index * LEGACY_EVENT_GROUP_SIZE
        entry_count, entries_offset, duration_ms = struct.unpack_from("<HIH", eeg.raw, pos)
        entries_end = entries_offset + entry_count * LEGACY_EVENT_MAP_SIZE
        if entries_offset <= 0 or entries_end > len(eeg.raw):
            raise ValueError(f"legacy event-group {index} entry list is outside the file")
        entries = tuple(
            LegacyEventGroupEntry(*struct.unpack_from("<HHI", eeg.raw, entries_offset + item * 8))
            for item in range(entry_count)
        )
        groups.append(LegacyEventGroup(entries, duration_ms))
    return groups


def parse_legacy_mapped_events(eeg: LegacyEEG) -> list[LegacyMappedEvent]:
    """Correlate the native index table with the parallel event-map table."""
    header = eeg.header
    index_count = header["index_count"]
    map_count = header["event_map_count"]
    if not index_count and not map_count:
        return []
    if index_count != map_count:
        raise ValueError(
            f"legacy mapped-event tables differ in length ({index_count} index, {map_count} map)"
        )
    index_offset = header["index_offset"]
    map_offset = header["event_map_offset"]
    if index_offset <= 0 or index_offset + index_count * 8 > len(eeg.raw):
        raise ValueError("legacy mapped-event index table is outside the file")
    if map_offset <= 0 or map_offset + map_count * 8 > len(eeg.raw):
        raise ValueError("legacy event-map table is outside the file")
    groups = parse_legacy_event_groups(eeg)
    events: list[LegacyMappedEvent] = []
    for item in range(index_count):
        onset_ms, lane_code, flags = struct.unpack_from("<IHH", eeg.raw, index_offset + item * 8)
        group_index, event_code, _reserved = struct.unpack_from(
            "<HHI", eeg.raw, map_offset + item * 8
        )
        if group_index >= len(groups):
            raise ValueError(
                f"legacy mapped event {item} references missing group {group_index}"
            )
        events.append(LegacyMappedEvent(onset_ms, group_index, event_code, lane_code, flags))
    return events


def legacy_section_map(eeg: LegacyEEG) -> list[dict[str, Any]]:
    h = eeg.header
    sections = [
        ("header", 0, LEGACY_HEADER_SIZE, 1),
        ("profile_redacted", h["profile_offset"], LEGACY_PROFILE_SIZE, 1),
        ("montage", h["montage_offset"], LEGACY_MONTAGE_SIZE, 1),
        (
            "channel_descriptors",
            h["channel_table_offset"],
            h["channel_count"] * LEGACY_CHANNEL_DESCRIPTOR_SIZE,
            h["channel_count"],
        ),
        (
            "sample_frames",
            h["sample_data_offset"],
            h["primary_marks_offset"] - h["sample_data_offset"],
            eeg.physical_frame_count,
        ),
        (
            "primary_marks",
            h["primary_marks_offset"],
            h["primary_mark_count"] * LEGACY_PRIMARY_MARK_SIZE,
            h["primary_mark_count"],
        ),
        (
            "view_descriptors",
            h["view_descriptor_offset"],
            h["view_descriptor_count"] * LEGACY_VIEW_DESCRIPTOR_SIZE,
            h["view_descriptor_count"],
        ),
        (
            "index_records",
            h["index_offset"],
            h["index_count"] * LEGACY_INDEX_RECORD_SIZE,
            h["index_count"],
        ),
        ("settings", h["settings_offset"], LEGACY_SETTINGS_SIZE, 1 if h["settings_offset"] else 0),
        (
            "aux_records",
            h["aux_record_offset"],
            h["aux_record_count"] * LEGACY_AUX_RECORD_SIZE,
            h["aux_record_count"],
        ),
        ("text_blob", h["text_blob_offset"], h["text_blob_size"], h["text_line_count"]),
        (
            "extended_marks",
            h["extended_marks_offset"],
            h["extended_mark_count"] * LEGACY_EXTENDED_MARK_DISK_SIZE,
            h["extended_mark_count"],
        ),
        (
            "float_table",
            h["float_table_offset"],
            LEGACY_FLOAT_TABLE_SIZE if h["float_table_present"] else 0,
            1 if h["float_table_present"] else 0,
        ),
        (
            "event_map",
            h["event_map_offset"],
            h["event_map_count"] * LEGACY_EVENT_MAP_SIZE,
            h["event_map_count"],
        ),
        (
            "event_groups",
            h["event_group_offset"],
            h["event_group_count"] * LEGACY_EVENT_GROUP_SIZE,
            h["event_group_count"],
        ),
        (
            "extra_descriptors",
            h["extra_descriptor_offset"],
            h["extra_descriptor_count"] * LEGACY_CHANNEL_DESCRIPTOR_SIZE,
            h["extra_descriptor_count"],
        ),
        (
            "optional_block",
            h["optional_block_offset"],
            LEGACY_OPTIONAL_BLOCK_SIZE if h["optional_block_size"] else 0,
            1 if h["optional_block_size"] else 0,
        ),
    ]
    rows = []
    for name, offset, size, count in sections:
        if not offset and not size and name not in {"header"}:
            status = "absent"
        elif 0 <= offset <= offset + size <= len(eeg.raw):
            status = "in_file"
        else:
            status = "outside_or_stale"
        rows.append({"section": name, "offset": offset, "size": size, "count": count, "status": status})
    return rows


def _edf_text(raw: bytes) -> str:
    return raw.decode("ascii", errors="replace").strip()


def _edf_number(raw: bytes, kind: type[int] | type[float]) -> int | float:
    value = _edf_text(raw)
    if not value:
        raise ValueError("blank required EDF numeric field")
    return kind(value)


def parse_edf(raw: bytes) -> EDFFile:
    if len(raw) < EDF_FIXED_HEADER_SIZE:
        raise ValueError("EDF is shorter than its fixed 256-byte header")
    version = _edf_text(raw[0:8])
    patient = _edf_text(raw[8:88])
    recording = _edf_text(raw[88:168])
    start_date = _edf_text(raw[168:176])
    start_time = _edf_text(raw[176:184])
    header_bytes = int(_edf_number(raw[184:192], int))
    reserved = _edf_text(raw[192:236])
    data_records = int(_edf_number(raw[236:244], int))
    record_duration = float(_edf_number(raw[244:252], float))
    signal_count = int(_edf_number(raw[252:256], int))
    expected_header = EDF_FIXED_HEADER_SIZE + signal_count * EDF_SIGNAL_HEADER_SIZE
    if signal_count < 1 or header_bytes < expected_header or header_bytes > len(raw):
        raise ValueError("EDF signal count/header size is inconsistent")
    if record_duration <= 0:
        raise ValueError("EDF data-record duration must be positive")

    cursor = EDF_FIXED_HEADER_SIZE
    widths = (16, 80, 8, 8, 8, 8, 8, 80, 8, 32)
    columns: list[list[bytes]] = []
    for width in widths:
        columns.append([raw[cursor + i * width : cursor + (i + 1) * width] for i in range(signal_count)])
        cursor += width * signal_count
    signals: list[EDFSignal] = []
    for i in range(signal_count):
        signals.append(
            EDFSignal(
                label=_edf_text(columns[0][i]),
                transducer=_edf_text(columns[1][i]),
                physical_dimension=_edf_text(columns[2][i]),
                physical_min=float(_edf_number(columns[3][i], float)),
                physical_max=float(_edf_number(columns[4][i], float)),
                digital_min=int(_edf_number(columns[5][i], int)),
                digital_max=int(_edf_number(columns[6][i], int)),
                prefilter=_edf_text(columns[7][i]),
                samples_per_record=int(_edf_number(columns[8][i], int)),
                reserved=_edf_text(columns[9][i]),
            )
        )

    bytes_per_record = sum(signal.samples_per_record * 2 for signal in signals)
    if bytes_per_record <= 0:
        raise ValueError("EDF data record is empty")
    available_records = (len(raw) - header_bytes) // bytes_per_record
    if data_records < 0:
        data_records = available_records
    if data_records > available_records:
        raise ValueError("EDF declares more complete data records than the file contains")

    digital = [[] for _ in signals]
    annotation_signal_lanes = {
        signal_index: lane_index
        for lane_index, signal_index in enumerate(
            index
            for index, signal in enumerate(signals)
            if signal.label.strip().lower() == EDF_ANNOTATION_LABEL.lower()
        )
    }
    annotation_payloads: list[tuple[int, bytes]] = []
    cursor = header_bytes
    for _record in range(data_records):
        for index, signal in enumerate(signals):
            size = signal.samples_per_record * 2
            payload = raw[cursor : cursor + size]
            cursor += size
            if signal.label.strip().lower() == EDF_ANNOTATION_LABEL.lower():
                annotation_payloads.append((annotation_signal_lanes[index], payload))
            else:
                digital[index].extend(struct.unpack("<" + "h" * signal.samples_per_record, payload))
    annotations: list[EDFAnnotation] = []
    for lane_index, payload in annotation_payloads:
        annotations.extend(
            replace(annotation, lane_index=lane_index) for annotation in parse_edf_tal(payload)
        )
    return EDFFile(
        version,
        patient,
        recording,
        start_date,
        start_time,
        reserved,
        data_records,
        record_duration,
        signals,
        digital,
        annotations,
    )


def read_edf(path: Path) -> EDFFile:
    return parse_edf(path.read_bytes())


def parse_edf_tal(payload: bytes) -> list[EDFAnnotation]:
    annotations: list[EDFAnnotation] = []
    for tal in payload.split(b"\0"):
        if not tal:
            continue
        delimiter_positions = [pos for pos in (tal.find(b"\x14"), tal.find(b"\x15")) if pos >= 0]
        if not delimiter_positions:
            continue
        split = min(delimiter_positions)
        try:
            onset = float(tal[:split].decode("ascii"))
        except (UnicodeDecodeError, ValueError):
            continue
        cursor = split
        duration: float | None = None
        if tal[cursor : cursor + 1] == b"\x15":
            cursor += 1
            end = tal.find(b"\x14", cursor)
            if end < 0:
                continue
            try:
                duration = float(tal[cursor:end].decode("ascii"))
            except (UnicodeDecodeError, ValueError):
                duration = None
            cursor = end
        if tal[cursor : cursor + 1] != b"\x14":
            continue
        texts = tal[cursor + 1 :].split(b"\x14")
        for text in texts:
            if text:
                annotations.append(
                    EDFAnnotation(onset, duration, text.decode("utf-8", errors="replace"))
                )
    return annotations


def _edf_field(value: str | int | float, width: int) -> bytes:
    if isinstance(value, float):
        text = f"{value:.10g}"
    else:
        text = str(value)
    encoded = text.encode("ascii", errors="replace")
    if len(encoded) > width:
        encoded = encoded[:width]
    return encoded.ljust(width, b" ")


def _annotation_tal(annotation: EDFAnnotation) -> bytes:
    onset = f"{annotation.onset_seconds:+.6f}".rstrip("0").rstrip(".")
    result = bytearray(onset.encode("ascii"))
    if annotation.duration_seconds is not None:
        duration = f"{annotation.duration_seconds:.6f}".rstrip("0").rstrip(".")
        result.extend(b"\x15" + duration.encode("ascii"))
    result.extend(b"\x14" + annotation.text.encode("utf-8", errors="replace") + b"\x14\0")
    return bytes(result)


def write_edf(path: Path, edf: EDFFile) -> None:
    signal_count = len(edf.signals)
    if signal_count != len(edf.digital_samples):
        raise ValueError("EDF signals and digital-sample arrays differ in length")
    header_bytes = EDF_FIXED_HEADER_SIZE + signal_count * EDF_SIGNAL_HEADER_SIZE
    header = bytearray()
    header.extend(_edf_field(edf.version or "0", 8))
    header.extend(_edf_field(edf.patient_id, 80))
    header.extend(_edf_field(edf.recording_id, 80))
    header.extend(_edf_field(edf.start_date, 8))
    header.extend(_edf_field(edf.start_time, 8))
    header.extend(_edf_field(header_bytes, 8))
    header.extend(_edf_field(edf.reserved, 44))
    header.extend(_edf_field(edf.data_record_count, 8))
    header.extend(_edf_field(edf.data_record_duration, 8))
    header.extend(_edf_field(signal_count, 4))
    getters = (
        (lambda s: s.label, 16),
        (lambda s: s.transducer, 80),
        (lambda s: s.physical_dimension, 8),
        (lambda s: s.physical_min, 8),
        (lambda s: s.physical_max, 8),
        (lambda s: s.digital_min, 8),
        (lambda s: s.digital_max, 8),
        (lambda s: s.prefilter, 80),
        (lambda s: s.samples_per_record, 8),
        (lambda s: s.reserved, 32),
    )
    for getter, width in getters:
        for signal in edf.signals:
            header.extend(_edf_field(getter(signal), width))
    if len(header) != header_bytes:
        raise AssertionError("internal EDF header-size error")

    output = bytearray(header)
    positions = [0] * signal_count
    for _record in range(edf.data_record_count):
        for index, signal in enumerate(edf.signals):
            count = signal.samples_per_record
            start = positions[index]
            values = edf.digital_samples[index][start : start + count]
            if len(values) < count:
                values = list(values) + [0] * (count - len(values))
            output.extend(struct.pack("<" + "h" * count, *values))
            positions[index] += count
    path.write_bytes(output)


def _unit_factor_to_uv(dimension: str) -> float:
    value = dimension.strip().replace("µ", "u").replace("μ", "u")
    value = value.replace("\u00b5", "u").replace("\u03bc", "u")
    if not value or not value.endswith("V"):
        return 1.0
    prefix = value[:-1]
    return {
        "": 1_000_000.0,
        "k": 1_000_000_000.0,
        "M": 1_000_000_000_000.0,
        "m": 1_000.0,
        "u": 1.0,
        "n": 0.001,
        "p": 0.000001,
    }.get(prefix, 1.0)


def select_wineeg_sample_rate(max_source_rate: float) -> int:
    """Reproduce CEdfPlusFile::FUN_00831290's import-rate buckets."""
    if max_source_rate <= 128.0:
        return 125
    if max_source_rate <= 256.0:
        return 250
    if max_source_rate <= 512.0:
        return 500
    if max_source_rate <= 1024.0:
        return 1000
    return 2000


def _resample_linear(values: Sequence[int], source_rate: float, target_rate: int, count: int) -> list[int]:
    if not values or count <= 0:
        return []
    if abs(source_rate - target_rate) < 1e-9 and len(values) >= count:
        return list(values[:count])
    step = source_rate / target_rate
    result: list[int] = []
    for index in range(count):
        position = index * step
        left = min(int(position), len(values) - 1)
        right = min(left + 1, len(values) - 1)
        fraction = position - left
        value = values[left] * (1.0 - fraction) + values[right] * fraction
        result.append(max(-32768, min(32767, int(round(value)))))
    return result


def _event_text(text: str, policy: str, index: int) -> str | None:
    if policy == "drop":
        return None
    if policy == "redact":
        return f"EVENT_{index + 1:04d}"
    preserved = ASCII_SAFE.sub("?", text)
    return preserved if preserved else f"EVENT_{index + 1:04d}"


# These are native group definitions recovered independently from the paired
# Jonma ERP recording (3000 ms) and WinEEG's 480-event 06-69 fixture (3300 ms).
# EDF exports retain onset, duration, and the class code, but not which one of
# several same-class stimulus templates was selected.  A deterministic
# representative is therefore used for each exported class.
_MAPPED_GROUP_TEMPLATES: dict[int, dict[int, tuple[tuple[int, int], ...]]] = {
    3000: {
        1: ((300, 1), (400, 0), (1400, 1), (1500, 0)),
        2: ((300, 1), (400, 0), (1400, 21), (1500, 0)),
        3: ((300, 21), (400, 0), (1400, 21), (1500, 0)),
        4: ((300, 21), (400, 0), (1400, 41), (1500, 0), (1400, 63), (1500, 0)),
    },
    3300: {
        1: ((300, 1), (450, 0), (1200, 1), (1350, 0)),
        2: ((300, 1), (450, 0), (1200, 2), (1350, 0)),
        3: ((300, 2), (450, 0), (1200, 1), (1350, 0)),
        4: ((300, 2), (450, 0), (1200, 2), (1350, 0)),
    },
}


def _canonical_mapped_event_model(
    annotations: Sequence[EDFAnnotation],
) -> tuple[list[LegacyMappedEvent], list[LegacyEventGroup]]:
    parsed: list[tuple[int, int, int]] = []
    for index, annotation in enumerate(annotations):
        text = annotation.text.strip()
        if not re.fullmatch(r"[1-4]", text):
            raise ValueError(
                "native EDF annotation lane 4 contains an unsupported mapped-event code "
                f"at item {index}: {text!r}; only the recovered numeric codes 1..4 are proven"
            )
        if annotation.duration_seconds is None:
            raise ValueError(
                f"native EDF annotation lane 4 item {index} has no group duration"
            )
        duration_value = annotation.duration_seconds * 1000.0
        duration_ms = int(round(duration_value))
        if abs(duration_value - duration_ms) > 1e-6 or duration_ms not in _MAPPED_GROUP_TEMPLATES:
            raise ValueError(
                "native EDF annotation lane 4 uses an unproven group duration at item "
                f"{index}: {annotation.duration_seconds!r} seconds; proven durations are "
                "3.0 and 3.3 seconds"
            )
        onset_value = annotation.onset_seconds * 1000.0
        onset_ms = int(round(onset_value))
        if abs(onset_value - onset_ms) > 1e-6 or not 0 <= onset_ms <= 0xFFFFFFFF:
            raise ValueError(
                f"native EDF annotation lane 4 item {index} onset cannot fit native milliseconds"
            )
        parsed.append((onset_ms, int(text), duration_ms))

    keys = sorted({(code, duration_ms) for _onset, code, duration_ms in parsed})
    groups: list[LegacyEventGroup] = []
    group_indexes: dict[tuple[int, int], int] = {}
    for code, duration_ms in keys:
        group_indexes[(code, duration_ms)] = len(groups)
        entries = tuple(
            LegacyEventGroupEntry(relative_ms, stimulus_code)
            for relative_ms, stimulus_code in _MAPPED_GROUP_TEMPLATES[duration_ms][code]
        )
        groups.append(LegacyEventGroup(entries, duration_ms))
    events = [
        LegacyMappedEvent(onset_ms, group_indexes[(code, duration_ms)], code)
        for onset_ms, code, duration_ms in sorted(parsed, key=lambda item: item[0])
    ]
    return events, groups


def _default_settings() -> bytes:
    block = bytearray(LEGACY_SETTINGS_SIZE)
    block[:7] = b"Default"
    for index in range(32):
        struct.pack_into("<H", block, 0x40 + index * 0x42, 1000)
    return bytes(block)


def _channel_descriptor(
    index: int,
    label: str,
    scale: float,
    zero_offset: float,
) -> bytes:
    block = bytearray(LEGACY_CHANNEL_DESCRIPTOR_SIZE)
    block[:8] = _legacy_label_bytes(label)
    lookup = label.strip().upper()
    if lookup.startswith("EEG "):
        lookup = lookup[4:].strip()
    if lookup not in STANDARD_CHANNELS and "-" in lookup:
        # Preserve labels such as Fp1-LE/C3-REF on disk while assigning the
        # proven WinEEG topology code and coordinates for their base sensor.
        base = lookup.split("-", 1)[0].strip()
        if base in STANDARD_CHANNELS:
            lookup = base
    # FUN_0082f310 uses -1 when an imported EDF label is not in WinEEG's
    # built-in channel table.  Do not assign an arbitrary standard-channel
    # code based on ordinal position.
    code, kind, x, y = STANDARD_CHANNELS.get(lookup, (-1, 0, 0, 0))
    struct.pack_into("<hhhhh", block, 0x08, code, 0, 0, 3, 0)
    struct.pack_into("<hh", block, 0x14, x, y)
    struct.pack_into("<f", block, 0x18, float(scale))
    struct.pack_into("<h", block, 0x1C, kind)
    struct.pack_into("<f", block, 0x26, 0.0)
    struct.pack_into("<f", block, 0x2A, float(zero_offset))
    return bytes(block)


def _primary_mark_bytes(onset_ms: int, label: str) -> bytes:
    block = bytearray(LEGACY_PRIMARY_MARK_SIZE)
    encoded = ASCII_SAFE.sub("?", label).encode("ascii", errors="replace")[:24]
    struct.pack_into("<IH", block, 0, max(0, onset_ms), len(encoded))
    block[6 : 6 + len(encoded)] = encoded
    return bytes(block)


def write_legacy_eeg(
    path: Path,
    *,
    sample_rate_hz: int,
    channels: Sequence[tuple[str, float, float]],
    digital_samples: Sequence[Sequence[int]],
    marks: Sequence[LegacyMark] = (),
    mapped_events: Sequence[LegacyMappedEvent] = (),
    event_groups: Sequence[LegacyEventGroup] = (),
    format_flags: int = 0x0123,
) -> None:
    if not channels or len(channels) != len(digital_samples):
        raise ValueError("legacy channel metadata and sample arrays must be non-empty and equal")
    if not 1 <= len(channels) <= 256:
        raise ValueError("legacy EEG supports 1..256 channels")
    frame_count = min(len(values) for values in digital_samples)
    channel_count = len(channels)
    sample_start = LEGACY_CHANNEL_TABLE_OFFSET + channel_count * LEGACY_CHANNEL_DESCRIPTOR_SIZE
    sample_end = sample_start + frame_count * channel_count * 2
    marks = list(marks)
    mapped_events = list(mapped_events)
    event_groups = list(event_groups)
    if len(marks) > 256:
        raise ValueError(
            "EDF contains "
            f"{len(marks)} annotations, but the proven native WinEEG primary-mark "
            "table accepts at most 256. Refusing to truncate events; use "
            "--event-policy drop only if an event-free EEG is intentional."
        )
    if len(mapped_events) > 0xFFFF or len(event_groups) > 0xFFFF:
        raise ValueError("native mapped-event and event-group counts must fit unsigned 16-bit fields")
    if bool(mapped_events) != bool(event_groups):
        raise ValueError("native mapped events and event groups must either both be present or absent")
    for index, event in enumerate(mapped_events):
        if not 0 <= event.onset_ms <= 0xFFFFFFFF:
            raise ValueError(f"mapped event {index} onset is outside the native uint32 range")
        if not 0 <= event.group_index < len(event_groups):
            raise ValueError(f"mapped event {index} has an invalid group index")
        if not 0 <= event.event_code <= 0xFFFF:
            raise ValueError(f"mapped event {index} code is outside the native uint16 range")
        if not 0 <= event.lane_code <= 0xFFFF or not 0 <= event.flags <= 0xFFFF:
            raise ValueError(f"mapped event {index} index metadata is outside uint16 range")
    for group_index, group in enumerate(event_groups):
        if len(group.entries) > 0xFFFF or not 0 <= group.duration_ms <= 0xFFFF:
            raise ValueError(f"event group {group_index} cannot fit the native descriptor")
        for entry_index, entry in enumerate(group.entries):
            if not 0 <= entry.relative_ms <= 0xFFFF or not 0 <= entry.stimulus_code <= 0xFFFF:
                raise ValueError(
                    f"event group {group_index} entry {entry_index} cannot fit uint16 fields"
                )
            if not 0 <= entry.reserved <= 0xFFFFFFFF:
                raise ValueError(
                    f"event group {group_index} entry {entry_index} reserved field is invalid"
                )
    primary_marks_offset = sample_end
    after_marks = primary_marks_offset + len(marks) * LEGACY_PRIMARY_MARK_SIZE
    index_offset = after_marks
    settings_offset = index_offset + len(mapped_events) * LEGACY_INDEX_RECORD_SIZE
    event_map_offset = settings_offset + LEGACY_SETTINGS_SIZE
    event_group_offset = event_map_offset + len(mapped_events) * LEGACY_EVENT_MAP_SIZE
    group_entries_offset = event_group_offset + len(event_groups) * LEGACY_EVENT_GROUP_SIZE
    group_entry_offsets: list[int] = []
    file_end = group_entries_offset
    for group in event_groups:
        group_entry_offsets.append(file_end)
        file_end += len(group.entries) * LEGACY_EVENT_MAP_SIZE

    header = bytearray(LEGACY_HEADER_SIZE)
    values: dict[str, int] = {
        "magic": LEGACY_MAGIC,
        "channel_count": channel_count,
        "sample_rate_hz": sample_rate_hz,
        "primary_mark_count": len(marks),
        "text_blob_size": 0,
        "sample_width_bytes": 2,
        "bits_per_sample": 16,
        "extended_mark_count": 0,
        "logical_frame_count": frame_count,
        "profile_offset": LEGACY_PROFILE_OFFSET,
        "channel_table_offset": LEGACY_CHANNEL_TABLE_OFFSET,
        "sample_data_offset": sample_start,
        "primary_marks_offset": primary_marks_offset,
        "extended_marks_offset": event_map_offset,
        "text_blob_offset": event_map_offset,
        "word_0x2c": 0,
        "word_0x2e": 0,
        "word_0x30": 0,
        "index_count": len(mapped_events),
        "index_offset": index_offset,
        "text_line_count": 0,
        "montage_offset": LEGACY_MONTAGE_OFFSET,
        "format_flags": format_flags,
        "alternate_block_count": 1 if mapped_events else 0,
        "alternate_block_offset": 0,
        "alternate_block_frames": 0,
        "event_map_count": len(mapped_events),
        "event_group_count": len(event_groups),
        "event_map_offset": event_map_offset,
        "event_group_offset": event_group_offset,
        "settings_offset": settings_offset,
        "aux_record_offset": event_map_offset,
        "aux_record_count": 0,
        "float_table_present": 0,
        "float_table_offset": 0,
        "view_descriptor_count": 0,
        "view_descriptor_offset": after_marks,
        "extra_descriptor_count": 0,
        "extra_descriptor_offset": file_end,
        "optional_block_offset": 0,
        "optional_block_size": 0,
    }
    for name, offset, fmt, _confidence in HEADER_FIELDS:
        _pack_into(header, offset, fmt, values.get(name, 0))

    output = bytearray(header)
    output.extend(bytes(LEGACY_PROFILE_SIZE))
    output.extend(bytes(LEGACY_MONTAGE_SIZE))
    for index, (label, scale, zero) in enumerate(channels):
        output.extend(_channel_descriptor(index, label, scale, zero))
    for frame in range(frame_count):
        for channel in range(channel_count):
            value = max(-32768, min(32767, int(digital_samples[channel][frame])))
            output.extend(struct.pack("<h", value))
    for mark in marks:
        output.extend(_primary_mark_bytes(mark.onset_ms, mark.label))
    for event in mapped_events:
        output.extend(struct.pack("<IHH", event.onset_ms, event.lane_code, event.flags))
    output.extend(_default_settings())
    for event in mapped_events:
        output.extend(struct.pack("<HHI", event.group_index, event.event_code, 0))
    for group, entries_offset in zip(event_groups, group_entry_offsets):
        output.extend(struct.pack("<HIH", len(group.entries), entries_offset, group.duration_ms))
    for group in event_groups:
        for entry in group.entries:
            output.extend(
                struct.pack("<HHI", entry.relative_ms, entry.stimulus_code, entry.reserved)
            )
    if len(output) != file_end:
        raise AssertionError("internal legacy EEG layout error")
    path.write_bytes(output)


def edf_to_legacy(
    source: Path,
    destination: Path,
    *,
    event_policy: str = "redact",
    sidecar: Path | None = None,
) -> dict[str, Any]:
    edf = read_edf(source)
    selected = [
        index
        for index, signal in enumerate(edf.signals)
        if signal.label.strip().lower() != EDF_ANNOTATION_LABEL.lower()
    ]
    if not selected:
        raise ValueError("EDF contains no non-annotation signals")
    rates = [edf.signals[index].samples_per_record / edf.data_record_duration for index in selected]
    target_rate = select_wineeg_sample_rate(max(rates))
    duration = edf.data_record_count * edf.data_record_duration
    target_frames = max(0, int(duration * target_rate))
    channel_meta: list[tuple[str, float, float]] = []
    samples: list[list[int]] = []
    full_labels: list[str] = []
    for index, source_rate in zip(selected, rates):
        signal = edf.signals[index]
        physical_span = signal.physical_max - signal.physical_min
        digital_span = signal.digital_max - signal.digital_min
        if physical_span == 0 or digital_span == 0:
            scale = 1.0
            zero = 0.0
        else:
            scale = (physical_span / digital_span) * _unit_factor_to_uv(signal.physical_dimension)
            zero = signal.digital_min - digital_span * signal.physical_min / physical_span
        label = signal.label.strip() or f"CH{len(channel_meta) + 1}"
        full_labels.append(label)
        channel_meta.append((label, scale, zero))
        samples.append(_resample_linear(edf.digital_samples[index], source_rate, target_rate, target_frames))

    annotation_signal_count = sum(
        signal.label.strip().lower() == EDF_ANNOTATION_LABEL.lower()
        for signal in edf.signals
    )
    lane_counts = {
        lane: sum(annotation.lane_index == lane for annotation in edf.annotations)
        for lane in range(annotation_signal_count)
    }
    native_lane_layout = annotation_signal_count == len(NATIVE_EDF_ANNOTATION_LANES)
    if native_lane_layout:
        unsupported = [
            lane
            for lane in (0, 2, 3)
            if lane_counts.get(lane, 0)
        ]
        if unsupported and event_policy != "drop":
            names = ", ".join(NATIVE_EDF_ANNOTATION_LANES[lane] for lane in unsupported)
            raise ValueError(
                "native EDF contains semantic annotations in lanes whose native serializer is "
                f"not yet proven ({names}); refusing to flatten or discard them"
            )
        primary_annotations = [
            annotation for annotation in edf.annotations if annotation.lane_index == 1
        ]
        mapped_annotations = [
            annotation for annotation in edf.annotations if annotation.lane_index == 4
        ]
    else:
        # A single/non-native annotation stream has no lane provenance.  It is
        # deliberately kept on the conservative primary-mark path, including
        # the 256-record refusal guard.
        primary_annotations = list(edf.annotations)
        mapped_annotations = []

    marks: list[LegacyMark] = []
    mapped_events: list[LegacyMappedEvent] = []
    event_groups: list[LegacyEventGroup] = []
    sidecar_events: list[dict[str, Any]] = []
    for index, annotation in enumerate(primary_annotations):
        label = _event_text(annotation.text, event_policy, index)
        if label is None:
            continue
        onset_ms = max(0, int(round(annotation.onset_seconds * 1000.0)))
        duration_ms = int(round((annotation.duration_seconds or 0.0) * 1000.0))
        marks.append(LegacyMark(onset_ms, label, duration_ms))
        sidecar_events.append({
            "onset_ms": onset_ms,
            "duration_ms": duration_ms,
            "label": label,
            "family": "primary",
            "annotation_lane": annotation.lane_index,
        })
    if mapped_annotations and event_policy != "drop":
        mapped_events, event_groups = _canonical_mapped_event_model(mapped_annotations)
        duration_by_group = [group.duration_ms for group in event_groups]
        sidecar_events.extend(
            {
                "onset_ms": event.onset_ms,
                "duration_ms": duration_by_group[event.group_index],
                "label": str(event.event_code),
                "family": "mapped",
                "annotation_lane": 4,
                "group_index": event.group_index,
            }
            for event in mapped_events
        )
    write_legacy_eeg(
        destination,
        sample_rate_hz=target_rate,
        channels=channel_meta,
        digital_samples=samples,
        marks=marks,
        mapped_events=mapped_events,
        event_groups=event_groups,
        format_flags=0x010F if mapped_events else 0x0123,
    )
    manifest = {
        "format": "wineeg_edf_to_legacy_conversion",
        "identity_policy": "EDF patient/recording identity not copied; legacy profile is zero-filled",
        "event_policy": event_policy,
        "source_signal_count": len(edf.signals),
        "converted_channel_count": len(selected),
        "target_sample_rate_hz": target_rate,
        "target_frame_count": target_frames,
        "full_channel_labels": full_labels,
        "annotation_signal_count": annotation_signal_count,
        "native_annotation_lane_layout": native_lane_layout,
        "annotation_lane_counts": {
            (
                NATIVE_EDF_ANNOTATION_LANES[lane]
                if native_lane_layout
                else f"generic_lane_{lane}"
            ): count
            for lane, count in lane_counts.items()
        },
        "primary_mark_count": len(marks),
        "mapped_event_count": len(mapped_events),
        "event_group_count": len(event_groups),
        "events": sidecar_events,
        "loss_notes": [
            "Legacy primary mark labels are limited to 24 bytes and 256 records; conversion refuses larger event sets instead of truncating them.",
            "Generic/primary EDF+ durations are retained only in this sidecar; the primary mark family stores onset and label.",
            "For native lane-4 events, EDF retains onset, duration, and class code but omits the original same-class stimulus-template selection; the serializer uses a deterministic recovered representative group.",
            "Channel labels in the .eeg descriptor are limited to seven ASCII bytes; full labels remain in this sidecar.",
        ],
    }
    if sidecar is not None:
        sidecar.write_text(json.dumps(manifest, indent=2), encoding="utf-8")
    return manifest


def _largest_divisor_at_most(value: int, limit: int) -> int:
    if value <= 0:
        return 1
    for candidate in range(min(value, max(1, limit)), 0, -1):
        if value % candidate == 0:
            return candidate
    return 1


def _build_annotation_records(
    annotations: Sequence[EDFAnnotation],
    record_count: int,
    record_duration: float,
    *,
    include_timekeeping: bool = True,
) -> tuple[list[bytes], int]:
    records: list[bytes] = []
    max_size = 0
    for record in range(record_count):
        start = record * record_duration
        end = start + record_duration
        payload = bytearray()
        if include_timekeeping:
            payload.extend(f"{start:+.6f}".rstrip("0").rstrip(".").encode("ascii"))
            payload.extend(b"\x14\x14\0")
        for annotation in annotations:
            if start <= annotation.onset_seconds < end or (
                record == record_count - 1 and annotation.onset_seconds == end
            ):
                payload.extend(_annotation_tal(annotation))
        records.append(bytes(payload))
        max_size = max(max_size, len(payload))
    samples = max(64, (max_size + 1) // 2)
    return records, samples


def legacy_to_edf(
    source: Path,
    destination: Path,
    *,
    event_policy: str = "redact",
    physical_frames: bool = False,
    conversion_sidecar: dict[str, Any] | None = None,
) -> dict[str, Any]:
    eeg = read_legacy_eeg(source)
    frame_count = eeg.physical_frame_count if physical_frames else min(
        eeg.physical_frame_count, eeg.header["logical_frame_count"]
    )
    rate = eeg.header["sample_rate_hz"]
    samples_per_record = _largest_divisor_at_most(frame_count, rate * 60)
    record_count = max(1, frame_count // samples_per_record) if frame_count else 1
    record_duration = samples_per_record / rate
    physical = legacy_physical_channels(eeg, logical_only=not physical_frames)
    sidecar_labels = (
        conversion_sidecar.get("full_channel_labels", [])
        if conversion_sidecar is not None
        else []
    )
    signals: list[EDFSignal] = []
    digital_arrays: list[list[int]] = []
    restored_full_channel_labels = 0
    for channel_index, (descriptor, values) in enumerate(zip(eeg.channels, physical)):
        is_label = descriptor.label.strip().upper() == "LABEL" or descriptor.kind == 4
        multiplier = 1.0 if is_label else 4.0
        digital = [max(-32767, min(32767, int(value * multiplier))) for value in values[:frame_count]]
        physical_limit = 32767.0 if is_label else 8191.75
        output_label = descriptor.label
        if channel_index < len(sidecar_labels):
            candidate = str(sidecar_labels[channel_index]).strip()
            if candidate:
                output_label = candidate
                if candidate != descriptor.label:
                    restored_full_channel_labels += 1
        signals.append(
            EDFSignal(
                label=output_label,
                transducer="" if is_label else "AgAgCl electrode",
                physical_dimension="" if is_label else "uV",
                physical_min=-physical_limit,
                physical_max=physical_limit,
                digital_min=-32767,
                digital_max=32767,
                prefilter="",
                samples_per_record=samples_per_record,
            )
        )
        digital_arrays.append(digital)

    annotation_lanes: list[list[EDFAnnotation]] = [
        [] for _ in NATIVE_EDF_ANNOTATION_LANES
    ]
    parsed_marks = parse_legacy_marks(eeg, include_text=event_policy == "preserve")
    primary_sidecar_events = (
        [event for event in conversion_sidecar.get("events", []) if event.get("family") == "primary"]
        if conversion_sidecar is not None
        else []
    )
    restored_primary_durations = 0
    restored_primary_labels = 0
    primary_index = 0
    for index, mark in enumerate(parsed_marks):
        restored_label = mark.label
        restored_duration_ms = mark.duration_ms
        if mark.family == "primary":
            sidecar_event = (
                primary_sidecar_events[primary_index]
                if primary_index < len(primary_sidecar_events)
                else None
            )
            primary_index += 1
            if (
                event_policy != "drop"
                and sidecar_event is not None
                and int(sidecar_event["onset_ms"]) == mark.onset_ms
            ):
                sidecar_duration = int(sidecar_event.get("duration_ms", 0))
                if sidecar_duration != mark.duration_ms:
                    restored_duration_ms = sidecar_duration
                    restored_primary_durations += 1
                if event_policy == "preserve":
                    sidecar_label = str(sidecar_event.get("label", ""))
                    if sidecar_label and sidecar_label != mark.label:
                        restored_label = sidecar_label
                        restored_primary_labels += 1
        label = _event_text(restored_label, event_policy, index)
        if label is None:
            continue
        lane_index = 1
        if mark.family == "extended_type_0":
            lane_index = 3
        elif mark.family.startswith("extended_type_"):
            lane_index = 2
        annotation_lanes[lane_index].append(
            EDFAnnotation(
                mark.onset_ms / 1000.0,
                restored_duration_ms / 1000.0 if restored_duration_ms else None,
                label,
                lane_index,
            )
        )

    if event_policy != "drop":
        groups = parse_legacy_event_groups(eeg)
        for event in parse_legacy_mapped_events(eeg):
            group = groups[event.group_index]
            annotation_lanes[4].append(
                EDFAnnotation(
                    event.onset_ms / 1000.0,
                    group.duration_ms / 1000.0,
                    str(event.event_code),
                    4,
                )
            )

    annotations = [annotation for lane in annotation_lanes for annotation in lane]
    if annotations:
        # Emit all five native lanes even when some are empty.  Their ordinal
        # position is the provenance needed to reconstruct the correct native
        # subsystem on a later EDF -> EEG conversion.
        for lane_index, lane_annotations in enumerate(annotation_lanes):
            annotation_records, annotation_samples = _build_annotation_records(
                lane_annotations,
                record_count,
                record_duration,
                include_timekeeping=lane_index == 0,
            )
            signals.append(
                EDFSignal(
                    label=EDF_ANNOTATION_LABEL,
                    transducer="",
                    physical_dimension="",
                    physical_min=-1.0,
                    physical_max=1.0,
                    digital_min=-32768,
                    digital_max=32767,
                    prefilter="",
                    samples_per_record=annotation_samples,
                )
            )
            annotation_values: list[int] = []
            for payload in annotation_records:
                padded = payload.ljust(annotation_samples * 2, b"\0")
                annotation_values.extend(
                    struct.unpack("<" + "h" * annotation_samples, padded)
                )
            digital_arrays.append(annotation_values)

    edf = EDFFile(
        version="0",
        patient_id="X X X X",
        recording_id="Startdate X X X WinEEG-reference",
        start_date="01.01.85",
        start_time="00.00.00",
        reserved="EDF+C" if annotations else "",
        data_record_count=record_count,
        data_record_duration=record_duration,
        signals=signals,
        digital_samples=digital_arrays,
        annotations=annotations,
    )
    write_edf(destination, edf)
    return {
        "format": "wineeg_legacy_to_edf_conversion",
        "identity_policy": "legacy profile identity not decoded or copied; EDF identity fields are fixed de-identified values",
        "event_policy": event_policy,
        "frame_policy": "physical" if physical_frames else "logical_clamped_to_physical",
        "source_logical_frames": eeg.header["logical_frame_count"],
        "source_physical_frames": eeg.physical_frame_count,
        "converted_frames": frame_count,
        "channel_count": len(eeg.channels),
        "annotation_count": len(annotations),
        "annotation_signal_count": len(NATIVE_EDF_ANNOTATION_LANES) if annotations else 0,
        "annotation_lane_counts": {
            name: len(annotation_lanes[index])
            for index, name in enumerate(NATIVE_EDF_ANNOTATION_LANES)
        },
        "primary_mark_count": len(annotation_lanes[1]),
        "mapped_event_count": len(annotation_lanes[4]),
        "sample_rate_hz": rate,
        "data_record_count": record_count,
        "data_record_duration": record_duration,
        "sidecar_used": conversion_sidecar is not None,
        "sidecar_restoration": {
            "full_channel_labels": restored_full_channel_labels,
            "primary_event_labels": restored_primary_labels,
            "primary_event_durations": restored_primary_durations,
        },
    }


def _edf_physical_signal_arrays(edf: EDFFile) -> list[tuple[EDFSignal, list[float]]]:
    result: list[tuple[EDFSignal, list[float]]] = []
    for index, signal in enumerate(edf.signals):
        if signal.label.strip().lower() == EDF_ANNOTATION_LABEL.lower():
            continue
        digital_span = signal.digital_max - signal.digital_min
        if digital_span == 0:
            values = [float(signal.physical_min)] * len(edf.digital_samples[index])
        else:
            physical_span = signal.physical_max - signal.physical_min
            values = [
                (value - signal.digital_min) * physical_span / digital_span
                + signal.physical_min
                for value in edf.digital_samples[index]
            ]
        result.append((signal, values))
    return result


def _resample_physical_for_comparison(
    values: Sequence[float], source_rate: float, target_rate: float, count: int
) -> list[float]:
    if not values or count <= 0:
        return []
    if abs(source_rate - target_rate) < 1e-9:
        return list(values[:count])
    step = source_rate / target_rate
    result: list[float] = []
    for index in range(count):
        position = index * step
        left = min(int(position), len(values) - 1)
        right = min(left + 1, len(values) - 1)
        fraction = position - left
        result.append(values[left] * (1.0 - fraction) + values[right] * fraction)
    return result


def validate_reference_roundtrip(
    source_edf: Path,
    intermediate_eeg: Path,
    output_edf: Path,
    *,
    report: Path | None = None,
) -> dict[str, Any]:
    """Compare a reference EDF -> EEG -> EDF run without emitting identity or event text."""
    source = read_edf(source_edf)
    legacy = read_legacy_eeg(intermediate_eeg)
    output = read_edf(output_edf)
    source_annotation_signals = sum(
        signal.label.strip().lower() == EDF_ANNOTATION_LABEL.lower()
        for signal in source.signals
    )
    output_annotation_signals = sum(
        signal.label.strip().lower() == EDF_ANNOTATION_LABEL.lower()
        for signal in output.signals
    )
    source_event_records = [
        (annotation.lane_index, annotation.onset_seconds, annotation.duration_seconds, annotation.text)
        for annotation in source.annotations
    ]
    output_event_records = [
        (annotation.lane_index, annotation.onset_seconds, annotation.duration_seconds, annotation.text)
        for annotation in output.annotations
    ]
    source_arrays = _edf_physical_signal_arrays(source)
    output_arrays = _edf_physical_signal_arrays(output)
    comparisons: list[dict[str, Any]] = []
    for index, ((source_signal, source_values), (output_signal, output_values)) in enumerate(
        zip(source_arrays, output_arrays)
    ):
        source_rate = source_signal.samples_per_record / source.data_record_duration
        output_rate = output_signal.samples_per_record / output.data_record_duration
        expected = _resample_physical_for_comparison(
            source_values, source_rate, output_rate, len(output_values)
        )
        compared = min(len(expected), len(output_values))
        maximum_error = (
            max(abs(expected[i] - output_values[i]) for i in range(compared))
            if compared
            else None
        )
        comparisons.append(
            {
                "channel_index": index,
                "source_label": source_signal.label,
                "output_label": output_signal.label,
                "source_samples": len(source_values),
                "output_samples": len(output_values),
                "compared_samples": compared,
                "source_rate_hz": source_rate,
                "output_rate_hz": output_rate,
                "max_abs_physical_error": maximum_error,
                "physical_dimension": source_signal.physical_dimension,
            }
        )
    result = {
        "format": "wineeg_reference_edf_eeg_edf_validation",
        "privacy": "source filenames, EDF identity fields, and event text omitted",
        "source_signal_count": len(source_arrays),
        "output_signal_count": len(output_arrays),
        "source_annotation_count": len(source.annotations),
        "output_annotation_count": len(output.annotations),
        "source_annotation_signal_count": source_annotation_signals,
        "output_annotation_signal_count": output_annotation_signals,
        "source_annotation_lane_counts": {
            str(lane): sum(annotation.lane_index == lane for annotation in source.annotations)
            for lane in range(source_annotation_signals)
        },
        "output_annotation_lane_counts": {
            str(lane): sum(annotation.lane_index == lane for annotation in output.annotations)
            for lane in range(output_annotation_signals)
        },
        "event_onset_duration_text_and_lane_exact": source_event_records == output_event_records,
        "mapped_event_count": legacy.header["event_map_count"],
        "event_group_count": legacy.header["event_group_count"],
        "legacy_channel_count": legacy.header["channel_count"],
        "legacy_sample_rate_hz": legacy.header["sample_rate_hz"],
        "legacy_logical_frames": legacy.header["logical_frame_count"],
        "legacy_physical_frames": legacy.physical_frame_count,
        "legacy_identity_profile_zero_filled": not any(
            legacy.raw[LEGACY_PROFILE_OFFSET : LEGACY_PROFILE_OFFSET + LEGACY_PROFILE_SIZE]
        ),
        "output_edf_deidentified": output.patient_id == "X X X X"
        and output.recording_id == "Startdate X X X WinEEG-reference",
        "channels": comparisons,
    }
    if report is not None:
        report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    return result


def _write_c_header(path: Path) -> None:
    path.write_text(
        """/* Generated static contract; no driver or product dependency. */
#pragma once
#include <stdint.h>
#pragma pack(push, 1)
typedef struct WineegEegHeader {
  uint16_t magic;                    /* 0x00: 0x0f0e */
  uint16_t channel_count;            /* 0x02 */
  uint16_t sample_rate_hz;           /* 0x04 */
  uint16_t primary_mark_count;       /* 0x06 */
  uint16_t text_blob_size;           /* 0x08 */
  uint16_t sample_width_bytes;       /* 0x0a */
  uint16_t bits_per_sample;          /* 0x0c */
  uint16_t extended_mark_count;      /* 0x0e */
  uint32_t logical_frame_count;      /* 0x10 */
  uint32_t profile_offset;           /* 0x14 */
  uint32_t channel_table_offset;     /* 0x18 */
  uint32_t sample_data_offset;       /* 0x1c */
  uint32_t primary_marks_offset;     /* 0x20 */
  uint32_t extended_marks_offset;    /* 0x24 */
  uint32_t text_blob_offset;         /* 0x28 */
  uint16_t unresolved_2c, unresolved_2e, unresolved_30;
  uint16_t index_count;              /* 0x32 */
  uint32_t index_offset;             /* 0x34 */
  uint16_t text_line_count;          /* 0x38 */
  uint32_t montage_offset;           /* 0x3a, intentionally unaligned */
  uint16_t format_flags;             /* 0x3e */
  uint8_t unresolved_40_47[8];
  uint16_t alternate_block_count;    /* 0x48 */
  uint32_t alternate_block_offset;   /* 0x4a */
  uint16_t alternate_block_frames;   /* 0x4e */
  uint16_t event_map_count;          /* 0x50 */
  uint16_t event_group_count;        /* 0x52 */
  uint32_t event_map_offset;         /* 0x54 */
  uint32_t event_group_offset;       /* 0x58 */
  uint32_t settings_offset;          /* 0x5c */
  uint32_t aux_record_offset;        /* 0x60 */
  uint16_t aux_record_count;         /* 0x64 */
  uint16_t float_table_present;      /* 0x66 */
  uint32_t float_table_offset;       /* 0x68 */
  uint16_t view_descriptor_count;    /* 0x6c */
  uint32_t view_descriptor_offset;   /* 0x6e */
  uint16_t extra_descriptor_count;   /* 0x72 */
  uint32_t extra_descriptor_offset;  /* 0x74 */
  uint32_t optional_block_offset;    /* 0x78 */
  uint16_t optional_block_size;      /* 0x7c; presence gates a 0x8000 block */
  uint16_t unresolved_7e;
} WineegEegHeader;

typedef struct WineegChannelDescriptor {
  char label[8];                     /* 0x00 */
  int16_t source_code;               /* 0x08 */
  int16_t reference_code;            /* 0x0a */
  int16_t field_0c, field_0e, field_10;
  uint8_t unresolved_12_13[2];
  int16_t x, y;                      /* 0x14, 0x16 */
  float scale;                       /* 0x18 */
  int16_t kind;                      /* 0x1c */
  uint8_t unresolved_1e_25[8];
  float correction;                  /* 0x26 */
  float zero_offset;                 /* 0x2a */
  uint8_t unresolved_2e_3f[18];
} WineegChannelDescriptor;

typedef struct WineegPrimaryMark {
  uint32_t onset_ms;
  uint16_t label_length;
  char label[26];                    /* writer uses at most 24 plus NUL */
} WineegPrimaryMark;

typedef struct WineegExtendedMarkDisk {
  uint16_t type;                     /* 0x00 */
  uint32_t onset_ms;                 /* 0x02, intentionally unaligned */
  uint32_t duration_ms;              /* 0x06 for recovered type 0 */
  uint8_t unresolved_0a_0d[4];
  char payload[18];                  /* 0x0e */
} WineegExtendedMarkDisk;

typedef struct WineegEventGroupDescriptor {
  uint16_t entry_count;              /* 0x00 */
  uint32_t entries_file_offset;      /* 0x02, intentionally unaligned */
  uint16_t duration_ms;              /* 0x06 */
} WineegEventGroupDescriptor;

typedef struct WineegEventIndexRecord {
  uint32_t onset_ms;                 /* 0x00 */
  uint16_t lane_code;                /* 0x04; observed 4 for mapped events */
  uint16_t flags;                    /* 0x06 */
} WineegEventIndexRecord;

typedef struct WineegEventMapRecord {
  uint16_t group_index;              /* 0x00 */
  uint16_t event_code;               /* 0x02; exported as EDF lane-4 text */
  uint32_t reserved;                 /* 0x04; observed zero */
} WineegEventMapRecord;

typedef struct WineegEventGroupEntry {
  uint16_t relative_ms;              /* 0x00 */
  uint16_t stimulus_code;            /* 0x02 */
  uint32_t reserved;                 /* 0x04; observed zero */
} WineegEventGroupEntry;
#pragma pack(pop)

typedef char WineegEegHeader_size_must_be_0x80[(sizeof(WineegEegHeader) == 0x80) ? 1 : -1];
typedef char WineegChannelDescriptor_size_must_be_0x40[(sizeof(WineegChannelDescriptor) == 0x40) ? 1 : -1];
typedef char WineegPrimaryMark_size_must_be_0x20[(sizeof(WineegPrimaryMark) == 0x20) ? 1 : -1];
typedef char WineegExtendedMarkDisk_size_must_be_0x20[(sizeof(WineegExtendedMarkDisk) == 0x20) ? 1 : -1];
typedef char WineegEventGroupDescriptor_size_must_be_8[(sizeof(WineegEventGroupDescriptor) == 8) ? 1 : -1];
typedef char WineegEventIndexRecord_size_must_be_8[(sizeof(WineegEventIndexRecord) == 8) ? 1 : -1];
typedef char WineegEventMapRecord_size_must_be_8[(sizeof(WineegEventMapRecord) == 8) ? 1 : -1];
typedef char WineegEventGroupEntry_size_must_be_8[(sizeof(WineegEventGroupEntry) == 8) ? 1 : -1];
""",
        encoding="utf-8",
    )


def analyze_examples(root: Path, output: Path) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    files: list[Path] = []
    for path in sorted(root.rglob("*")):
        if path.is_file() and path.suffix.lower() == ".eeg":
            try:
                with path.open("rb") as handle:
                    prefix = handle.read(2)
                if path.stat().st_size >= LEGACY_HEADER_SIZE and len(prefix) == 2 and _unpack(prefix, 0, "H") == LEGACY_MAGIC:
                    files.append(path)
            except OSError:
                continue
    sample_rows: list[dict[str, Any]] = []
    channel_rows: list[dict[str, Any]] = []
    section_rows: list[dict[str, Any]] = []
    for ordinal, path in enumerate(files, 1):
        source_id = f"legacy_eeg_{ordinal:03d}"
        eeg = read_legacy_eeg(path)
        sample_rows.append(
            {
                "source_id": source_id,
                "file_size": len(eeg.raw),
                "channel_count": eeg.header["channel_count"],
                "sample_rate_hz": eeg.header["sample_rate_hz"],
                "sample_width_bytes": eeg.header["sample_width_bytes"],
                "logical_frames": eeg.header["logical_frame_count"],
                "physical_frames": eeg.physical_frame_count,
                "trailing_sample_bytes": eeg.trailing_sample_bytes,
                "frame_relation": "equal" if eeg.header["logical_frame_count"] == eeg.physical_frame_count else "mismatch",
            }
        )
        for channel in eeg.channels:
            channel_rows.append({"source_id": source_id, **asdict(channel)})
        for section in legacy_section_map(eeg):
            section_rows.append({"source_id": source_id, **section})

    def write_csv(name: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        with (output / name).open("w", newline="", encoding="utf-8") as handle:
            writer = csv.DictWriter(handle, fieldnames=list(rows[0]))
            writer.writeheader()
            writer.writerows(rows)

    write_csv("sample_layouts.csv", sample_rows)
    write_csv("channel_descriptors.csv", channel_rows)
    write_csv("section_maps.csv", section_rows)
    contract = {
        "format": "WinEEG legacy EEG",
        "magic": "0x0f0e",
        "related_distinct_erp_magic": "0x0e04",
        "endianness": "little",
        "header_size": LEGACY_HEADER_SIZE,
        "profile_policy": "identity-bearing 512-byte block; never decoded in generated analysis",
        "channel_descriptor_size": LEGACY_CHANNEL_DESCRIPTOR_SIZE,
        "sample_layout": "time-major frames, channel_count signed integers per frame",
        "sample_widths_read_bytes": [2, 3],
        "reference_writer_sample_width_bytes": 2,
        "sample_decode": "physical = (digital - zero_offset) * scale",
        "physical_frame_count": "(primary_marks_offset - sample_data_offset) / (channel_count * sample_width_bytes)",
        "logical_frame_policy": "report separately; reference export defaults to logical count clamped to physical count",
        "header_fields": [
            {"name": name, "offset": f"0x{offset:02x}", "type": fmt, "confidence": confidence}
            for name, offset, fmt, confidence in HEADER_FIELDS
        ],
        "fixed_blocks": {
            "profile": {"offset": 0x80, "size": 0x200},
            "montage": {"offset": 0x280, "size": 0x200},
            "settings": {"size": 0x840},
            "float_table": {"size": 0x40000, "optional": True},
            "optional_block": {"size": 0x8000, "optional": True},
        },
        "channel_descriptor_fields": [
            {"name": "label", "offset": "0x00", "type": "char[8]"},
            {"name": "source_code", "offset": "0x08", "type": "int16"},
            {"name": "reference_code", "offset": "0x0a", "type": "int16"},
            {"name": "x", "offset": "0x14", "type": "int16"},
            {"name": "y", "offset": "0x16", "type": "int16"},
            {"name": "scale", "offset": "0x18", "type": "float32"},
            {"name": "kind", "offset": "0x1c", "type": "int16"},
            {"name": "correction", "offset": "0x26", "type": "float32"},
            {"name": "zero_offset", "offset": "0x2a", "type": "float32"},
        ],
        "record_sizes": {
            "primary_mark": 0x20,
            "extended_mark_disk": 0x20,
            "view_descriptor": 0x40,
            "index": 8,
            "event_map": 8,
            "event_group": 8,
            "aux": 0x10,
        },
        "event_group_descriptor": {
            "entry_count": {"offset": 0, "type": "uint16"},
            "entries_file_offset": {"offset": 2, "type": "uint32"},
            "duration_ms": {"offset": 6, "type": "uint16"},
            "variable_entry_size": 8,
        },
        "trailer_serialization_order": [
            "primary marks",
            "optional alternate family",
            "view descriptors",
            "index records",
            "settings",
            "aux records",
            "text blob",
            "extended marks",
            "optional float table",
            "event maps",
            "event group descriptors and variable lists",
            "extra descriptors",
            "optional 0x8000 block",
            "rewrite header",
        ],
    }
    (output / "format_contract.json").write_text(json.dumps(contract, indent=2), encoding="utf-8")
    conversion = {
        "legacy_to_physical": "physical_uV = (stored_int - descriptor.zero_offset) * descriptor.scale",
        "edf_to_legacy_scale": "scale = unit_to_uV * (physical_max - physical_min) / (digital_max - digital_min)",
        "edf_to_legacy_zero": "zero = digital_min - (digital_max-digital_min)*physical_min/(physical_max-physical_min)",
        "unit_to_uV": {"V": 1000000.0, "mV": 1000.0, "uV": 1.0, "nV": 0.001, "pV": 0.000001},
        "native_edf_export_normal_channel": {
            "digital": [-32767, 32767],
            "physical_uV": [-8191.75, 8191.75],
            "digital_formula": "clamp(trunc(physical_uV * 4), -32767, 32767)",
        },
        "native_edf_export_label_channel": {
            "digital": [-32767, 32767],
            "physical": [-32767, 32767],
        },
        "native_edf_plus_annotation_signals": [
            "record timekeeping",
            "primary marks",
            "typed extended marks",
            "artifact intervals",
            "mapped/grouped events",
        ],
        "reference_converter_annotation_signals": 5,
        "annotation_lane_roles": list(NATIVE_EDF_ANNOTATION_LANES),
        "event_families": ["primary marks", "typed extended marks", "artifacts", "mapped/grouped events"],
        "import_rate_buckets_hz": [125, 250, 500, 1000, 2000],
        "import_rate_thresholds": [
            {"maximum_source_hz": 128, "destination_hz": 125},
            {"maximum_source_hz": 256, "destination_hz": 250},
            {"maximum_source_hz": 512, "destination_hz": 500},
            {"maximum_source_hz": 1024, "destination_hz": 1000},
            {"maximum_source_hz": None, "destination_hz": 2000},
        ],
        "reference_identity_policy": "zero legacy profile; fixed de-identified EDF identity fields",
        "reference_event_policies": ["redact", "preserve", "drop"],
        "known_loss_boundaries": [
            "legacy descriptor labels use seven ASCII bytes plus NUL",
            "primary marks retain onset and at most 24 label bytes, not generic EDF duration",
            "full labels and generic durations can be retained in an optional sidecar",
            "native event provenance is retained in five ordinal EDF annotation signals",
            "EDF mapped events do not retain the original same-class group-template selection; recovered representative groups are serialized",
            "reference writer emits the minimal 16-bit core and does not synthesize optional proprietary blocks",
        ],
    }
    (output / "conversion_contract.json").write_text(json.dumps(conversion, indent=2), encoding="utf-8")
    _write_c_header(output / "wineeg_legacy_eeg.h")
    (output / "README.md").write_text(
        """# WinEEG EEG/EDF Generated Contract

Generated by `tools/wineeg_eeg_edf.py analyze`.

The complete interpreted specification is `WINEEG_EEG_EDF_FORMAT_CONTRACT.md` at the workspace root.

- Source filenames and identity-bearing profile contents are omitted.
- Channel labels are retained because they define signal topology.
- Event text is not emitted by the analyzer.
- `logical_frames != physical_frames` is reported, not silently repaired.
- `outside_or_stale` means the stored pointer is absent, stale, or belongs to a variant not represented by the inferred section size.
- `wineeg_legacy_eeg.h` contains packed size-checked header, descriptor, primary/extended mark, and event-group structures.
""",
        encoding="utf-8",
    )
    return {"files": len(files), "output": str(output), "privacy": "identity and event text omitted"}


def inspect_file(path: Path, include_event_text: bool = False) -> dict[str, Any]:
    raw = path.read_bytes()
    if len(raw) >= 2 and _unpack(raw, 0, "H") == LEGACY_MAGIC:
        eeg = parse_legacy_eeg(raw)
        return {
            "format": "wineeg_legacy_eeg",
            "file_size": len(raw),
            "header": eeg.header,
            "physical_frame_count": eeg.physical_frame_count,
            "trailing_sample_bytes": eeg.trailing_sample_bytes,
            "channels": [asdict(channel) for channel in eeg.channels],
            "sections": legacy_section_map(eeg),
            "marks": [asdict(mark) for mark in parse_legacy_marks(eeg, include_text=include_event_text)],
            "mapped_event_count": eeg.header["event_map_count"],
            "mapped_events": [
                asdict(event) for event in parse_legacy_mapped_events(eeg)
            ] if include_event_text else "<structural records redacted>",
            "event_groups": [
                asdict(group) for group in parse_legacy_event_groups(eeg)
            ],
            "privacy": "legacy profile identity omitted",
        }
    edf = parse_edf(raw)
    annotation_signal_count = sum(
        signal.label.strip().lower() == EDF_ANNOTATION_LABEL.lower()
        for signal in edf.signals
    )
    return {
        "format": "edf_plus" if "EDF+" in edf.reserved else "edf",
        "file_size": len(raw),
        "patient_id": "<redacted>",
        "recording_id": "<redacted>",
        "start_date": "<redacted>",
        "start_time": "<redacted>",
        "data_record_count": edf.data_record_count,
        "data_record_duration": edf.data_record_duration,
        "signals": [asdict(signal) for signal in edf.signals],
        "annotation_count": len(edf.annotations),
        "annotation_signal_count": annotation_signal_count,
        "annotation_lane_counts": {
            str(lane): sum(annotation.lane_index == lane for annotation in edf.annotations)
            for lane in range(annotation_signal_count)
        },
        "annotations": [asdict(annotation) for annotation in edf.annotations]
        if include_event_text
        else "<redacted>",
    }


def main() -> int:
    workspace = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    inspect_parser = sub.add_parser("inspect", help="inspect one EEG or EDF without identity output")
    inspect_parser.add_argument("input", type=Path)
    inspect_parser.add_argument("--include-event-text", action="store_true")

    analyze_parser = sub.add_parser("analyze", help="generate the privacy-safe format contract")
    analyze_parser.add_argument("--root", type=Path, default=workspace / "WinEEG" / "examples")
    analyze_parser.add_argument(
        "--output", type=Path, default=workspace / "analysis_output" / "semantic" / "eeg_edf"
    )

    to_edf = sub.add_parser("eeg-to-edf", help="convert legacy EEG to de-identified EDF/EDF+")
    to_edf.add_argument("input", type=Path)
    to_edf.add_argument("output", type=Path)
    to_edf.add_argument("--event-policy", choices=("redact", "preserve", "drop"), default="redact")
    to_edf.add_argument("--physical-frames", action="store_true")

    to_eeg = sub.add_parser("edf-to-eeg", help="convert EDF/EDF+ to a synthetic-safe legacy EEG")
    to_eeg.add_argument("input", type=Path)
    to_eeg.add_argument("output", type=Path)
    to_eeg.add_argument("--event-policy", choices=("redact", "preserve", "drop"), default="redact")
    to_eeg.add_argument("--sidecar", type=Path)

    validate = sub.add_parser(
        "validate-roundtrip",
        help="compare EDF -> legacy EEG -> EDF samples without emitting identity or event text",
    )
    validate.add_argument("source_edf", type=Path)
    validate.add_argument("intermediate_eeg", type=Path)
    validate.add_argument("output_edf", type=Path)
    validate.add_argument("--report", type=Path)

    args = parser.parse_args()
    if args.command == "inspect":
        result = inspect_file(args.input.resolve(), args.include_event_text)
    elif args.command == "analyze":
        result = analyze_examples(args.root.resolve(), args.output.resolve())
    elif args.command == "eeg-to-edf":
        result = legacy_to_edf(
            args.input.resolve(),
            args.output.resolve(),
            event_policy=args.event_policy,
            physical_frames=args.physical_frames,
        )
    elif args.command == "edf-to-eeg":
        result = edf_to_legacy(
            args.input.resolve(),
            args.output.resolve(),
            event_policy=args.event_policy,
            sidecar=args.sidecar.resolve() if args.sidecar else None,
        )
    else:
        result = validate_reference_roundtrip(
            args.source_edf.resolve(),
            args.intermediate_eeg.resolve(),
            args.output_edf.resolve(),
            report=args.report.resolve() if args.report else None,
        )
    print(json.dumps(result, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
