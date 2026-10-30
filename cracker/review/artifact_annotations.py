"""Persistent, non-destructive artifact review for continuous EEG.

Automatic detections are proposals.  They affect analysis only after a human
marks them accepted.  Each saved revision records the input hash, algorithm,
thresholds, operator, parent revision and an annotation-content hash.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
from typing import Any, Dict, Iterable, List, Mapping, Optional, Sequence, Tuple
import uuid

import numpy as np
from scipy.signal import welch


SCHEMA = "eeg-paradox.artifact-review.v1"
ALGORITHM_VERSION = "artifact-proposals-1.0.0"

DEFAULT_THRESHOLDS: Dict[str, float] = {
    "window_s": 1.0,
    "step_s": 0.5,
    "flatline_std_v": 0.10e-6,
    "high_amplitude_v": 150e-6,
    "discontinuity_v": 100e-6,
    "blink_eog_v": 100e-6,
    "line_noise_ratio": 0.20,
    "emg_ratio": 0.50,
    "clipping_identical_fraction": 0.10,
}


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _canonical_hash(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


@dataclass(frozen=True)
class ArtifactAnnotation:
    onset_s: float
    duration_s: float
    reason: str
    channels: Tuple[str, ...] = ()
    status: str = "proposed"
    source: str = "automatic"
    confidence: Optional[float] = None
    annotation_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    operator: Optional[str] = None
    reviewed_utc: Optional[str] = None
    notes: str = ""

    def __post_init__(self) -> None:
        if not np.isfinite(self.onset_s) or self.onset_s < 0:
            raise ValueError("annotation onset_s must be finite and non-negative")
        if not np.isfinite(self.duration_s) or self.duration_s <= 0:
            raise ValueError("annotation duration_s must be finite and positive")
        if self.status not in {"proposed", "accepted", "rejected"}:
            raise ValueError(f"invalid annotation status: {self.status}")
        if not str(self.reason).strip():
            raise ValueError("annotation reason is required")

    def to_dict(self) -> Dict[str, Any]:
        payload = asdict(self)
        payload["channels"] = list(self.channels)
        return payload

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ArtifactAnnotation":
        values = dict(payload)
        values["channels"] = tuple(values.get("channels") or ())
        return cls(**values)


@dataclass
class ArtifactReviewSidecar:
    input_file: str
    input_sha256: str
    recording_duration_s: float
    sfreq: float
    channel_names: List[str]
    annotations: List[ArtifactAnnotation] = field(default_factory=list)
    revision_id: str = field(default_factory=lambda: str(uuid.uuid4()))
    parent_revision_id: Optional[str] = None
    created_utc: str = field(default_factory=_utc_now)
    operator: Optional[str] = None
    algorithm_version: str = ALGORITHM_VERSION
    thresholds: Dict[str, float] = field(default_factory=lambda: dict(DEFAULT_THRESHOLDS))
    raw_input_modified: bool = False
    schema: str = SCHEMA

    @property
    def annotation_hash(self) -> str:
        return _canonical_hash([item.to_dict() for item in self.annotations])

    def to_dict(self) -> Dict[str, Any]:
        return {
            "schema": self.schema,
            "revision_id": self.revision_id,
            "parent_revision_id": self.parent_revision_id,
            "created_utc": self.created_utc,
            "operator": self.operator,
            "input_file": self.input_file,
            "input_sha256": self.input_sha256,
            "recording_duration_s": self.recording_duration_s,
            "sfreq": self.sfreq,
            "channel_names": list(self.channel_names),
            "algorithm_version": self.algorithm_version,
            "thresholds": dict(self.thresholds),
            "raw_input_modified": self.raw_input_modified,
            "annotation_hash": self.annotation_hash,
            "annotations": [item.to_dict() for item in self.annotations],
        }

    @classmethod
    def from_dict(cls, payload: Mapping[str, Any]) -> "ArtifactReviewSidecar":
        if payload.get("schema") != SCHEMA:
            raise ValueError(f"unsupported artifact sidecar schema: {payload.get('schema')}")
        sidecar = cls(
            input_file=str(payload["input_file"]),
            input_sha256=str(payload["input_sha256"]),
            recording_duration_s=float(payload["recording_duration_s"]),
            sfreq=float(payload["sfreq"]),
            channel_names=list(payload["channel_names"]),
            annotations=[ArtifactAnnotation.from_dict(item) for item in payload.get("annotations", [])],
            revision_id=str(payload["revision_id"]),
            parent_revision_id=payload.get("parent_revision_id"),
            created_utc=str(payload["created_utc"]),
            operator=payload.get("operator"),
            algorithm_version=str(payload.get("algorithm_version", ALGORITHM_VERSION)),
            thresholds={str(k): float(v) for k, v in payload.get("thresholds", {}).items()},
            raw_input_modified=bool(payload.get("raw_input_modified", False)),
        )
        if payload.get("annotation_hash") and payload["annotation_hash"] != sidecar.annotation_hash:
            raise ValueError("artifact sidecar annotation hash mismatch")
        if sidecar.raw_input_modified:
            raise ValueError("artifact sidecar claims destructive raw modification")
        return sidecar

    @classmethod
    def load(cls, path: Path, *, expected_input: Optional[Path] = None) -> "ArtifactReviewSidecar":
        payload = json.loads(Path(path).read_text(encoding="utf-8"))
        sidecar = cls.from_dict(payload)
        if expected_input is not None and sha256_file(expected_input) != sidecar.input_sha256:
            raise ValueError("artifact sidecar does not match the selected input file")
        return sidecar

    def save(self, path: Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(self.to_dict(), indent=2), encoding="utf-8")
        return path

    def review(
        self,
        decisions: Mapping[str, str],
        *,
        operator: str,
        notes: Optional[Mapping[str, str]] = None,
    ) -> "ArtifactReviewSidecar":
        revised: List[ArtifactAnnotation] = []
        reviewed_at = _utc_now()
        for item in self.annotations:
            status = decisions.get(item.annotation_id, item.status)
            if status not in {"proposed", "accepted", "rejected"}:
                raise ValueError(f"invalid review decision: {status}")
            payload = item.to_dict()
            payload.update({
                "status": status,
                "operator": operator if status != "proposed" else item.operator,
                "reviewed_utc": reviewed_at if status != "proposed" else item.reviewed_utc,
                "notes": (notes or {}).get(item.annotation_id, item.notes),
            })
            revised.append(ArtifactAnnotation.from_dict(payload))
        return ArtifactReviewSidecar(
            input_file=self.input_file,
            input_sha256=self.input_sha256,
            recording_duration_s=self.recording_duration_s,
            sfreq=self.sfreq,
            channel_names=list(self.channel_names),
            annotations=revised,
            parent_revision_id=self.revision_id,
            operator=operator,
            algorithm_version=self.algorithm_version,
            thresholds=dict(self.thresholds),
        )


def _merge_spans(spans: Iterable[ArtifactAnnotation], gap_s: float = 0.05) -> List[ArtifactAnnotation]:
    ordered = sorted(spans, key=lambda item: (item.reason, item.channels, item.onset_s))
    merged: List[ArtifactAnnotation] = []
    for item in ordered:
        if (
            merged
            and merged[-1].reason == item.reason
            and merged[-1].channels == item.channels
            and item.onset_s <= merged[-1].onset_s + merged[-1].duration_s + gap_s
        ):
            previous = merged.pop()
            end = max(previous.onset_s + previous.duration_s, item.onset_s + item.duration_s)
            merged.append(ArtifactAnnotation(
                onset_s=previous.onset_s,
                duration_s=end - previous.onset_s,
                reason=previous.reason,
                channels=previous.channels,
                confidence=max(previous.confidence or 0.0, item.confidence or 0.0),
            ))
        else:
            merged.append(item)
    return sorted(merged, key=lambda item: (item.onset_s, item.reason, item.channels))


def _band_power(freqs: np.ndarray, psd: np.ndarray, low: float, high: float) -> float:
    mask = (freqs >= low) & (freqs <= high)
    return float(np.trapz(psd[mask], freqs[mask])) if np.count_nonzero(mask) > 1 else 0.0


def propose_artifacts(
    data: np.ndarray,
    channel_names: Sequence[str],
    sfreq: float,
    *,
    input_file: str = "memory",
    input_sha256: Optional[str] = None,
    thresholds: Optional[Mapping[str, float]] = None,
) -> ArtifactReviewSidecar:
    """Generate conservative artifact proposals without changing input samples."""

    samples = np.asarray(data, dtype=np.float64)
    if samples.ndim != 2 or samples.shape[0] != len(channel_names):
        raise ValueError("data must have shape (len(channel_names), n_samples)")
    if not np.isfinite(sfreq) or sfreq <= 0:
        raise ValueError("sfreq must be positive and finite")
    limits = {**DEFAULT_THRESHOLDS, **dict(thresholds or {})}
    window = max(8, int(round(limits["window_s"] * sfreq)))
    step = max(1, int(round(limits["step_s"] * sfreq)))
    proposals: List[ArtifactAnnotation] = []

    for channel_index, channel in enumerate(channel_names):
        signal = samples[channel_index]
        for start in range(0, max(1, signal.size - window + 1), step):
            stop = min(signal.size, start + window)
            chunk = signal[start:stop]
            if chunk.size < 8 or not np.all(np.isfinite(chunk)):
                reason, confidence = "nonfinite_or_missing", 1.0
            elif float(np.std(chunk)) < limits["flatline_std_v"]:
                reason, confidence = "flatline", 1.0
            elif float(np.max(np.abs(chunk))) > limits["high_amplitude_v"]:
                ratio = float(np.max(np.abs(chunk))) / limits["high_amplitude_v"]
                reason, confidence = "high_amplitude", min(1.0, ratio / 3.0)
            else:
                unique_fraction = len(np.unique(chunk)) / chunk.size
                if unique_fraction <= 1.0 - limits["clipping_identical_fraction"]:
                    reason, confidence = "clipping_or_quantization", 0.85
                else:
                    nperseg = min(chunk.size, max(16, int(round(sfreq))))
                    freqs, psd = welch(chunk, sfreq, nperseg=nperseg, scaling="density")
                    total = _band_power(freqs, psd, 1.0, min(45.0, sfreq / 2.0 - 0.01))
                    line = max(
                        _band_power(freqs, psd, 49.0, 51.0),
                        _band_power(freqs, psd, 59.0, 61.0),
                    )
                    emg = _band_power(freqs, psd, 30.0, min(45.0, sfreq / 2.0 - 0.01))
                    if total > 0 and line / total > limits["line_noise_ratio"]:
                        reason, confidence = "line_noise", min(1.0, line / total)
                    elif total > 0 and emg / total > limits["emg_ratio"]:
                        reason, confidence = "emg", min(1.0, emg / total)
                    elif "EOG" in str(channel).upper() and np.max(np.abs(chunk)) > limits["blink_eog_v"]:
                        reason, confidence = "blink_eog", 0.90
                    elif chunk.size > 1 and np.max(np.abs(np.diff(chunk))) > limits["discontinuity_v"]:
                        reason, confidence = "discontinuity", 0.95
                    else:
                        continue
            proposals.append(ArtifactAnnotation(
                onset_s=start / sfreq,
                duration_s=(stop - start) / sfreq,
                reason=reason,
                channels=(str(channel),),
                confidence=confidence,
            ))

    duration = samples.shape[1] / sfreq
    digest = input_sha256 or hashlib.sha256(samples.tobytes(order="C")).hexdigest()
    return ArtifactReviewSidecar(
        input_file=input_file,
        input_sha256=digest,
        recording_duration_s=duration,
        sfreq=float(sfreq),
        channel_names=list(channel_names),
        annotations=_merge_spans(proposals, gap_s=limits["step_s"] + 1e-12),
        thresholds=limits,
    )


def apply_accepted_annotations(raw: Any, sidecar: ArtifactReviewSidecar) -> Any:
    """Attach accepted BAD_* spans to a Raw copy; source samples are untouched."""

    accepted = [item for item in sidecar.annotations if item.status == "accepted"]
    copied = raw.copy() if hasattr(raw, "copy") else raw
    try:
        if getattr(copied, "_eeg_paradox_simple_raw", False):
            raise TypeError("SimpleRaw uses the neutral annotation representation")
        import mne

        existing = copied.annotations
        annotations = mne.Annotations(
            onset=[item.onset_s for item in accepted],
            duration=[item.duration_s for item in accepted],
            description=[f"BAD_{item.reason}" for item in accepted],
            ch_names=[list(item.channels) for item in accepted],
        )
        copied.set_annotations(existing + annotations)
    except Exception:
        existing = list(getattr(copied, "annotations", []) or [])
        existing.extend({
            "onset": item.onset_s,
            "duration": item.duration_s,
            "description": f"BAD_{item.reason}",
            "channels": list(item.channels),
        } for item in accepted)
        copied.set_annotations(existing)
    copied._eeg_paradox_artifact_review = {
        "revision_id": sidecar.revision_id,
        "annotation_hash": sidecar.annotation_hash,
        "accepted_count": len(accepted),
        "raw_input_modified": False,
    }
    return copied


def _mask_for(sidecar: ArtifactReviewSidecar, channel: str) -> np.ndarray:
    n_samples = max(0, int(round(sidecar.recording_duration_s * sidecar.sfreq)))
    keep = np.ones(n_samples, dtype=bool)
    for item in sidecar.annotations:
        if item.status != "accepted" or (item.channels and channel not in item.channels):
            continue
        start = max(0, int(np.floor(item.onset_s * sidecar.sfreq)))
        stop = min(n_samples, int(np.ceil((item.onset_s + item.duration_s) * sidecar.sfreq)))
        keep[start:stop] = False
    return keep


def review_reliability(
    data: np.ndarray,
    sidecar: ArtifactReviewSidecar,
    *,
    comparison_sidecar: Optional[ArtifactReviewSidecar] = None,
    minimum_clean_s: float = 60.0,
    bootstrap_iterations: int = 500,
    random_seed: int = 17,
) -> Dict[str, Any]:
    """Compute clean coverage, split-half spectral stability and review agreement."""

    samples = np.asarray(data, dtype=float)
    if samples.shape[0] != len(sidecar.channel_names):
        raise ValueError("sidecar channel list does not match data")
    clean_seconds: Dict[str, float] = {}
    feature_pairs: List[Tuple[float, float]] = []
    bands = ((1.0, 4.0), (4.0, 8.0), (8.0, 13.0), (13.0, 30.0))
    for index, channel in enumerate(sidecar.channel_names):
        mask = _mask_for(sidecar, channel)
        usable = samples[index, :len(mask)][mask]
        clean_seconds[channel] = usable.size / sidecar.sfreq
        midpoint = usable.size // 2
        if midpoint < max(16, int(sidecar.sfreq)):
            continue
        halves = (usable[:midpoint], usable[midpoint:2 * midpoint])
        spectra = []
        for half in halves:
            freqs, psd = welch(half, sidecar.sfreq, nperseg=min(len(half), int(2 * sidecar.sfreq)))
            spectra.append([np.log10(max(_band_power(freqs, psd, lo, hi), np.finfo(float).tiny)) for lo, hi in bands])
        feature_pairs.extend(zip(spectra[0], spectra[1]))
    if len(feature_pairs) >= 2:
        first, second = np.asarray(feature_pairs, dtype=float).T
        split_half = float(np.corrcoef(first, second)[0, 1])
        rng = np.random.default_rng(random_seed)
        boots = []
        for _ in range(max(0, int(bootstrap_iterations))):
            take = rng.integers(0, len(first), len(first))
            if np.std(first[take]) > 0 and np.std(second[take]) > 0:
                boots.append(float(np.corrcoef(first[take], second[take])[0, 1]))
        ci = [float(np.percentile(boots, 2.5)), float(np.percentile(boots, 97.5))] if boots else [None, None]
    else:
        split_half, ci = None, [None, None]

    review_agreement = None
    if comparison_sidecar is not None:
        def accepted_grid(candidate: ArtifactReviewSidecar) -> set:
            grid = set()
            for channel in candidate.channel_names:
                rejected = np.flatnonzero(~_mask_for(candidate, channel))
                grid.update((channel, int(index)) for index in rejected)
            return grid
        left, right = accepted_grid(sidecar), accepted_grid(comparison_sidecar)
        union = left | right
        review_agreement = float(len(left & right) / len(union)) if union else 1.0

    minimum = min(clean_seconds.values(), default=0.0)
    accepted_reasons: Dict[str, float] = {}
    for item in sidecar.annotations:
        if item.status == "accepted":
            accepted_reasons[item.reason] = accepted_reasons.get(item.reason, 0.0) + item.duration_s
    return {
        "clean_duration_s_by_channel": clean_seconds,
        "minimum_clean_duration_s": minimum,
        "minimum_clean_data_required_s": float(minimum_clean_s),
        "minimum_clean_data_met": minimum >= minimum_clean_s,
        "accepted_duration_s_by_reason": accepted_reasons,
        "split_half_spectral_correlation": split_half,
        "split_half_bootstrap_ci95": ci,
        "bootstrap_iterations": int(bootstrap_iterations),
        "review_jaccard": review_agreement,
        "artifact_revision_id": sidecar.revision_id,
        "annotation_hash": sidecar.annotation_hash,
        "visual_signoff_required_for_clinical_norms": True,
        "visual_signoff_complete": bool(sidecar.operator) and not any(
            item.status == "proposed" for item in sidecar.annotations
        ),
    }
