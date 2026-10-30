#!/usr/bin/env python3
"""
BXS Converter
Convert BXS files to EDF format using bxs2edf.exe (preferred) or coded converters (fallback)

Copyright (C) 2025 EEG Paradox Clinical System Contributors
Licensed under GNU General Public License v3.0
"""

import subprocess
import logging
import sys
from pathlib import Path
from typing import Tuple, Optional
import numpy as np
import converters.converter_a_bxs_edf_to_ng.converter as converter_module

logger = logging.getLogger(__name__)


def _parse_exe_scale_uv(stdout: str) -> Optional[float]:
    """Return the official converter's native device scale in uV/count.

    ``bxs2edf.exe`` prints a pipe-delimited table whose seventh field is the
    volts-per-count scale.  The old parser looked for a number on the header
    line containing the word ``scale`` and therefore always returned its
    hard-coded fallback for real converter output.
    """
    for line in str(stdout or "").splitlines():
        fields = [field.strip() for field in line.split("|")]
        if len(fields) < 8 or not fields[0].isdigit():
            continue
        try:
            return float(fields[6]) * 1e6
        except (TypeError, ValueError):
            continue
    return None

# Try to import MNE for EDF writing
try:
    import mne
    MNE_AVAILABLE = True
except ImportError:
    MNE_AVAILABLE = False
    logger.warning("MNE not available - EDF writing will use pyedflib if available")

try:
    import pyedflib
    PYEDFLIB_AVAILABLE = True
except ImportError:
    PYEDFLIB_AVAILABLE = False


class BXSConverter:
    """BXS to EDF converter using bxs2edf.exe (preferred) or coded converters (fallback)"""
    
    def __init__(self, exe_path: Optional[Path] = None):
        """
        Initialize BXS converter
        
        Args:
            exe_path: Path to bxs2edf.exe (auto-detected if None)
        """
        self.exe_path = self._find_bxs2edf_exe(exe_path)
        if not self.exe_path or not self.exe_path.exists():
            logger.info("bxs2edf.exe not found. Will use coded converter if available.")
            self.exe_path = None
        
        if not self.exe_path:
            logger.warning("bxs2edf.exe not available. Will use coded converter.")
    
    def _find_bxs2edf_exe(self, exe_path: Optional[Path] = None) -> Optional[Path]:
        """
        Find bxs2edf.exe in the project
        
        Args:
            exe_path: Optional explicit path
            
        Returns:
            Path to bxs2edf.exe or None if not found
        """
        if exe_path and Path(exe_path).exists():
            return Path(exe_path)
        
        # Try to find in project root and parent directories
        current_file = Path(__file__)
        
        # Start from current file's directory and search upward
        search_paths = []
        
        # Calculate likely project root (3 levels up from cracker/formats/bxs_converter.py)
        likely_project_root = current_file.parent.parent.parent
        search_paths.append(likely_project_root)
        
        # Also check parent directories
        search_paths.extend([
            likely_project_root.parent,
            likely_project_root.parent.parent,
            current_file.parent.parent.parent.parent.parent  # Original calculation for compatibility
        ])
        
        # Check each path
        for base_path in search_paths:
            # Check rapid_reporter directory
            exe = base_path / "rapid_reporter" / "bxs2edf.exe"
            if exe.exists():
                return exe
            
            # Check project root
            exe = base_path / "bxs2edf.exe"
            if exe.exists():
                return exe
        
        return None
    
    def convert(self, bxs_path: Path, output_dir: Path, header_path: Optional[Path] = None) -> Tuple[Path, float]:
        """
        Convert BXS file to EDF
        
        Tries bxs2edf.exe first, falls back to coded converter if exe not available.
        
        Args:
            bxs_path: Path to input BXS file
            output_dir: Output directory
            header_path: Optional header file path (only used with bxs2edf.exe)
            
        Returns:
            Tuple of (edf_path, scale_factor)
        """
        bxs_path = Path(bxs_path)
        output_dir = Path(output_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        
        edf_path = output_dir / (bxs_path.stem + ".edf")
        
        # Try bxs2edf.exe first if available
        if self.exe_path:
            try:
                logger.info(f"Attempting conversion with bxs2edf.exe: {bxs_path.name}")
                return self._convert_with_exe(bxs_path, edf_path, header_path)
            except Exception as e:
                logger.warning(f"bxs2edf.exe conversion failed: {e}. Falling back to coded converter.")
        try:
            logger.info(f"Using coded converter: {bxs_path.name}")
            return self._convert_with_coded_converter(bxs_path, edf_path)
        except Exception as e:
            logger.error(f"Coded converter failed: {e}")
            raise
        
        # Neither method available
        raise FileNotFoundError(
            "Neither bxs2edf.exe nor coded converter available. "
            "Please ensure bxs2edf.exe is available or coded converter is properly installed."
        )
    
    def _convert_with_exe(self, bxs_path: Path, edf_path: Path, header_path: Optional[Path] = None) -> Tuple[Path, float]:
        """Convert using bxs2edf.exe"""
        # Build command
        cmd = [str(self.exe_path), str(bxs_path.resolve()), str(edf_path.resolve()), "/o"]
        if header_path:
            cmd.append(f"/h={header_path}")
        
        logger.info(f"Converting {bxs_path.name} to {edf_path.name} using bxs2edf.exe...")
        
        try:
            result = subprocess.run(cmd, capture_output=True, text=True, check=True)
            
            # Parse native amplifier scale reported by the official converter.
            # Do not publish a made-up fallback when an unfamiliar converter
            # version changes its table layout.
            scale_factor = _parse_exe_scale_uv(result.stdout)
            if scale_factor is None:
                scale_factor = float("nan")
                logger.warning(
                    "bxs2edf.exe did not report a parseable native scale; "
                    "the EDF remains calibrated, but bxs_scale_factor is unavailable"
                )
            
            if not edf_path.exists():
                raise FileNotFoundError(f"Expected EDF output not found: {edf_path}")
            
            logger.info(f"Conversion successful: {edf_path}")
            return edf_path, scale_factor
            
        except subprocess.CalledProcessError as e:
            logger.error(f"Conversion failed: {e.stderr}")
            raise
        except Exception as e:
            logger.error(f"Conversion error: {e}")
            raise
    
    def _convert_with_coded_converter(self, bxs_path: Path, edf_path: Path) -> Tuple[Path, float]:
        """Convert using coded converter (reads BXS directly and writes EDF)"""
        # Use the module's read_bxs_file function
        read_bxs_file = converter_module.read_bxs_file
        
        logger.info(f"Reading BXS file: {bxs_path.name}")
        
        # Read BXS file using coded converter
        bxs_data = read_bxs_file(bxs_path)
        
        channels = bxs_data['channels']
        data = bxs_data['data']  # Shape: (channels, samples)
        sample_rate = bxs_data['sample_rate']
        
        logger.info(f"BXS file read: {len(channels)} channels, {data.shape[1]} samples, {sample_rate} Hz")
        
        # Write EDF file
        logger.info(f"Writing EDF file: {edf_path.name}")
        
        if MNE_AVAILABLE:
            # Use MNE to create EDF file
            # Create MNE RawArray
            ch_types = ['eeg'] * len(channels)
            info = mne.create_info(ch_names=channels, sfreq=sample_rate, ch_types=ch_types)
            
            # Convert data to Volts (assuming data is in microvolts, convert to volts)
            # BXS data is typically in microvolts, EDF expects volts
            data_volts = data / 1e6
            
            raw = mne.io.RawArray(data_volts, info, verbose=False)
            
            # Write EDF file
            raw.save(str(edf_path), overwrite=True, fmt='edf', verbose=False)
            
            logger.info(f"EDF file written successfully: {edf_path}")
            # Default scale factor (1.0 since we converted from uV to V)
            scale_factor = 1.0
            
        elif PYEDFLIB_AVAILABLE:
            # Use pyedflib to write EDF
            num_channels = len(channels)
            num_samples = data.shape[1]
            
            # Create EDF file
            with pyedflib.EdfWriter(str(edf_path), num_channels) as edf:
                # Set channel info
                for i, ch_name in enumerate(channels):
                    # pyEDFlib expects physical samples in the declared unit.
                    ch_data = data[i, :]
                    
                    # Set channel properties
                    edf.setLabel(i, ch_name)
                    edf.setPhysicalDimension(i, 'uV')
                    edf.setSampleFrequency(i, sample_rate)
                    
                    # Write channel data
                    edf.writeSignal(i, ch_data)
                
                # Set patient and recording info
                edf.setPatientCode(bxs_path.stem)
                edf.setPatientName(bxs_path.stem)
                edf.setRecordingAdditional(bxs_data.get('metadata', {}).get('protocol', 'TQ7'))
            
            logger.info(f"EDF file written successfully: {edf_path}")
            scale_factor = 1.0
            
        else:
            raise ImportError("Neither MNE nor pyedflib available. Cannot write EDF file.")
        
        if not edf_path.exists():
            raise FileNotFoundError(f"EDF file was not created: {edf_path}")
        
        return edf_path, scale_factor
