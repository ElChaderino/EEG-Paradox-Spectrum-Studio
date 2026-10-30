"""
EDF Header Enhancer
Optional EDF header enhancement using pyedflib for metadata injection.

Copyright (C) 2025 EEG Paradox Clinical System Contributors
Licensed under GNU General Public License v3.0
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Union

from .utils import validate_file_path, MetadataError

logger = logging.getLogger(__name__)

# Try to import pyedflib
try:
    import pyedflib
    _PYEDFLIB_AVAILABLE = True
except ImportError:
    _PYEDFLIB_AVAILABLE = False
    pyedflib = None
    logger.warning("pyedflib not available. EDF header enhancement will be limited.")

def enhance_edf_header(
    edf_path: Union[str, Path],
    channel_names: Optional[List[str]] = None,
    subject_info: Optional[Dict] = None,
    output_path: Optional[Union[str, Path]] = None
) -> Path:
    """
    Enhance EDF header with proper channel names and metadata.
    
    Args:
        edf_path: Path to input EDF file
        channel_names: List of channel names to inject
        subject_info: Subject metadata dictionary
        output_path: Optional output path (default: adds '_enhanced' suffix)
        
    Returns:
        Path to enhanced EDF file
        
    Raises:
        MetadataError: If enhancement fails
    """
    edf_file = validate_file_path(edf_path, must_exist=True, extensions=['.edf'])
    
    if not _PYEDFLIB_AVAILABLE:
        logger.warning(
            "pyedflib not available. Cannot enhance EDF header. "
            "Returning original file path."
        )
        return edf_file
    
    try:
        # Determine output path
        if output_path is None:
            output_path = edf_file.parent / f"{edf_file.stem}_enhanced.edf"
        else:
            output_path = Path(output_path)
        
        # Read existing EDF
        reader = pyedflib.EdfReader(str(edf_file))
        
        try:
            n_channels = reader.signals_in_file
            signal_labels = reader.getSignalLabels()
            signal_parameters = []
            
            # Get signal parameters
            for i in range(n_channels):
                signal_parameters.append({
                    'label': signal_labels[i] if i < len(signal_labels) else f'CH{i+1}',
                    'dimension': reader.getPhysicalDimension(i),
                    'sample_rate': reader.getSampleFrequency(i),
                    'physical_max': reader.getPhysicalMaximum(i),
                    'physical_min': reader.getPhysicalMinimum(i),
                    'digital_max': reader.getDigitalMaximum(i),
                    'digital_min': reader.getDigitalMinimum(i),
                    'prefilter': reader.getPrefilter(i),
                    'transducer': reader.getTransducer(i)
                })
            
            # Update channel names if provided
            if channel_names:
                for i, ch_name in enumerate(channel_names[:n_channels]):
                    signal_parameters[i]['label'] = ch_name
            
            # Create new EDF writer
            writer = pyedflib.EdfWriter(
                str(output_path),
                n_channels,
                file_type=pyedflib.FILETYPE_EDFPLUS
            )
            
            try:
                # Set signal parameters
                for i, params in enumerate(signal_parameters):
                    writer.setLabel(i, params['label'])
                    writer.setPhysicalDimension(i, params['dimension'])
                    writer.setSampleFrequency(i, params['sample_rate'])
                    writer.setPhysicalMaximum(i, params['physical_max'])
                    writer.setPhysicalMinimum(i, params['physical_min'])
                    writer.setDigitalMaximum(i, params['digital_max'])
                    writer.setDigitalMinimum(i, params['digital_min'])
                    writer.setPrefilter(i, params['prefilter'])
                    writer.setTransducer(i, params['transducer'])
                
                # Set subject info
                if subject_info:
                    patient_name = subject_info.get('name', '')
                    patient_id = subject_info.get('subject_id', '')
                    patient_additional = f"DOB: {subject_info.get('dob', '')} | "
                    patient_additional += f"Gender: {subject_info.get('gender', '')} | "
                    patient_additional += f"Handedness: {subject_info.get('handedness', '')}"
                    
                    writer.setPatientName(patient_name)
                    writer.setPatientCode(patient_id)
                    writer.setPatientAdditional(patient_additional)
                    
                    # Set recording info
                    recording_additional = f"Technician: {subject_info.get('technician', '')} | "
                    recording_additional += f"Medications: {subject_info.get('medications', '')}"
                    writer.setRecordingAdditional(recording_additional)
                
                # Copy data
                for i in range(n_channels):
                    signal = reader.readSignal(i)
                    writer.writePhysicalSamples(signal)
                
                logger.info(f"Enhanced EDF header: {output_path.name}")
                
            finally:
                writer.close()
                
        finally:
            reader.close()
        
        return output_path
        
    except Exception as e:
        logger.error(f"Error enhancing EDF header: {e}")
        raise MetadataError(f"Failed to enhance EDF header: {e}")

def inject_subject_metadata(
    edf_path: Union[str, Path],
    subject_info: Dict,
    output_path: Optional[Union[str, Path]] = None
) -> Path:
    """
    Inject subject metadata into EDF file.
    
    Args:
        edf_path: Path to input EDF file
        subject_info: Subject metadata dictionary
        output_path: Optional output path
        
    Returns:
        Path to EDF file with injected metadata
    """
    return enhance_edf_header(
        edf_path,
        channel_names=None,
        subject_info=subject_info,
        output_path=output_path
    )

