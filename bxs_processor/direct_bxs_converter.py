"""
Direct BXS to EDF Converter
Converts BXS files directly to EDF format without requiring bxs2edf.exe.

This converter uses the reverse-engineered OLE2 stream extraction to:
1. Extract EEG data from Stream0 (or Stream0-Stream3 for multi-channel)
2. Deinterleave channels (F3, F4, P3, P4)
3. Create EDF file with proper headers and annotations
4. Add segment markers (EC, EO, EOT)

Copyright (C) 2025 EEG Paradox Clinical System Contributors
Licensed under GNU General Public License v3.0
"""

import logging
import struct
from pathlib import Path
from typing import Dict, List, Optional, Union, Tuple
from datetime import datetime

import numpy as np

try:
    import pyedflib
    PYEDFLIB_AVAILABLE = True
except ImportError:
    PYEDFLIB_AVAILABLE = False

try:
    import mne
    MNE_AVAILABLE = True
except ImportError:
    MNE_AVAILABLE = False

from .utils import validate_file_path, validate_directory, ConversionError
from .ole2_data_extractor import extract_and_decode_stream

logger = logging.getLogger(__name__)


def extract_eeg_data_from_bxs(bxs_path: Union[str, Path], stream_name: str = 'Stream0') -> Dict:
    """
    Extract and deinterleave EEG data from BXS stream.
    
    Args:
        bxs_path: Path to BXS file
        stream_name: Name of stream containing EEG data (default: 'Stream0')
        
    Returns:
        Dictionary with:
        - 'channels': List of channel names
        - 'data': 2D numpy array (channels × samples)
        - 'sample_rate': Sampling rate in Hz
        - 'duration': Duration in seconds
        - 'format': Data format info
    """
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    
    logger.info(f"Extracting EEG data from {stream_name} in {bxs_file.name}")
    
    # Extract and decode stream
    decoded = extract_and_decode_stream(bxs_file, stream_name, num_samples=1000)
    
    if not decoded.get('format_verified'):
        raise ConversionError(f"Stream {stream_name} does not contain verified EEG data")
    
    if decoded.get('likely_format') != 'int16_le':
        raise ConversionError(f"Unsupported format: {decoded.get('likely_format')}")
    
    # Get channel structure
    ch_struct = decoded.get('channel_structure')
    if not ch_struct or ch_struct.get('type') != 'interleaved':
        raise ConversionError("Expected interleaved channel structure not found")
    
    num_channels = ch_struct.get('num_channels', 4)
    channel_names = ch_struct.get('likely_channels', ['F3', 'F4', 'P3', 'P4'])
    
    # Extract full raw data
    raw_data = decoded['raw_data']
    data_size = len(raw_data)
    
    # Decode all samples as int16_le
    total_samples = data_size // 2
    samples = np.frombuffer(raw_data, dtype=np.int16)
    
    # Deinterleave channels
    # Format: [F3_0, F4_0, P3_0, P4_0, F3_1, F4_1, P3_1, P4_1, ...]
    samples_per_channel = total_samples // num_channels
    
    channel_data = []
    for ch_idx in range(num_channels):
        ch_samples = samples[ch_idx::num_channels][:samples_per_channel]
        channel_data.append(ch_samples)
    
    # Stack into 2D array (channels × samples)
    data_array = np.array(channel_data, dtype=np.float64)
    
    # Get sample rate estimate
    rate_info = decoded.get('sample_rate_estimate', {})
    sample_rate = rate_info.get('hz', 250.0)
    
    # Calculate duration
    duration = samples_per_channel / sample_rate if sample_rate > 0 else 0
    
    logger.info(
        f"Extracted {num_channels} channels: {channel_names}\n"
        f"  Samples per channel: {samples_per_channel:,}\n"
        f"  Sample rate: {sample_rate:.1f} Hz\n"
        f"  Duration: {duration:.1f} seconds"
    )
    
    return {
        'channels': channel_names,
        'data': data_array,
        'sample_rate': sample_rate,
        'duration': duration,
        'samples_per_channel': samples_per_channel,
        'format': 'int16_le',
        'raw_info': decoded
    }


def create_edf_header(
    num_signals: int,
    channel_names: List[str],
    sample_rate: float,
    duration: float,
    patient_id: str = "Unknown",
    recording_id: str = "BXS Conversion",
    start_date: Optional[str] = None,
    start_time: Optional[str] = None
) -> Dict:
    """
    Create EDF header structure.
    
    Args:
        num_signals: Number of EEG channels
        channel_names: List of channel names
        sample_rate: Sampling rate in Hz
        duration: Duration in seconds
        patient_id: Patient identifier
        recording_id: Recording identifier
        start_date: Start date (format: DD.MM.YY)
        start_time: Start time (format: HH.MM.SS)
        
    Returns:
        Dictionary with EDF header information
    """
    if start_date is None:
        start_date = datetime.now().strftime("%d.%m.%y")
    if start_time is None:
        start_time = datetime.now().strftime("%H.%M.%S")
    
    # Calculate samples per data record
    # EDF standard: data records are typically 1 second
    data_record_duration = 1.0  # seconds
    samples_per_record = int(sample_rate * data_record_duration)
    num_data_records = int(duration / data_record_duration)
    
    # EDF header is 256 bytes + (256 bytes × num_signals)
    header_bytes = 256 + (256 * num_signals)
    
    header = {
        'version': 0,
        'patient_id': patient_id[:80].ljust(80),
        'recording_id': recording_id[:80].ljust(80),
        'start_date': start_date[:8].ljust(8),
        'start_time': start_time[:8].ljust(8),
        'header_bytes': header_bytes,
        'reserved': ''.ljust(44),
        'num_data_records': num_data_records,
        'duration_data_record': data_record_duration,
        'num_signals': num_signals,
        'signal_headers': []
    }
    
    # Create signal headers
    for i, ch_name in enumerate(channel_names):
        # Physical range: typically ±32767 for 16-bit, but we'll use ±1000 uV as reasonable range
        physical_min = -1000.0
        physical_max = 1000.0
        digital_min = -32768
        digital_max = 32767
        
        signal_header = {
            'label': ch_name[:16].ljust(16),
            'transducer_type': 'EEG'.ljust(80),
            'physical_dimension': 'uV'.ljust(8),
            'physical_min': physical_min,
            'physical_max': physical_max,
            'digital_min': digital_min,
            'digital_max': digital_max,
            'prefiltering': ''.ljust(80),
            'samples_per_record': samples_per_record,
            'reserved': ''.ljust(32)
        }
        
        header['signal_headers'].append(signal_header)
    
    return header


def write_edf_file(
    output_path: Union[str, Path],
    channel_data: np.ndarray,
    channel_names: List[str],
    sample_rate: float,
    patient_id: str = "Unknown",
    recording_id: str = "BXS Conversion",
    annotations: Optional[List[Tuple[float, float, str]]] = None
) -> Path:
    """
    Write EDF file using pyedflib.
    
    Args:
        output_path: Path to output EDF file
        channel_data: 2D numpy array (channels × samples)
        channel_names: List of channel names
        sample_rate: Sampling rate in Hz
        patient_id: Patient identifier
        recording_id: Recording identifier
        annotations: Optional list of (onset, duration, description) tuples
        
    Returns:
        Path to created EDF file
    """
    if not PYEDFLIB_AVAILABLE:
        raise ConversionError("pyedflib is required for direct EDF conversion. Install with: pip install pyedflib")
    
    output_file = Path(output_path)
    output_file.parent.mkdir(parents=True, exist_ok=True)
    
    num_channels = len(channel_names)
    num_samples = channel_data.shape[1]
    
    logger.info(f"Writing EDF file: {output_file.name}")
    logger.info(f"  Channels: {num_channels}, Samples: {num_samples:,}, Rate: {sample_rate:.1f} Hz")
    
    # Create EDF file
    try:
        with pyedflib.EdfWriter(
            str(output_file),
            num_channels,
            file_type=pyedflib.FILETYPE_EDFPLUS
        ) as edf_file:
            # Set header info (ASCII only, no spaces)
            edf_file.setPatientName(patient_id.replace(' ', '_')[:80])
            edf_file.setPatientCode("")
            edf_file.setPatientAdditional("")
            edf_file.setEquipment(recording_id.replace(' ', '_').replace(':', '_')[:80])
            edf_file.setTechnician("BXS_Direct_Converter")
            edf_file.setRecordingAdditional("")
            
            # Set signal headers
            for i, ch_name in enumerate(channel_names):
                edf_file.setLabel(i, ch_name)
                edf_file.setPhysicalDimension(i, "uV")
                edf_file.setPhysicalMaximum(i, 1000.0)
                edf_file.setPhysicalMinimum(i, -1000.0)
                edf_file.setDigitalMaximum(i, 32767)
                edf_file.setDigitalMinimum(i, -32768)
                edf_file.setSamplefrequency(i, int(sample_rate))  # Note: lowercase 'f' and integer
                edf_file.setPrefilter(i, "")
                edf_file.setTransducer(i, "EEG")
            
            # Write data in records
            # EDF format: data records are typically 1 second
            # Each record contains samples_per_record samples for each channel
            samples_per_record = int(sample_rate)
            num_records = int(num_samples / samples_per_record)
            
            # Write data record by record
            for record_idx in range(num_records):
                start_idx = record_idx * samples_per_record
                end_idx = start_idx + samples_per_record
                
                # Extract one record's worth of data for all channels
                record_data = channel_data[:, start_idx:end_idx].astype(np.int16)
                
                # Write record (pyedflib expects channels × samples for writeSamples)
                edf_file.writeSamples(record_data)
            
            # Write any remaining samples
            remaining_start = num_records * samples_per_record
            if remaining_start < num_samples:
                remaining_data = channel_data[:, remaining_start:].astype(np.int16)
                # Pad to samples_per_record if needed
                if remaining_data.shape[1] < samples_per_record:
                    padding = np.zeros((num_channels, samples_per_record - remaining_data.shape[1]), dtype=np.int16)
                    remaining_data = np.hstack([remaining_data, padding])
                edf_file.writeSamples(remaining_data)
            
            # Add annotations if provided
            if annotations:
                for onset, duration, description in annotations:
                    edf_file.writeAnnotation(onset, duration, description)
            
            logger.info(f"✓ EDF file created: {output_file}")
    
    except Exception as e:
        raise ConversionError(f"Failed to write EDF file: {e}")
    
    return output_file


def convert_bxs_to_edf_direct(
    bxs_path: Union[str, Path],
    output_path: Union[str, Path],
    stream_name: str = 'Stream0',
    channel_names: Optional[List[str]] = None,
    patient_id: str = "Unknown",
    recording_id: Optional[str] = None,
    segment_durations: List[float] = [60.0, 60.0, 60.0],
    segment_labels: List[str] = ['EC', 'EO', 'EOT'],
    add_segments: bool = True
) -> Path:
    """
    Convert BXS file directly to EDF without bxs2edf.exe.
    
    Args:
        bxs_path: Path to BXS file
        output_path: Path to output EDF file
        stream_name: Name of stream containing EEG data (default: 'Stream0')
        channel_names: Optional list of channel names (auto-detected if None)
        patient_id: Patient identifier
        recording_id: Recording identifier (auto-generated from filename if None)
        segment_durations: Duration of each segment in seconds
        segment_labels: Labels for each segment
        add_segments: If True, add segment annotations
        
    Returns:
        Path to created EDF file
        
    Raises:
        ConversionError: If conversion fails
    """
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    output_file = Path(output_path)
    
    if not PYEDFLIB_AVAILABLE:
        raise ConversionError("pyedflib is required. Install with: pip install pyedflib")
    
    logger.info(f"Converting {bxs_file.name} to EDF (direct method)")
    
    try:
        # Extract EEG data
        eeg_data = extract_eeg_data_from_bxs(bxs_file, stream_name)
        
        # Use detected channel names or provided ones
        if channel_names is None:
            channel_names = eeg_data['channels']
        
        # Generate recording ID from filename if not provided (ASCII only)
        if recording_id is None:
            recording_id = f"BXS_Direct_Conversion_{bxs_file.stem}"
        
        # Create annotations for segments
        annotations = []
        if add_segments and len(segment_durations) == len(segment_labels):
            current_time = 0.0
            for duration, label in zip(segment_durations, segment_labels):
                annotations.append((current_time, duration, label))
                current_time += duration
        
        # Write EDF file
        edf_path = write_edf_file(
            output_file,
            eeg_data['data'],
            channel_names,
            eeg_data['sample_rate'],
            patient_id=patient_id,
            recording_id=recording_id,
            annotations=annotations if annotations else None
        )
        
        logger.info(f"✓ Direct conversion complete: {edf_path.name}")
        logger.info(f"  Channels: {', '.join(channel_names)}")
        logger.info(f"  Duration: {eeg_data['duration']:.1f} seconds")
        logger.info(f"  Sample rate: {eeg_data['sample_rate']:.1f} Hz")
        
        return edf_path
        
    except Exception as e:
        raise ConversionError(f"Direct BXS to EDF conversion failed: {e}")

