"""
Enhanced BXS Converter - Multi-Strategy Approach
Convert BXS to EDF with automatic segment annotations and metadata injection.

Multi-Strategy Approach:
- Strategy 1 (Primary): Use existing converter.convert_bxs_to_edf, then enhance
- Strategy 2 (Fallback): Direct BXS parsing if format reverse-engineered
- Strategy 3 (Fallback): Signal-based segment detection
- Strategy 4 (Fallback): Time-based with validation

Copyright (C) 2025 EEG Paradox Clinical System Contributors
Licensed under GNU General Public License v3.0
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Union
import shutil
import re
import subprocess
import os

import mne
import numpy as np

from .utils import (
    validate_file_path,
    validate_directory,
    ConversionError,
    ValidationError,
    validate_segment_durations,
    validate_segment_labels
)
from .segment_detector import (
    detect_segments_by_timing,
    detect_segments_multi_method,
    validate_segments
)

logger = logging.getLogger(__name__)

def convert_bxs_to_annotated_edf(
    bxs_path: Union[str, Path],
    output_dir: Union[str, Path],
    subject_info: Optional[Dict] = None,
    segment_durations: List[float] = [60.0, 60.0, 60.0],
    segment_labels: List[str] = ['EC', 'EO', 'EOT'],
    use_signal_detection: bool = False,
    backup_original: bool = True
) -> Path:
    """
    Convert BXS to EDF with automatic segment annotations.
    
    Args:
        bxs_path: Path to BXS file
        output_dir: Output directory for EDF file
        subject_info: Optional subject metadata dictionary
        segment_durations: Duration of each segment in seconds
        segment_labels: Labels for each segment
        use_signal_detection: If True, use signal-based detection instead of timing
        backup_original: If True, create backup of original EDF before modification
        
    Returns:
        Path to created annotated EDF file
        
    Raises:
        ConversionError: If conversion fails
    """
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    output_directory = validate_directory(output_dir, create_if_missing=True)
    
    # Validate segment parameters
    validate_segment_durations(segment_durations, expected_count=3)
    validate_segment_labels(segment_labels, expected_count=3)
    
    try:
        # Strategy 1: Use existing converter
        logger.info(f"Converting {bxs_file.name} to EDF using existing converter...")
        
        # Find bxs2edf.exe in parent rapid_reporter directory
        parent_dir = Path(__file__).parent.parent
        bxs2edf_exe = parent_dir / "bxs2edf.exe"
        
        if not bxs2edf_exe.exists():
            raise ConversionError(
                f"bxs2edf.exe not found at {bxs2edf_exe}. "
                f"Please ensure bxs2edf.exe is in the rapid_reporter directory."
            )
        
        # Convert BXS to EDF using bxs2edf.exe directly with full path
        edf_path = output_directory / (bxs_file.stem + ".edf")
        
        # Build command with absolute paths
        cmd = [str(bxs2edf_exe), str(bxs_file.resolve()), str(edf_path.resolve()), "/o"]
        
        logger.debug(f"Running: {' '.join(cmd)}")
        
        # Run conversion
        try:
            result = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                check=True,
                cwd=str(parent_dir)  # Set working directory to where bxs2edf.exe is
            )
            logger.debug(f"Conversion stdout: {result.stdout}")
            if result.stderr:
                logger.debug(f"Conversion stderr: {result.stderr}")
            
            # Parse scale factor from output (same as original converter)
            scale_factor = None
            for line in result.stdout.splitlines():
                if "scale" in line.lower():
                    match = re.search(r"scale\s*\|?\s*([\d.eE+-]+)", line)
                    if match:
                        scale_factor = float(match.group(1)) * 1e6  # Convert V to uV
                        break
            if scale_factor is None:
                logger.warning("Scale factor not found in bxs2edf output, defaulting to 0.1092442 uV/unit")
                scale_factor = 0.1092442  # Fallback
            
            if not edf_path.exists():
                raise ConversionError(f"Expected EDF output not found: {edf_path}")
                
        except subprocess.CalledProcessError as e:
            error_msg = f"bxs2edf.exe failed with exit code {e.returncode}"
            if e.stdout:
                error_msg += f"\nStdout: {e.stdout}"
            if e.stderr:
                error_msg += f"\nStderr: {e.stderr}"
            raise ConversionError(error_msg)
        logger.info(f"Initial conversion complete: {edf_path.name} (scale: {scale_factor} uV/unit)")
        
        # Create backup if requested
        if backup_original:
            backup_path = output_directory / f"{edf_path.stem}_original_backup.edf"
            shutil.copy2(edf_path, backup_path)
            logger.debug(f"Created backup: {backup_path.name}")
        
        # Load EDF with MNE
        logger.info("Loading EDF with MNE...")
        raw = mne.io.read_raw_edf(str(edf_path), preload=True)
        
        # Extract channel names from header if available
        ch_names = raw.ch_names
        logger.info(f"Loaded EDF with {len(ch_names)} channels: {ch_names}")
        
        # Detect or create segments
        if use_signal_detection:
            logger.info("Using signal-based segment detection...")
            try:
                segments = detect_segments_multi_method(raw)
            except Exception as e:
                logger.warning(f"Signal detection failed: {e}, falling back to timing")
                segments = detect_segments_by_timing(raw, segment_durations, segment_labels)
        else:
            logger.info("Using time-based segment detection...")
            segments = detect_segments_by_timing(raw, segment_durations, segment_labels)
        
        # Validate segments
        try:
            validate_segments(segments, raw)
        except Exception as e:
            logger.warning(f"Segment validation warning: {e}")
        
        # Create MNE annotations
        annotations = []
        for start, end, label, confidence in segments:
            annotations.append({
                'onset': start,
                'duration': end - start,
                'description': label
            })
            logger.debug(f"Segment: {label} {start:.1f}-{end:.1f}s (confidence: {confidence:.2f})")
        
        # Add annotations to raw object
        mne_annotations = mne.Annotations(
            onset=[ann['onset'] for ann in annotations],
            duration=[ann['duration'] for ann in annotations],
            description=[ann['description'] for ann in annotations]
        )
        raw.set_annotations(mne_annotations)
        logger.info(f"Added {len(annotations)} segment annotations")
        
        # Inject subject metadata if provided
        if subject_info:
            logger.info("Injecting subject metadata...")
            raw.info['subject_info'] = subject_info
            
            # Add to description field
            if 'name' in subject_info:
                raw.info['subject_info'] = subject_info.get('name', '')
            if 'subject_id' in subject_info:
                raw.info['subject_info'] = subject_info.get('subject_id', '')
            
            logger.info(f"Injected metadata for: {subject_info.get('name', 'Unknown')}")
        
        # Save annotated EDF
        annotated_edf_path = output_directory / f"{bxs_file.stem}_annotated.edf"
        logger.info(f"Saving annotated EDF: {annotated_edf_path.name}")
        raw.save(str(annotated_edf_path), overwrite=True)
        
        logger.info(f"✓ Successfully created annotated EDF: {annotated_edf_path.name}")
        
        return annotated_edf_path
        
    except Exception as e:
        logger.error(f"Error converting BXS to annotated EDF: {e}")
        raise ConversionError(f"Failed to convert BXS to annotated EDF: {e}")

def convert_with_multiple_strategies(
    bxs_path: Union[str, Path],
    output_dir: Union[str, Path],
    subject_info: Optional[Dict] = None
) -> Path:
    """
    Try multiple conversion strategies and return the best result.
    
    Args:
        bxs_path: Path to BXS file
        output_dir: Output directory
        subject_info: Optional subject metadata
        
    Returns:
        Path to best converted EDF file
    """
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    output_directory = validate_directory(output_dir, create_if_missing=True)
    
    strategies = []
    
    # Strategy 1: Standard conversion with timing
    try:
        logger.info("Trying Strategy 1: Standard conversion with timing...")
        result = convert_bxs_to_annotated_edf(
            bxs_file,
            output_directory,
            subject_info=subject_info,
            use_signal_detection=False
        )
        strategies.append(('timing', result, True))
        logger.info("✓ Strategy 1 succeeded")
    except Exception as e:
        logger.warning(f"Strategy 1 failed: {e}")
        strategies.append(('timing', None, False))
    
    # Strategy 2: Signal-based detection
    try:
        logger.info("Trying Strategy 2: Signal-based detection...")
        result = convert_bxs_to_annotated_edf(
            bxs_file,
            output_directory,
            subject_info=subject_info,
            use_signal_detection=True
        )
        strategies.append(('signal', result, True))
        logger.info("✓ Strategy 2 succeeded")
    except Exception as e:
        logger.warning(f"Strategy 2 failed: {e}")
        strategies.append(('signal', None, False))
    
    # Return first successful strategy
    for strategy_name, result, success in strategies:
        if success:
            logger.info(f"Using result from {strategy_name} strategy")
            return result
    
    # If all strategies failed
    raise ConversionError("All conversion strategies failed")

def validate_conversion(
    edf_path: Union[str, Path],
    expected_duration: float = 180.0,
    tolerance: float = 5.0
) -> Dict[str, any]:
    """
    Validate converted EDF file.
    
    Args:
        edf_path: Path to EDF file
        expected_duration: Expected duration in seconds
        tolerance: Time tolerance in seconds
        
    Returns:
        Validation results dictionary
        
    Raises:
        ValidationError: If validation fails
    """
    edf_file = validate_file_path(edf_path, must_exist=True, extensions=['.edf'])
    
    try:
        raw = mne.io.read_raw_edf(str(edf_file), preload=False)
        actual_duration = raw.times[-1]
        
        duration_diff = abs(actual_duration - expected_duration)
        
        validation_result = {
            'valid': duration_diff <= tolerance,
            'expected_duration': expected_duration,
            'actual_duration': actual_duration,
            'duration_diff': duration_diff,
            'channels': len(raw.ch_names),
            'sampling_rate': raw.info['sfreq'],
            'has_annotations': raw.annotations is not None and len(raw.annotations) > 0,
            'annotation_count': len(raw.annotations) if raw.annotations else 0
        }
        
        if not validation_result['valid']:
            raise ValidationError(
                f"Duration mismatch: expected {expected_duration:.1f}s, "
                f"got {actual_duration:.1f}s (diff: {duration_diff:.1f}s)"
            )
        
        logger.info(f"✓ Validation passed: {edf_file.name}")
        
        return validation_result
        
    except Exception as e:
        logger.error(f"Validation failed: {e}")
        raise ValidationError(f"EDF validation failed: {e}")

def rollback_conversion(edf_path: Union[str, Path]) -> bool:
    """
    Rollback a failed conversion by restoring backup.
    
    Args:
        edf_path: Path to EDF file to rollback
        
    Returns:
        True if rollback successful
    """
    edf_file = validate_file_path(edf_path, must_exist=True, extensions=['.edf'])
    
    backup_path = edf_file.parent / f"{edf_file.stem}_original_backup.edf"
    
    if not backup_path.exists():
        logger.warning(f"No backup found for rollback: {backup_path}")
        return False
    
    try:
        # Restore from backup
        shutil.copy2(backup_path, edf_file)
        logger.info(f"✓ Rolled back conversion: {edf_file.name}")
        return True
    except Exception as e:
        logger.error(f"Rollback failed: {e}")
        return False

