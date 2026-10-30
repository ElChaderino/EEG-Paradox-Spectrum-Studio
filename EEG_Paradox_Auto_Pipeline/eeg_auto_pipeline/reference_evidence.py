"""Conservative recording-reference evidence and derived-reference policy.

The recorded acquisition reference and the reference used for an analysis are
different facts.  File labels and headers may declare the former, but a saved
display montage is not proof.  A common-average analysis reference can be
derived from a simultaneous standard-19 recording without recovering the
original reference electrode signal.
"""
from __future__ import annotations

from collections import Counter
import re
from typing import Any, Iterable, Mapping


STANDARD_19 = {
    "FP1", "FP2", "F7", "F3", "FZ", "F4", "F8", "T7", "C3", "CZ",
    "C4", "T8", "P7", "P3", "PZ", "P4", "P8", "O1", "O2",
}
_LEGACY = {"T3": "T7", "T4": "T8", "T5": "P7", "T6": "P8"}
_EAR_LEFT = {"A1", "M1", "T9", "LE"}
_EAR_RIGHT = {"A2", "M2", "T10", "RE"}
_REFERENCE_SUFFIX = re.compile(
    r"(?:-|_|\s)(LINKED[_ ]?EARS?|LINKED[_ ]?MASTOIDS?|LE|AVG|AV|CAR|"
    r"AVERAGE[_ ]?REFERENCE|CZ|A1|A2|M1|M2|REF)$",
    re.IGNORECASE,
)


def normalize_reference(value: Any) -> str | None:
    token = re.sub(r"[^A-Z0-9]+", "_", str(value or "").strip().upper()).strip("_")
    if token in {"LINKED_EAR", "LINKED_EARS", "LINKED_MASTOID", "LINKED_MASTOIDS", "LE"}:
        return "linked_ears"
    if token in {"AVERAGE", "AVERAGE_REFERENCE", "COMMON_AVERAGE", "AVG", "AV", "CAR"}:
        return "average_reference"
    if token in {"CZ", "A1", "A2", "M1", "M2"}:
        return token.lower()
    return None


def _canonical_channel(label: Any) -> str | None:
    text = re.sub(r"^(?:EEG|POL)\s*", "", str(label or "").strip(), flags=re.IGNORECASE)
    text = _REFERENCE_SUFFIX.sub("", text).strip(" -_").upper()
    text = re.sub(r"[^A-Z0-9]", "", text)
    text = _LEGACY.get(text, text)
    return text if text in STANDARD_19 | _EAR_LEFT | _EAR_RIGHT else None


def _label_reference(label: Any) -> str | None:
    match = _REFERENCE_SUFFIX.search(str(label or "").strip())
    return normalize_reference(match.group(1)) if match else None


def _metadata_reference(fields: Mapping[str, Any]) -> tuple[str | None, list[str]]:
    candidates: list[tuple[str, str]] = []
    pattern = re.compile(
        r"\b(?:RECORDING|ACQUISITION|ANALYSIS|EEG)?\s*REF(?:ERENCE)?\s*[:=]\s*"
        r"(LINKED\s+(?:EARS?|MASTOIDS?)|COMMON\s+AVERAGE|AVERAGE\s+REFERENCE|"
        r"CAR|AVG|AV|CZ|A1|A2|M1|M2)\b",
        re.IGNORECASE,
    )
    for key, value in fields.items():
        match = pattern.search(str(value or ""))
        normalized = normalize_reference(match.group(1)) if match else None
        if normalized:
            candidates.append((str(key), normalized))
    distinct = sorted({value for _, value in candidates})
    return (distinct[0] if len(distinct) == 1 else None,
            [key for key, _ in candidates])


def build_reference_evidence(
    channel_names: Iterable[Any],
    *,
    canonical_channel_names: Iterable[Any] | None = None,
    metadata_fields: Mapping[str, Any] | None = None,
    upstream_hint: Any = None,
    saved_display_montage_label: Any = None,
    descriptor_reference_codes: Iterable[Any] | None = None,
) -> dict[str, Any]:
    """Return auditable evidence without claiming recovery of a missing signal."""
    labels = [str(value) for value in channel_names]
    canonical = {
        value for value in (
            _canonical_channel(item)
            for item in (list(canonical_channel_names) if canonical_channel_names is not None else labels)
        ) if value
    }
    standard_19_complete = STANDARD_19.issubset(canonical)
    left_ear_present = bool(canonical & _EAR_LEFT)
    right_ear_present = bool(canonical & _EAR_RIGHT)

    suffixes = [_label_reference(label) for label in labels]
    suffix_counts = Counter(value for value in suffixes if value)
    label_candidate = None
    label_ambiguous = len(suffix_counts) > 1
    if len(suffix_counts) == 1:
        candidate, count = next(iter(suffix_counts.items()))
        # A single label can be an auxiliary derivation. Require repeat evidence.
        if count >= 2 and count >= max(2, len(labels) * 0.75):
            label_candidate = candidate

    metadata_candidate, metadata_sources = _metadata_reference(metadata_fields or {})
    hint_candidate = normalize_reference(upstream_hint)
    evidence: list[dict[str, Any]] = []
    if suffix_counts:
        evidence.append({
            "source": "signal_labels",
            "classification": "explicit_derivation_suffix",
            "counts": dict(sorted(suffix_counts.items())),
            "strength": "strong" if label_candidate else "ambiguous_or_insufficient",
        })
    if metadata_sources:
        evidence.append({
            "source": "recording_header",
            "classification": "explicit_reference_field",
            "fields": metadata_sources,
            "strength": "declared_metadata",
        })
    if hint_candidate:
        evidence.append({
            "source": "upstream_header_parser",
            "classification": "reference_hint",
            "value": hint_candidate,
            "strength": "supporting",
        })
    if saved_display_montage_label:
        evidence.append({
            "source": "saved_display_montage",
            "classification": "non_authoritative_display_setting",
            "present": True,
            "strength": "not_acquisition_reference_proof",
        })
    codes = list(descriptor_reference_codes or [])
    if codes:
        evidence.append({
            "source": "native_descriptor_reference_codes",
            "classification": "unmapped_numeric_codes",
            "codes": codes,
            "strength": "not_interpreted_without_proven_mapping",
        })

    candidates = [value for value in (label_candidate, metadata_candidate, hint_candidate) if value]
    distinct = sorted(set(candidates))
    if label_ambiguous or len(distinct) > 1:
        status, acquisition = "ambiguous", None
    elif label_candidate:
        status, acquisition = "declared_in_signal_labels", label_candidate
    elif metadata_candidate:
        status, acquisition = "declared_in_recording_metadata", metadata_candidate
    elif hint_candidate:
        status, acquisition = "header_parser_hint_requires_review", hint_candidate
    else:
        status, acquisition = "unknown", None

    car_available = standard_19_complete and status != "ambiguous"
    linked_ears_available = left_ear_present and right_ear_present
    return {
        "schema": "eeg-paradox.reference-evidence.v1",
        "acquisition_reference": {
            "status": status,
            "value": acquisition,
            "original_reference_signal_recovered": False,
        },
        "montage": {
            "standard_19_complete": standard_19_complete,
            "canonical_channels": sorted(canonical),
            "left_ear_or_mastoid_present": left_ear_present,
            "right_ear_or_mastoid_present": right_ear_present,
        },
        "analysis_options": {
            "common_average": {
                "available": car_available,
                "profile": "average_reference",
                "kind": "derived_analysis_reference",
                "source_samples_unchanged": True,
                "notice": (
                    "Common average can be derived across the simultaneous standard-19 set. "
                    "This cancels a shared reference term but does not identify or reconstruct "
                    "the original reference electrode signal."
                ),
            },
            "linked_ears": {
                "available": linked_ears_available,
                "profile": "linked_ears",
                "kind": "requires_recorded_bilateral_ear_or_mastoid_signals",
                "notice": (
                    "Linked ears cannot be reconstructed when bilateral ear/mastoid signals "
                    "are absent."
                ),
            },
            "cuban_wave1": {
                "available": standard_19_complete,
                "analysis_reference": "average_reference_gsf",
                "gsf_role": "global_power_scale_normalization_not_reference_recovery",
                "classification": "normative_output_subject_to_provider_applicability_and_review",
            },
            "neuromod_average_reference": {
                "available": car_available,
                "analysis_reference": "average_reference",
                "classification": "WIP / Research Review",
                "normative_or_clinical_release": False,
            },
        },
        "evidence": evidence,
        "automatic_policy": (
            "derive_common_average_for_research" if car_available and acquisition is None
            else "use_declared_or_reviewed_reference_with_provenance" if acquisition
            else "stop_for_reference_review"
        ),
    }


__all__ = ["STANDARD_19", "build_reference_evidence", "normalize_reference"]
