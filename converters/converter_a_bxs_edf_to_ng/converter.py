"""
Converter A: BXS/EDF to NG
Converts BXS or EDF files to NG format.
"""

import sys
import logging
from pathlib import Path
from typing import Dict, List, Optional, Any, Union
import numpy as np

# Add parent directory to path to import bxs_processor modules
# Handle both script execution and PyInstaller bundle
if getattr(sys, 'frozen', False):
    # Running as compiled executable
    # PyInstaller sets sys._MEIPASS to the temp directory where files are extracted
    if hasattr(sys, '_MEIPASS'):
        # Add the temp directory where PyInstaller extracts files (bxs_processor is here)
        if str(sys._MEIPASS) not in sys.path:
            sys.path.insert(0, str(sys._MEIPASS))
    # Also try relative to executable
    _exe_dir = Path(sys.executable).parent
    if str(_exe_dir) not in sys.path:
        sys.path.insert(0, str(_exe_dir))
    _parent_dir = _exe_dir.parent.parent
else:
    # Running as script
    _parent_dir = Path(__file__).parent.parent.parent

if str(_parent_dir) not in sys.path:
    sys.path.insert(0, str(_parent_dir))

# Initialize logger first
logger = logging.getLogger(__name__)

try:
    from bxs_processor.direct_bxs_converter import extract_eeg_data_from_bxs
    from bxs_processor.ng_parser import parse_ng_header
    BXS_PROCESSOR_AVAILABLE = True
except ImportError:
    BXS_PROCESSOR_AVAILABLE = False
    logger.warning("bxs_processor not available - BXS conversion will not work")

try:
    import mne
    MNE_AVAILABLE = True
except ImportError:
    MNE_AVAILABLE = False

try:
    import pyedflib
    PYEDFLIB_AVAILABLE = True
except ImportError:
    PYEDFLIB_AVAILABLE = False

# Import ng_writer - handle both relative and absolute imports
try:
    from .ng_writer import write_ng_file, compute_analysis_data_from_eeg
except ImportError:
    try:
        from ng_writer import write_ng_file, compute_analysis_data_from_eeg
    except ImportError:
        # Last resort: try absolute import
        import sys
        _current_dir = Path(__file__).parent
        if str(_current_dir) not in sys.path:
            sys.path.insert(0, str(_current_dir))
        from ng_writer import write_ng_file, compute_analysis_data_from_eeg

logger = logging.getLogger(__name__)


def read_bxs_file(bxs_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Read BXS file and extract EEG data and metadata.
    
    Args:
        bxs_path: Path to BXS file
        
    Returns:
        Dictionary with:
        - 'channels': List of channel names
        - 'data': 2D numpy array (channels × samples)
        - 'sample_rate': Sampling rate in Hz
        - 'duration': Duration in seconds
        - 'metadata': Dictionary with metadata
    """
    if not BXS_PROCESSOR_AVAILABLE:
        raise ImportError("bxs_processor not available. Cannot read BXS files.")
    
    bxs_file = Path(bxs_path)
    if not bxs_file.exists():
        raise FileNotFoundError(f"BXS file not found: {bxs_path}")
    
    logger.info(f"Reading BXS file: {bxs_file.name}")
    
    # Extract EEG data
    eeg_data = extract_eeg_data_from_bxs(bxs_file, stream_name='Stream0')
    
    # Extract metadata from filename and file properties
    metadata = {
        'test_name': bxs_file.stem,
        'protocol': 'TQ7',  # Default, could be extracted from file
    }
    
    return {
        'channels': eeg_data['channels'],
        'data': eeg_data['data'],
        'sample_rate': eeg_data['sample_rate'],
        'duration': eeg_data['duration'],
        'metadata': metadata,
        'samples_per_channel': eeg_data.get('samples_per_channel', 0)
    }


def read_edf_file(edf_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Read EDF file and extract EEG data and metadata.
    
    Args:
        edf_path: Path to EDF file
        
    Returns:
        Dictionary with:
        - 'channels': List of channel names
        - 'data': 2D numpy array (channels × samples)
        - 'sample_rate': Sampling rate in Hz
        - 'duration': Duration in seconds
        - 'metadata': Dictionary with metadata
    """
    edf_file = Path(edf_path)
    if not edf_file.exists():
        raise FileNotFoundError(f"EDF file not found: {edf_path}")
    
    logger.info(f"Reading EDF file: {edf_file.name}")
    
    if MNE_AVAILABLE:
        # Use MNE for reading
        raw = mne.io.read_raw_edf(str(edf_file), preload=True, verbose=False)
        
        # Get channel data
        data = raw.get_data()  # Shape: (channels, samples)
        channel_names = raw.ch_names
        sample_rate = raw.info['sfreq']
        duration = raw.times[-1] if len(raw.times) > 0 else 0
        
        # Extract metadata from EDF header
        metadata = {
            'test_name': raw.info.get('subject_info', {}).get('his_id', edf_file.stem) if hasattr(raw.info, 'subject_info') else edf_file.stem,
            'protocol': 'TQ7',  # Default
        }
        
        # Try to get patient info
        if hasattr(raw.info, 'subject_info') and raw.info.get('subject_info'):
            subj_info = raw.info['subject_info']
            if isinstance(subj_info, dict):
                metadata.update({
                    'dob': subj_info.get('birthday', ''),
                    'gender': subj_info.get('sex', ''),
                })
        
        return {
            'channels': channel_names,
            'data': data,
            'sample_rate': sample_rate,
            'duration': duration,
            'metadata': metadata,
            'samples_per_channel': data.shape[1]
        }
    
    elif PYEDFLIB_AVAILABLE:
        # Use pyedflib for reading
        with pyedflib.EdfReader(str(edf_file)) as edf:
            num_channels = edf.signals_in_file
            channel_names = [edf.getLabel(i) for i in range(num_channels)]
            sample_rates = [edf.getSampleFrequency(i) for i in range(num_channels)]
            sample_rate = sample_rates[0] if sample_rates else 250.0
            
            # Read all channel data
            channel_data = []
            for i in range(num_channels):
                ch_data = edf.readSignal(i)
                channel_data.append(ch_data)
            
            data = np.array(channel_data)
            duration = data.shape[1] / sample_rate if sample_rate > 0 else 0
            
            # Extract metadata
            metadata = {
                'test_name': edf_file.stem,
                'protocol': 'TQ7',
            }
            
            # Try to get patient info
            try:
                patient_id = edf.getPatientCode()
                if patient_id:
                    metadata['test_name'] = patient_id
            except:
                pass
            
            return {
                'channels': channel_names,
                'data': data,
                'sample_rate': sample_rate,
                'duration': duration,
                'metadata': metadata,
                'samples_per_channel': data.shape[1]
            }
    
    else:
        raise ImportError("Neither mne nor pyedflib available. Cannot read EDF files.")


def detect_segment_type_from_annotations(
    edf_path: Union[str, Path],
    duration: float
) -> Optional[str]:
    """
    Detect segment type from EDF annotations.
    
    Args:
        edf_path: Path to EDF file
        duration: Total duration in seconds
        
    Returns:
        'EC', 'EO', 'EOT', or None
    """
    if not MNE_AVAILABLE:
        return None
    
    try:
        raw = mne.io.read_raw_edf(str(edf_path), preload=False, verbose=False)
        annotations = raw.annotations
        
        if len(annotations) > 0:
            # Check annotations for segment markers
            for desc in annotations.description:
                desc_upper = desc.upper()
                if 'EC' in desc_upper or 'EYES CLOSED' in desc_upper:
                    return 'EC'
                elif 'EO' in desc_upper and 'EOT' not in desc_upper:
                    return 'EO'
                elif 'EOT' in desc_upper or 'EYES OPEN TASK' in desc_upper:
                    return 'EOT'
        
        # Fallback: use timing-based detection
        if duration >= 120:
            # Could be full recording, check middle segment
            return 'EO'  # Default to middle segment
        elif duration >= 60:
            return 'EC'  # First segment
        
    except Exception as e:
        logger.debug(f"Could not detect segment from annotations: {e}")
    
    return None


def convert_bxs_to_ng(
    bxs_path: Union[str, Path],
    output_path: Union[str, Path],
    metadata: Optional[Dict[str, Any]] = None,
    segment_type: Optional[str] = None
) -> Path:
    """
    Convert BXS file to NG format.
    
    Args:
        bxs_path: Path to input BXS file
        output_path: Path to output NG file
        metadata: Optional metadata dictionary (will be merged with extracted metadata)
        segment_type: Optional segment type ('EC', 'EO', 'EOT'). If None, defaults to 'EC'
        
    Returns:
        Path to created NG file
    """
    # Read BXS file
    bxs_data = read_bxs_file(bxs_path)
    
    # Merge metadata
    final_metadata = bxs_data['metadata'].copy()
    if metadata:
        final_metadata.update(metadata)
    
    # Default segment type
    if segment_type is None:
        segment_type = 'EC'
    
    # Compute analysis data from EEG
    analysis_data = compute_analysis_data_from_eeg(
        bxs_data['data'],
        bxs_data['sample_rate'],
        bxs_data['channels']
    )
    
    # Write NG file
    return write_ng_file(
        output_path,
        final_metadata,
        segment_type,
        analysis_data,
        sample_count=bxs_data['samples_per_channel']
    )


def convert_edf_to_ng(
    edf_path: Union[str, Path],
    output_path: Union[str, Path],
    metadata: Optional[Dict[str, Any]] = None,
    segment_type: Optional[str] = None
) -> Path:
    """
    Convert EDF file to NG format.
    
    Args:
        edf_path: Path to input EDF file
        output_path: Path to output NG file
        metadata: Optional metadata dictionary (will be merged with extracted metadata)
        segment_type: Optional segment type ('EC', 'EO', 'EOT'). If None, tries to detect from annotations
        
    Returns:
        Path to created NG file
    """
    # Read EDF file
    edf_data = read_edf_file(edf_path)
    
    # Merge metadata
    final_metadata = edf_data['metadata'].copy()
    if metadata:
        final_metadata.update(metadata)
    
    # Auto-detect segment type if not provided
    if segment_type is None:
        segment_type = detect_segment_type_from_annotations(
            edf_path,
            edf_data['duration']
        )
        if segment_type is None:
            # Fallback: try to detect from filename or use default
            filename = Path(edf_path).stem.upper()
            if 'EC' in filename or 'CLOSED' in filename:
                segment_type = 'EC'
            elif 'EOT' in filename or 'TASK' in filename:
                segment_type = 'EOT'
            elif 'EO' in filename or 'OPEN' in filename:
                segment_type = 'EO'
            else:
                segment_type = 'EC'  # Default to Eyes Closed
                logger.warning(f"Could not auto-detect segment type, defaulting to EC")
        else:
            logger.info(f"Auto-detected segment type: {segment_type}")
        if segment_type is None:
            segment_type = 'EC'  # Default
    
    # Compute analysis data from EEG
    analysis_data = compute_analysis_data_from_eeg(
        edf_data['data'],
        edf_data['sample_rate'],
        edf_data['channels']
    )
    
    # Write NG file
    return write_ng_file(
        output_path,
        final_metadata,
        segment_type,
        analysis_data,
        sample_count=edf_data['samples_per_channel']
    )

