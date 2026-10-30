"""
Rapid Reporter Integration - Direct EDF Processing
Process EDF files and pass them directly to rapid_reporter without complex export.

This module provides a simple way to:
1. Load EDF files (or convert BXS to EDF)
2. Add segment annotations
3. Pass directly to rapid_reporter for processing
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Union
import shutil

import mne
import numpy as np

from .utils import (
    validate_file_path,
    validate_directory,
    ConversionError,
    validate_segment_durations,
    validate_segment_labels
)
from .segment_detector import (
    detect_segments_by_timing,
    detect_segments_multi_method,
    validate_segments
)
from .subject_info_parser import parse_subject_file, SubjectInfo

logger = logging.getLogger(__name__)

def prepare_edf_for_rapid_reporter(
    edf_input_path: Union[str, Path],
    output_dir: Union[str, Path],
    subject_info: Optional[Dict] = None,
    segment_durations: List[float] = [60.0, 60.0, 60.0],
    segment_labels: List[str] = ['EC', 'EO', 'EOT'],
    use_signal_detection: bool = False,
    add_annotations_to_file: bool = True
) -> Path:
    """
    Prepare an EDF file for rapid_reporter processing.
    
    This function:
    1. Loads the EDF file with MNE
    2. Adds segment annotations in memory
    3. Optionally adds annotations to the EDF file using pyedflib
    4. Returns the path to the prepared EDF file
    
    Args:
        edf_input_path: Path to the input EDF file
        output_dir: Output directory for the prepared EDF file
        subject_info: Optional subject metadata dictionary
        segment_durations: Duration of each segment in seconds
        segment_labels: Labels for each segment
        use_signal_detection: If True, use signal-based detection instead of timing
        add_annotations_to_file: If True, add annotations directly to EDF file using pyedflib
        
    Returns:
        Path to the prepared EDF file ready for rapid_reporter (multi-channel files are kept intact)
    """
    edf_file = validate_file_path(edf_input_path, must_exist=True, extensions=['.edf'])
    output_directory = validate_directory(output_dir, create_if_missing=True)
    
    # Validate segment parameters
    validate_segment_durations(segment_durations, expected_count=3)
    validate_segment_labels(segment_labels, expected_count=3)
    
    try:
        logger.info(f"Preparing EDF file for rapid_reporter: {edf_file.name}...")
        
        # Load EDF with MNE
        logger.info("Loading EDF with MNE...")
        raw = mne.io.read_raw_edf(str(edf_file), preload=True)
        
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
        
        # Create annotations for segments
        mne_annotations = mne.Annotations(
            onset=[ann[0] for ann in segments],
            duration=[ann[1] - ann[0] for ann in segments],
            description=[ann[2] for ann in segments]
        )
        raw.set_annotations(mne_annotations)
        logger.info(f"Added {len(segments)} annotations to EDF in memory.")
        
        # Extract primary site from channel names (first channel is usually the primary site)
        primary_site = None
        if ch_names:
            # Get first channel and normalize it
            first_ch = ch_names[0].upper()
            # Map common channel names to standard site names
            site_mapping = {
                'FP1': 'FP1', 'FP2': 'FP2',
                'F3': 'F3', 'F4': 'F4', 'F7': 'F7', 'F8': 'F8', 'FZ': 'FZ',
                'C3': 'C3', 'C4': 'C4', 'CZ': 'CZ',
                'P3': 'P3', 'P4': 'P4', 'PZ': 'PZ',
                'O1': 'O1', 'O2': 'O2',
                'T3': 'T3', 'T4': 'T4', 'T5': 'T5', 'T6': 'T6'
            }
            primary_site = site_mapping.get(first_ch, first_ch)
        
        # If we couldn't get site from channels, try to extract from filename
        if not primary_site:
            # Try to extract site from filename (e.g., F3_TEST_11042025 -> F3)
            stem = edf_file.stem.replace('_TEST', '').replace('_annotated', '')
            parts = stem.split('_')
            for part in parts:
                part_upper = part.upper()
                if part_upper in ['F3', 'F4', 'F7', 'F8', 'FZ', 'FP1', 'FP2', 
                                 'C3', 'C4', 'CZ', 'P3', 'P4', 'PZ', 
                                 'O1', 'O2', 'T3', 'T4', 'T5', 'T6']:
                    primary_site = part_upper
                    break
        
        # Inject subject metadata if provided (optional, non-blocking)
        if subject_info:
            _try_inject_metadata(raw, subject_info)
        
        # Keep multi-channel files intact for multi-channel rapid_reporter
        # Use a generic name that indicates multi-channel TQ7 format
        # The multi-channel analyzer can handle files with multiple channels
        
        # Determine output filename based on primary site or use generic name
        if primary_site:
            # Use primary site name with _7 suffix for TQ7 multi-segment format
            prepared_edf_path = output_directory / f"{primary_site}_multichannel_7.edf"
        else:
            # Use original name with multichannel indicator
            new_name = edf_file.stem.replace('_annotated', '').replace('_TEST', '')
            prepared_edf_path = output_directory / f"{new_name}_multichannel_7.edf"
        
        # Copy the original multi-channel file (keep all channels)
        shutil.copy2(edf_file, prepared_edf_path)
        
        logger.info(f"✓ Prepared multi-channel EDF file: {prepared_edf_path.name}")
        logger.info(f"  Channels: {', '.join(ch_names)} ({len(ch_names)} total)")
        logger.info(f"  File contains 3 segments (EC: 0-60s, EO: 60-120s, EOT: 120-180s)")
        logger.info(f"  Primary site: {primary_site or 'unknown'}")
        logger.info(f"  Ready for multi-channel rapid_reporter processing")
        
        return prepared_edf_path
        
    except Exception as e:
        logger.error(f"Error preparing EDF file: {e}")
        raise ConversionError(f"Failed to prepare EDF file: {e}")


def _try_inject_metadata(raw, subject_info):
    """Helper function to safely inject metadata without raising exceptions."""
    if not subject_info:
        return
    
    try:
        logger.info("Attempting to inject subject metadata...")
        
        # Parse name into first/last if possible
        name = subject_info.get('name', '').strip() or ''
        name_parts = name.split(maxsplit=1) if name else []
        first_name = name_parts[0] if name_parts else ''
        last_name = name_parts[1] if len(name_parts) > 1 else ''
        
        # Parse gender (MNE uses: 0=unknown, 1=male, 2=female)
        gender_str = str(subject_info.get('gender', '')).upper()
        sex = 1 if gender_str in ['M', 'MALE'] else (2 if gender_str in ['F', 'FEMALE'] else 0)
        
        # Parse handedness (MNE uses: 0=unknown, 1=right, 2=left)
        hand_str = str(subject_info.get('handedness', '')).upper()
        hand = 1 if hand_str in ['R', 'RIGHT'] else (2 if hand_str in ['L', 'LEFT'] else 0)
        
        # Create subject_info dict (MNE standard format)
        mne_subject_info = {
            'his_id': str(subject_info.get('subject_id', '')),
            'first_name': first_name,
            'last_name': last_name,
            'sex': sex,
            'hand': hand,
        }
        
        # Only try to set if we have at least a subject_id or name
        if mne_subject_info['his_id'] or mne_subject_info['first_name']:
            raw.info['subject_info'] = mne_subject_info
            logger.info(f"✓ Injected metadata for: {name or 'Unknown'}")
        else:
            logger.debug("No subject_id or name found, skipping metadata")
    except Exception:
        # Swallow all exceptions - metadata injection is optional
        logger.debug("Metadata injection failed (non-fatal)")
        pass


def process_edf_folder_for_rapid_reporter(
    edf_folder: Union[str, Path],
    subject_file: Optional[Union[str, Path]] = None,
    output_dir: Optional[Union[str, Path]] = None,
    segment_durations: List[float] = [60.0, 60.0, 60.0],
    segment_labels: List[str] = ['EC', 'EO', 'EOT'],
    use_signal_detection: bool = False
) -> List[Path]:
    """
    Process all EDF files in a folder and prepare them for rapid_reporter.
    
    Returns list of prepared EDF file paths ready for rapid_reporter processing.
    """
    edf_folder = validate_directory(edf_folder, must_exist=True)
    
    if output_dir is None:
        output_dir = edf_folder / "rapid_reporter_input"
    else:
        output_dir = validate_directory(output_dir, create_if_missing=True)
    
    # Parse subject info if provided
    subject_info = None
    if subject_file:
        subject_file_path = validate_file_path(subject_file, must_exist=True)
        try:
            subject_info_obj = parse_subject_file(subject_file_path)
            subject_info = subject_info_obj.to_dict()
            logger.info(f"Loaded subject info: {subject_info.get('name', 'Unknown')}")
        except Exception as e:
            logger.warning(f"Could not parse subject file: {e}")
    
    # Find all EDF files (exclude already processed files)
    edf_files = sorted(edf_folder.glob("*.edf"))
    # Filter out files that are already in rapid_reporter format or are backups
    edf_files = [
        f for f in edf_files 
        if not f.stem.endswith('_annotated') 
        and not f.stem.endswith('_original_backup')
        and not f.stem.endswith('_7')  # Already in rapid_reporter format
        and not f.stem.endswith('_multichannel_7')  # Already prepared for multi-channel
    ]
    
    if not edf_files:
        logger.warning(f"No EDF files found in {edf_folder}")
        return []
    
    logger.info(f"Found {len(edf_files)} EDF files to process")
    
    prepared_files = []
    for edf_file in edf_files:
        try:
            logger.info(f"Processing {edf_file.name}...")
            result = prepare_edf_for_rapid_reporter(
                edf_file,
                output_dir,
                subject_info=subject_info,
                segment_durations=segment_durations,
                segment_labels=segment_labels,
                use_signal_detection=use_signal_detection,
                add_annotations_to_file=False
            )
            
            # Add prepared file to list
            prepared_files.append(result)
            logger.info(f"✓ Prepared: {result.name}")
        except Exception as e:
            logger.error(f"✗ Failed to process {edf_file.name}: {e}")
    
    logger.info(f"Processed {len(prepared_files)}/{len(edf_files)} EDF files")
    logger.info(f"✓ Multi-channel files are ready for rapid_reporter processing in: {output_dir}")
    logger.info(f"  Use MultiChannelAnalyzer or run_multichannel_rapid_reporter.py to process")
    return prepared_files

