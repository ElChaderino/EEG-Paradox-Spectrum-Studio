"""
EDF Processor - Direct EDF File Processing
Process existing EDF files by adding segment annotations and metadata.

This module allows you to skip BXS conversion and work directly with existing EDF files.

Copyright (C) 2025 EEG Paradox Clinical System Contributors
Licensed under GNU General Public License v3.0
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

def process_edf_file(
    edf_path: Union[str, Path],
    output_dir: Optional[Union[str, Path]] = None,
    subject_info: Optional[Dict] = None,
    segment_durations: List[float] = [60.0, 60.0, 60.0],
    segment_labels: List[str] = ['EC', 'EO', 'EOT'],
    use_signal_detection: bool = False,
    backup_original: bool = True,
    overwrite: bool = True
) -> Path:
    """
    Process existing EDF file by adding segment annotations and metadata.
    
    Args:
        edf_path: Path to existing EDF file
        output_dir: Output directory (default: same as input file)
        subject_info: Optional subject metadata dictionary
        segment_durations: Duration of each segment in seconds
        segment_labels: Labels for each segment
        use_signal_detection: If True, use signal-based detection instead of timing
        backup_original: If True, create backup of original EDF before modification
        overwrite: If True, overwrite existing annotated file
        
    Returns:
        Path to processed annotated EDF file
        
    Raises:
        ConversionError: If processing fails
    """
    edf_file = validate_file_path(edf_path, must_exist=True, extensions=['.edf'])
    
    # Determine output directory
    if output_dir is None:
        output_directory = edf_file.parent
    else:
        output_directory = validate_directory(output_dir, create_if_missing=True)
    
    # Validate segment parameters
    validate_segment_durations(segment_durations, expected_count=3)
    validate_segment_labels(segment_labels, expected_count=3)
    
    try:
        logger.info(f"Processing EDF file: {edf_file.name}")
        
        # Create backup if requested
        if backup_original:
            backup_path = output_directory / f"{edf_file.stem}_original_backup.edf"
            if not backup_path.exists():
                shutil.copy2(edf_file, backup_path)
                logger.debug(f"Created backup: {backup_path.name}")
        
        # Load EDF with MNE
        logger.info("Loading EDF with MNE...")
        raw = mne.io.read_raw_edf(str(edf_file), preload=True)
        
        # Extract channel names from header if available
        ch_names = raw.ch_names
        total_duration = raw.times[-1]
        logger.info(f"Loaded EDF with {len(ch_names)} channels: {ch_names}")
        logger.info(f"Total duration: {total_duration:.1f} seconds")
        
        # Check if annotations already exist
        if raw.annotations and len(raw.annotations) > 0:
            logger.info(f"EDF already has {len(raw.annotations)} annotations")
            existing_annotations = raw.annotations.description
            if any(label in existing_annotations for label in segment_labels):
                logger.warning("EDF already contains segment annotations. Skipping annotation addition.")
            else:
                # Add new annotations alongside existing ones
                logger.info("Adding new segment annotations alongside existing ones...")
        else:
            logger.info("No existing annotations found, adding segment annotations...")
        
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
        new_annotations = []
        for start, end, label, confidence in segments:
            new_annotations.append({
                'onset': start,
                'duration': end - start,
                'description': label
            })
            logger.debug(f"Segment: {label} {start:.1f}-{end:.1f}s (confidence: {confidence:.2f})")
        
        # Combine with existing annotations if any
        if raw.annotations and len(raw.annotations) > 0:
            # Merge existing and new annotations
            all_onsets = list(raw.annotations.onset) + [ann['onset'] for ann in new_annotations]
            all_durations = list(raw.annotations.duration) + [ann['duration'] for ann in new_annotations]
            all_descriptions = list(raw.annotations.description) + [ann['description'] for ann in new_annotations]
            
            combined_annotations = mne.Annotations(
                onset=all_onsets,
                duration=all_durations,
                description=all_descriptions
            )
            raw.set_annotations(combined_annotations)
            logger.info(f"Added {len(new_annotations)} new annotations (total: {len(combined_annotations)})")
        else:
            # Just add new annotations
            mne_annotations = mne.Annotations(
                onset=[ann['onset'] for ann in new_annotations],
                duration=[ann['duration'] for ann in new_annotations],
                description=[ann['description'] for ann in new_annotations]
            )
            raw.set_annotations(mne_annotations)
            logger.info(f"Added {len(new_annotations)} segment annotations")
        
        # Inject subject metadata if provided (completely optional - never fails)
        # This is wrapped in a way that absolutely cannot raise exceptions
        def _try_inject_metadata():
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
                    # Try to set subject_info - MNE may reject this, but that's okay
                    raw.info['subject_info'] = mne_subject_info
                    logger.info(f"✓ Injected metadata for: {name or 'Unknown'}")
                else:
                    logger.debug("No subject_id or name found, skipping metadata")
            except Exception:
                # Swallow all exceptions - metadata injection is optional
                logger.debug("Metadata injection failed (non-fatal)")
                pass
        
        # Call the safe metadata injection function
        _try_inject_metadata()
        
        # Save annotated EDF
        annotated_edf_path = output_directory / f"{edf_file.stem}_annotated.edf"
        logger.info(f"Saving annotated EDF: {annotated_edf_path.name}")
        
        # MNE's raw.save() only saves to FIF format, so we need to use export_raw for EDF
        try:
            # Try mne.export.export_raw (MNE >= 1.0)
            if hasattr(mne, 'export'):
                mne.export.export_raw(
                    str(annotated_edf_path),
                    raw,
                    fmt='edf',
                    overwrite=overwrite,
                    physical_range='auto'
                )
                logger.debug("Successfully exported EDF using mne.export.export_raw")
            else:
                # Fallback: use pyedflib directly
                raise AttributeError("mne.export not available")
        except (AttributeError, Exception) as export_error:
            # Fallback: try using pyedflib if available
            logger.warning(f"MNE export failed: {export_error}, trying pyedflib fallback...")
            try:
                import pyedflib
                logger.debug("Using pyedflib as fallback for EDF export")
                
                # Get data and info
                data = raw.get_data()
                ch_names = raw.ch_names
                sfreq = int(raw.info['sfreq'])
                n_channels = len(ch_names)
                
                # Prepare channel info for EdfWriter
                channel_info = []
                for i, ch_name in enumerate(ch_names):
                    ch_data = data[i]
                    channel_info.append({
                        'label': ch_name,
                        'dimension': 'uV',
                        'sample_rate': sfreq,
                        'physical_max': float(ch_data.max()),
                        'physical_min': float(ch_data.min()),
                        'digital_max': 32767,
                        'digital_min': -32768,
                        'transducer': '',
                        'prefilter': ''
                    })
                
                # Create EDF file with channel info
                edf_file = pyedflib.EdfWriter(str(annotated_edf_path), channel_info, file_type=pyedflib.FILETYPE_EDFPLUS)
                
                try:
                    # Write data in chunks
                    chunk_size = sfreq * 10  # 10 second chunks
                    for start_idx in range(0, data.shape[1], chunk_size):
                        end_idx = min(start_idx + chunk_size, data.shape[1])
                        edf_file.writeSamples([data[:, start_idx:end_idx]])
                    
                    # Write annotations if present
                    if raw.annotations:
                        for ann in raw.annotations:
                            edf_file.writeAnnotation(
                                float(ann['onset']),
                                float(ann['duration']),
                                str(ann['description'])
                            )
                    
                    logger.debug("Successfully exported EDF using pyedflib")
                finally:
                    edf_file.close()
            except ImportError:
                raise ConversionError(
                    f"EDF export failed: {export_error}. "
                    "Install pyedflib for EDF export: pip install pyedflib"
                ) from export_error
            except Exception as pyedflib_error:
                raise ConversionError(
                    f"EDF export failed with both MNE export and pyedflib: {export_error}, {pyedflib_error}"
                ) from export_error
        
        logger.info(f"✓ Successfully processed EDF: {annotated_edf_path.name}")
        
        return annotated_edf_path
        
    except Exception as e:
        logger.error(f"Error processing EDF file: {e}")
        raise ConversionError(f"Failed to process EDF file: {e}")

def process_edf_folder(
    edf_folder: Union[str, Path],
    subject_file: Optional[Union[str, Path]] = None,
    output_dir: Optional[Union[str, Path]] = None,
    segment_durations: List[float] = [60.0, 60.0, 60.0],
    segment_labels: List[str] = ['EC', 'EO', 'EOT'],
    use_signal_detection: bool = False
) -> List[Path]:
    """
    Process all EDF files in a folder.
    
    Args:
        edf_folder: Folder containing EDF files
        subject_file: Optional path to Subject.txt file
        output_dir: Output directory (default: edf_folder/annotated)
        segment_durations: Duration of each segment in seconds
        segment_labels: Labels for each segment
        use_signal_detection: If True, use signal-based detection
        
    Returns:
        List of processed EDF file paths
    """
    edf_directory = validate_directory(edf_folder, must_exist=True)
    
    # Determine output directory
    if output_dir is None:
        output_directory = edf_directory / "annotated"
    else:
        output_directory = validate_directory(output_dir, create_if_missing=True)
    
    # Load subject info if provided
    subject_info = None
    if subject_file:
        from .subject_info_parser import parse_subject_file
        try:
            subject_info = parse_subject_file(subject_file).to_dict()
            logger.info(f"Loaded subject info: {subject_info.get('name', 'Unknown')}")
        except Exception as e:
            logger.warning(f"Failed to load subject info: {e}")
    
    # Find all EDF files
    edf_files = list(edf_directory.glob("*.edf"))
    
    # Filter out already annotated files
    edf_files = [f for f in edf_files if not f.stem.endswith('_annotated')]
    
    if not edf_files:
        logger.warning(f"No EDF files found in {edf_directory}")
        return []
    
    logger.info(f"Found {len(edf_files)} EDF files to process")
    
    processed_files = []
    
    for edf_file in edf_files:
        try:
            logger.info(f"Processing {edf_file.name}...")
            
            annotated_path = process_edf_file(
                edf_file,
                output_dir=output_directory,
                subject_info=subject_info,
                segment_durations=segment_durations,
                segment_labels=segment_labels,
                use_signal_detection=use_signal_detection,
                backup_original=True
            )
            
            processed_files.append(annotated_path)
            logger.info(f"✓ Processed: {edf_file.name}")
            
        except Exception as e:
            logger.error(f"✗ Failed to process {edf_file.name}: {e}")
    
    logger.info(f"Processed {len(processed_files)}/{len(edf_files)} EDF files")
    
    return processed_files

