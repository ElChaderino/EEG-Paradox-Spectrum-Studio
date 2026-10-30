"""Separate EEG spectrum/history viewer. GPL-3.0; see NOTICE.md."""

from .spectral import SpectrumConfig, SpectrumResult, analyze_array
from .adapters import analyze_raw, analyze_file
from .export import export_studio, refresh_studio
from .review import QualityConfig, attach_review, artifact_review_events, load_workspace
from .integrations import export_project_review, event_table_events, stage_events
from .runner import RunOptions,RunArtifacts,run_recording

__version__ = "0.6.8"
__all__ = ["SpectrumConfig", "SpectrumResult", "analyze_array", "analyze_raw", "analyze_file", "export_studio", "refresh_studio"]
__all__ += ["QualityConfig", "attach_review", "artifact_review_events", "load_workspace"]
__all__ += ["export_project_review", "event_table_events", "stage_events"]
__all__ += ["RunOptions","RunArtifacts","run_recording"]
