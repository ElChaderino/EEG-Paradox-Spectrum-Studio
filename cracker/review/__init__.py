"""Human-reviewable EEG artifact annotations and quality summaries."""

from .artifact_annotations import (
    ArtifactAnnotation,
    ArtifactReviewSidecar,
    apply_accepted_annotations,
    propose_artifacts,
    review_reliability,
)

__all__ = [
    "ArtifactAnnotation",
    "ArtifactReviewSidecar",
    "apply_accepted_annotations",
    "propose_artifacts",
    "review_reliability",
]
