"""
BXS Processor Utilities
Comprehensive error handling, logging, file validation, and helper functions.

Copyright (C) 2025 EEG Paradox Clinical System Contributors
Licensed under GNU General Public License v3.0
"""

import logging
import sys
from pathlib import Path
from typing import Optional, Union, Dict, Any
from datetime import datetime
import json
import pickle

# ============================================================================
# Error Handling Classes
# ============================================================================

class BXSProcessorError(Exception):
    """Base exception for all BXS processor errors."""
    pass

class ConversionError(BXSProcessorError):
    """Raised when BXS to EDF conversion fails."""
    pass

class ValidationError(BXSProcessorError):
    """Raised when validation fails."""
    pass

class FormatError(BXSProcessorError):
    """Raised when file format is invalid or unrecognized."""
    pass

class SegmentDetectionError(BXSProcessorError):
    """Raised when segment detection fails."""
    pass

class MetadataError(BXSProcessorError):
    """Raised when metadata parsing or injection fails."""
    pass

# ============================================================================
# Logging Configuration
# ============================================================================

def setup_logging(
    level: int = logging.INFO,
    log_file: Optional[Union[str, Path]] = None,
    module_name: str = 'bxs_processor'
) -> logging.Logger:
    """
    Set up comprehensive logging for the BXS processor module.
    
    Args:
        level: Logging level (default: INFO)
        log_file: Optional log file path
        module_name: Name of the logger
        
    Returns:
        Configured logger instance
    """
    logger = logging.getLogger(module_name)
    logger.setLevel(level)
    
    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()
    
    # Console handler
    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setLevel(level)
    console_formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    console_handler.setFormatter(console_formatter)
    logger.addHandler(console_handler)
    
    # File handler (if specified)
    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_path, encoding='utf-8')
        file_handler.setLevel(logging.DEBUG)  # More verbose in file
        file_formatter = logging.Formatter(
            '%(asctime)s [%(levelname)s] %(name)s.%(funcName)s:%(lineno)d: %(message)s',
            datefmt='%Y-%m-%d %H:%M:%S'
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)
    
    return logger

# ============================================================================
# File Validation Utilities
# ============================================================================

def validate_file_path(
    file_path: Union[str, Path],
    must_exist: bool = True,
    must_be_file: bool = True,
    extensions: Optional[list] = None
) -> Path:
    """
    Validate a file path and return Path object.
    
    Args:
        file_path: Path to validate
        must_exist: Whether file must exist
        must_be_file: Whether path must be a file (not directory)
        extensions: Optional list of allowed extensions (e.g., ['.bxs', '.edf'])
        
    Returns:
        Validated Path object
        
    Raises:
        ValidationError: If validation fails
    """
    path = Path(file_path)
    
    if must_exist and not path.exists():
        raise ValidationError(f"File does not exist: {path}")
    
    if must_be_file and path.exists() and not path.is_file():
        raise ValidationError(f"Path is not a file: {path}")
    
    if extensions and path.suffix.lower() not in [ext.lower() for ext in extensions]:
        raise ValidationError(
            f"File extension '{path.suffix}' not in allowed extensions: {extensions}"
        )
    
    return path

def validate_directory(
    dir_path: Union[str, Path],
    must_exist: bool = False,
    create_if_missing: bool = True
) -> Path:
    """
    Validate a directory path and optionally create it.
    
    Args:
        dir_path: Directory path to validate
        must_exist: Whether directory must exist
        create_if_missing: Whether to create directory if it doesn't exist
        
    Returns:
        Validated Path object
        
    Raises:
        ValidationError: If validation fails
    """
    path = Path(dir_path)
    
    if path.exists() and not path.is_dir():
        raise ValidationError(f"Path exists but is not a directory: {path}")
    
    if not path.exists():
        if must_exist:
            raise ValidationError(f"Directory does not exist: {path}")
        elif create_if_missing:
            path.mkdir(parents=True, exist_ok=True)
    
    return path

# ============================================================================
# Progress Tracking Utilities
# ============================================================================

class ProgressTracker:
    """Track progress of batch operations."""
    
    def __init__(self, total: int, description: str = "Processing"):
        self.total = total
        self.current = 0
        self.description = description
        self.start_time = datetime.now()
        self.logger = logging.getLogger(__name__)
        
    def update(self, increment: int = 1, message: Optional[str] = None):
        """Update progress."""
        self.current += increment
        percentage = (self.current / self.total) * 100 if self.total > 0 else 0
        elapsed = (datetime.now() - self.start_time).total_seconds()
        
        if message:
            self.logger.info(f"{self.description}: {message}")
        
        self.logger.info(
            f"{self.description}: {self.current}/{self.total} ({percentage:.1f}%) "
            f"| Elapsed: {elapsed:.1f}s"
        )
    
    def finish(self):
        """Mark progress as complete."""
        elapsed = (datetime.now() - self.start_time).total_seconds()
        self.logger.info(
            f"{self.description}: Complete! {self.current}/{self.total} "
            f"| Total time: {elapsed:.1f}s"
        )

# ============================================================================
# Checkpoint/Resume Functionality
# ============================================================================

def save_checkpoint(
    checkpoint_file: Union[str, Path],
    data: Dict[str, Any]
) -> None:
    """
    Save checkpoint data to file.
    
    Args:
        checkpoint_file: Path to checkpoint file
        data: Dictionary of checkpoint data
    """
    checkpoint_path = Path(checkpoint_file)
    checkpoint_path.parent.mkdir(parents=True, exist_ok=True)
    
    # Add timestamp
    data['checkpoint_timestamp'] = datetime.now().isoformat()
    
    with open(checkpoint_path, 'wb') as f:
        pickle.dump(data, f)

def load_checkpoint(
    checkpoint_file: Union[str, Path]
) -> Optional[Dict[str, Any]]:
    """
    Load checkpoint data from file.
    
    Args:
        checkpoint_file: Path to checkpoint file
        
    Returns:
        Checkpoint data dictionary or None if file doesn't exist
    """
    checkpoint_path = Path(checkpoint_file)
    
    if not checkpoint_path.exists():
        return None
    
    try:
        with open(checkpoint_path, 'rb') as f:
            data = pickle.load(f)
        return data
    except Exception as e:
        logging.getLogger(__name__).warning(
            f"Failed to load checkpoint {checkpoint_path}: {e}"
        )
        return None

def clear_checkpoint(checkpoint_file: Union[str, Path]) -> None:
    """Clear checkpoint file."""
    checkpoint_path = Path(checkpoint_file)
    if checkpoint_path.exists():
        checkpoint_path.unlink()

# ============================================================================
# Data Validation Helpers
# ============================================================================

def validate_segment_durations(
    durations: list,
    expected_count: int = 3,
    min_duration: float = 1.0,
    max_duration: float = 300.0
) -> bool:
    """
    Validate segment durations.
    
    Args:
        durations: List of segment durations in seconds
        expected_count: Expected number of segments
        min_duration: Minimum allowed duration
        max_duration: Maximum allowed duration
        
    Returns:
        True if valid
        
    Raises:
        ValidationError: If validation fails
    """
    if len(durations) != expected_count:
        raise ValidationError(
            f"Expected {expected_count} segments, got {len(durations)}"
        )
    
    for i, duration in enumerate(durations):
        if not isinstance(duration, (int, float)):
            raise ValidationError(f"Segment {i} duration must be numeric")
        if duration < min_duration:
            raise ValidationError(
                f"Segment {i} duration {duration}s is below minimum {min_duration}s"
            )
        if duration > max_duration:
            raise ValidationError(
                f"Segment {i} duration {duration}s exceeds maximum {max_duration}s"
            )
    
    return True

def validate_segment_labels(
    labels: list,
    expected_count: int = 3,
    allowed_labels: Optional[list] = None
) -> bool:
    """
    Validate segment labels.
    
    Args:
        labels: List of segment labels
        expected_count: Expected number of segments
        allowed_labels: Optional list of allowed label values
        
    Returns:
        True if valid
        
    Raises:
        ValidationError: If validation fails
    """
    if len(labels) != expected_count:
        raise ValidationError(
            f"Expected {expected_count} labels, got {len(labels)}"
        )
    
    if allowed_labels:
        for i, label in enumerate(labels):
            if label not in allowed_labels:
                raise ValidationError(
                    f"Segment {i} label '{label}' not in allowed labels: {allowed_labels}"
                )
    
    return True

# ============================================================================
# Configuration Loading
# ============================================================================

def load_config(config_file: Optional[Union[str, Path]] = None) -> Dict[str, Any]:
    """
    Load configuration from YAML file.
    
    Args:
        config_file: Path to config file (default: config.yaml in module directory)
        
    Returns:
        Configuration dictionary
    """
    import yaml
    
    if config_file is None:
        config_file = Path(__file__).parent / 'config.yaml'
    
    config_path = Path(config_file)
    
    if not config_path.exists():
        logging.getLogger(__name__).warning(
            f"Config file not found: {config_path}, using defaults"
        )
        return {}
    
    try:
        with open(config_path, 'r', encoding='utf-8') as f:
            config = yaml.safe_load(f)
        return config or {}
    except Exception as e:
        logging.getLogger(__name__).error(f"Failed to load config: {e}")
        return {}

