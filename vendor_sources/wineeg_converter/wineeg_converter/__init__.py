"""Direct EDF/EDF+ <-> proprietary WinEEG EEG conversion."""

from .service import (
    ConversionError,
    convert,
    convert_batch,
    convert_edf_to_eeg,
    convert_eeg_to_edf,
    detect_format,
    inspect_safe,
    validate_roundtrip,
)

__version__ = "3.2.0"

__all__ = [
    "ConversionError",
    "convert",
    "convert_batch",
    "convert_edf_to_eeg",
    "convert_eeg_to_edf",
    "detect_format",
    "inspect_safe",
    "validate_roundtrip",
]
