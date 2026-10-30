"""
NG File Parser
Parses NeuroGuide (.ng) binary files to extract metadata and data.

Based on reverse engineering of NG file format:
- Magic string: "NeuroGuide\0"
- Header contains length-prefixed strings
- Format: [4-byte length (little-endian)][string data (null-terminated)]

Copyright (C) 2025 EEG Paradox Clinical System Contributors
Licensed under GNU General Public License v3.0
"""

import logging
import struct
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union

from .utils import validate_file_path, FormatError

logger = logging.getLogger(__name__)


def extract_segment_marker(ng_path: Union[str, Path]) -> Optional[str]:
    """
    Extract segment marker from NG file.
    
    The segment marker is located dynamically, right after the notes string (last string).
    It is NOT at a fixed offset - it moves based on the length of preceding strings.
    
    Structure:
    - Notes string ends with: 0x0A (newline) 0x00 (null)
    - Immediately after notes string: Segment marker byte
    - Segment marker values:
      - 0x01 = EC (Eyes Closed)
      - 0x02 = EO (Eyes Open)
      - 0x03 = EOT (Eyes Open Task) [likely]
    
    Args:
        ng_path: Path to NG file
        
    Returns:
        'EC' if marker is 0x01 (Eyes Closed)
        'EO' if marker is 0x02 (Eyes Open)
        'EOT' if marker is 0x03 (Eyes Open Task)
        None if marker not found or invalid
    """
    ng_file = validate_file_path(ng_path, must_exist=True, extensions=['.ng'])
    
    try:
        with open(ng_file, 'rb') as f:
            # Skip magic string
            magic = f.read(10)
            if magic != b'NeuroGuide':
                return None
            
            # Skip fixed fields
            f.read(23)
            
            # Parse all length-prefixed strings to get to the end
            strings = []
            for i in range(20):  # Safety limit
                length_bytes = f.read(4)
                if len(length_bytes) < 4:
                    break
                
                length = struct.unpack('<I', length_bytes)[0]
                if length == 0 or length > 1000:
                    break
                
                string_data = f.read(length)
                if len(string_data) < length:
                    break
                
                clean_data = string_data.rstrip(b'\x00')
                try:
                    decoded = clean_data.decode('ascii', errors='ignore')
                    if len(decoded) > 0:
                        strings.append(decoded)
                except:
                    pass
            
            # Now we're at the position right after the last (notes) string data
            # The notes string data includes the newline (0x0A) and null (0x00) at the end
            # So we need to go back to find where the newline is, or look ahead
            # Actually, the segment marker is right at the current file position
            # because the notes string data ends with 0x0A 0x00, and the next byte is the marker
            # But we've already read past it, so we need to check the bytes we just read
            
            # The last string data we read should end with 0x0A 0x00
            # So the segment marker should be the next byte after we finish reading strings
            # But we need to account for the fact that we read the string data
            
            # Actually, let's re-read: we read length (4 bytes), then string_data (length bytes)
            # After that, we're at: start_of_string + 4 + length
            # The string_data ends with 0x0A 0x00, so the segment marker is at: start_of_string + 4 + length
            # Which is exactly where we are now!
            
            # So the segment marker is the next byte
            marker_byte = f.read(1)
            if len(marker_byte) == 0:
                return None
            marker_byte = marker_byte[0]
            
            if marker_byte == 0x01:
                return 'EC'
            elif marker_byte == 0x02:
                return 'EO'
            elif marker_byte == 0x03:
                return 'EOT'
            else:
                logger.debug(f"Unknown segment marker value: 0x{marker_byte:02x} (expected 0x01, 0x02, or 0x03)")
                return None
    
    except Exception as e:
        logger.error(f"Error extracting segment marker from {ng_file}: {e}")
        return None


def parse_ng_header(ng_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Parse NG file header to extract metadata.
    
    Args:
        ng_path: Path to NG file
        
    Returns:
        Dictionary with parsed header information
    """
    ng_file = validate_file_path(ng_path, must_exist=True, extensions=['.ng'])
    
    result = {
        'file_path': str(ng_file),
        'file_size': ng_file.stat().st_size,
        'magic_string': None,
        'metadata': {},
        'raw_strings': []
    }
    
    try:
        with open(ng_file, 'rb') as f:
            # Check magic string
            magic = f.read(10)  # "NeuroGuide" = 10 bytes
            if magic != b'NeuroGuide':
                raise FormatError(f"Not a valid NG file (magic string: {magic})")
            
            result['magic_string'] = 'NeuroGuide'
            
            # Read initial fixed fields (appears to be version/flags)
            # "TEST" string starts at offset 37, length field at offset 33
            # So after "NeuroGuide" (10 bytes), we have 23 bytes until first string
            # But the length field is at offset 33, so that's 23 bytes of fixed fields
            # Let's read 23 bytes to get to the first string length field
            fixed_data = f.read(23)
            if len(fixed_data) == 23:
                result['fixed_fields_raw'] = fixed_data.hex()
                # Try to parse as structured data
                try:
                    # Format might be: H H H I H I I (2+2+2+4+2+4+4 = 20 bytes) + 3 more
                    fields = struct.unpack('<HHHIHII', fixed_data[:20])
                    result['version_fields'] = {
                        'field1': fields[0],
                        'field2': fields[1],
                        'field3': fields[2],
                        'field4': fields[3],
                        'field5': fields[4],
                        'field6': fields[5],
                        'field7': fields[6]
                    }
                except:
                    pass
            
            # Parse length-prefixed strings
            strings = []
            max_strings = 20  # Limit to prevent infinite loops
            
            for i in range(max_strings):
                try:
                    # Read 4-byte length (little-endian)
                    length_bytes = f.read(4)
                    if len(length_bytes) < 4:
                        break
                    
                    length = struct.unpack('<I', length_bytes)[0]
                    
                    # Sanity check
                    if length == 0 or length > 1000:
                        # Might be a non-string field, skip
                        f.seek(f.tell() - 4)
                        break
                    
                    # Read string data
                    string_data = f.read(length)
                    if len(string_data) < length:
                        break
                    
                    # Decode (remove null terminator if present)
                    clean_data = string_data.rstrip(b'\x00')
                    try:
                        decoded = clean_data.decode('ascii', errors='ignore')
                        if len(decoded) > 0:
                            strings.append(decoded)
                            result['raw_strings'].append({
                                'index': i,
                                'length': length,
                                'value': decoded
                            })
                    except:
                        pass
                        
                except (struct.error, UnicodeDecodeError):
                    break
                except Exception as e:
                    logger.debug(f"Error parsing string {i}: {e}")
                    break
            
            # Map strings to metadata fields based on position and content
            metadata = {}
            for i, s in enumerate(strings):
                # Test name / Subject ID (usually first)
                if i == 0 and len(s) < 20:
                    metadata['test_name'] = s
                # Date of birth (format: MM/DD/YYYY)
                elif '/' in s and len(s) == 10 and s.count('/') == 2:
                    if 'dob' not in metadata:
                        metadata['dob'] = s
                # Age (decimal number)
                elif s.replace('.', '').isdigit() and '.' in s:
                    if 'age' not in metadata:
                        metadata['age'] = s
                # Gender
                elif s.lower() in ['male', 'female', 'm', 'f']:
                    if 'gender' not in metadata:
                        metadata['gender'] = s
                # Handedness
                elif s.lower() in ['right', 'left', 'ambidextrous']:
                    if 'handedness' not in metadata:
                        metadata['handedness'] = s
                # Protocol (contains TQ7 or channel names)
                elif 'TQ7' in s or any(ch in s for ch in ['F3', 'F4', 'P3', 'P4', 'Digit', 'Span']):
                    if 'protocol' not in metadata:
                        metadata['protocol'] = s
                # Test date (format: MM/DD/YYYY)
                elif '/' in s and len(s) >= 8 and s.count('/') == 2:
                    if 'test_date' not in metadata or metadata.get('test_date') == metadata.get('dob'):
                        metadata['test_date'] = s
                # Technician
                elif 'Tech' in s or (len(s) < 30 and i > 7):
                    if 'technician' not in metadata:
                        metadata['technician'] = s
                # Doctor/Clinician
                elif 'Dr.' in s or 'Dr ' in s:
                    if 'doctor' not in metadata:
                        metadata['doctor'] = s
                # Medications
                elif any(med in s.lower() for med in ['mg', 'qd', 'bid', 'tid', 'sertraline', 'xanax', 'prozac']):
                    if 'medications' not in metadata:
                        metadata['medications'] = s
                # Notes
                elif len(s) > 20:
                    if 'notes' not in metadata:
                        metadata['notes'] = s
            
            result['metadata'] = metadata
            result['all_strings'] = strings
            result['segment_marker'] = extract_segment_marker(ng_path)
            
    except Exception as e:
        logger.error(f"Error parsing NG header: {e}")
        raise FormatError(f"Failed to parse NG header: {e}")
    
    return result


def extract_segment_marker(ng_path: Union[str, Path]) -> Optional[str]:
    """
    Extract segment marker from NG file using simple pattern matching.
    
    Simple method: Find the pattern 0x0A 0x00 (newline + null) which marks
    the end of the notes string, then the next byte is the segment marker.
    
    This is easier than parsing all strings - just search for the pattern!
    
    Structure:
    - Notes string ends with: 0x0A (newline) 0x00 (null)
    - Immediately after: Segment marker byte (0x01, 0x02, or 0x03)
    
    Args:
        ng_path: Path to NG file
        
    Returns:
        'EC' if marker is 0x01 (Eyes Closed)
        'EO' if marker is 0x02 (Eyes Open)
        'EOT' if marker is 0x03 (Eyes Open Task)
        None if marker not found or invalid
    """
    ng_file = validate_file_path(ng_path, must_exist=True, extensions=['.ng'])
    
    try:
        with open(ng_file, 'rb') as f:
            data = f.read()
        
        # Simple method: Search for pattern 0x0A 0x00 [0x01|0x02|0x03]
        # This pattern marks: newline (end of notes) + null + segment marker
        for i in range(len(data) - 2):
            if (data[i] == 0x0A and      # Newline (end of notes string)
                data[i+1] == 0x00 and    # Null terminator
                data[i+2] in [0x01, 0x02, 0x03]):  # Segment marker
            
                marker_byte = data[i+2]
                
                if marker_byte == 0x01:
                    return 'EC'
                elif marker_byte == 0x02:
                    return 'EO'
                elif marker_byte == 0x03:
                    return 'EOT'
        
        # Pattern not found
        logger.debug(f"Segment marker pattern (0x0A 0x00 [0x01|0x02|0x03]) not found in {ng_file}")
        return None
    
    except Exception as e:
        logger.error(f"Error extracting segment marker: {e}")
        return None

def extract_ng_metadata(ng_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Extract metadata from NG file (convenience function).
    
    Args:
        ng_path: Path to NG file
        
    Returns:
        Dictionary with metadata
    """
    parsed = parse_ng_header(ng_path)
    return parsed.get('metadata', {})


def find_eeg_data_in_ng(ng_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Search for EEG data in NG file.
    
    NG files may contain processed EEG data, spectra, or analysis results.
    
    Args:
        ng_path: Path to NG file
        
    Returns:
        Dictionary with data location information
    """
    ng_file = validate_file_path(ng_path, must_exist=True, extensions=['.ng'])
    
    result = {
        'file_path': str(ng_file),
        'file_size': ng_file.stat().st_size,
        'data_sections': [],
        'suspected_formats': [],
        'channel_data': {},
        'spectral_data': {}
    }
    
    try:
        with open(ng_file, 'rb') as f:
            file_data = f.read()
            file_size = len(file_data)
            
            # First, parse header to find where data starts
            header_info = parse_ng_header(ng_file)
            
            # Estimate data start (after header strings)
            # Header typically ends around 500-1000 bytes
            data_start = 1000  # Conservative estimate
            
            logger.info(f"Searching for EEG data in {ng_file.name} (size: {file_size:,} bytes)")
            
            # Strategy 1: Look for structured data blocks with length headers
            # Format might be: [4-byte length][data]
            structured_blocks = []
            for i in range(data_start, min(file_size - 8, data_start + 200000), 1):
                try:
                    if i + 4 > file_size:
                        break
                    length = struct.unpack('<I', file_data[i:i+4])[0]
                    # Reasonable block size
                    if 4 < length < 100000 and i + 4 + length <= file_size:
                        block_data = file_data[i+4:i+4+length]
                        # Check if block contains mostly floats or structured data
                        float_count = 0
                        for j in range(0, min(len(block_data) - 3, 100), 4):
                            try:
                                val = struct.unpack('<f', block_data[j:j+4])[0]
                                if not (val != val) and -1e6 < val < 1e6:
                                    float_count += 1
                            except:
                                break
                        
                        if float_count > 10:  # At least 10 valid floats
                            structured_blocks.append({
                                'offset': i,
                                'length': length,
                                'float_ratio': float_count / min(100, len(block_data) // 4)
                            })
                except:
                    pass
            
            if structured_blocks:
                result['data_sections'].extend(structured_blocks[:20])
            
            # Strategy 2: Look for frequency spectra (float arrays)
            # Typical EEG spectra: 64-256 frequency bins, power values
            float_arrays = []
            
            # Sample at various offsets to find float arrays
            sample_offsets = [1000, 5000, 10000, 50000, 100000, 200000, 300000, 500000]
            for start_offset in sample_offsets:
                if start_offset + 1024 > file_size:
                    continue
                
                consecutive_floats = []
                for i in range(start_offset, min(file_size - 4, start_offset + 50000), 4):
                    try:
                        val = struct.unpack('<f', file_data[i:i+4])[0]
                        # Check for reasonable values
                        if not (val != val) and not (val == float('inf')) and 0 <= val <= 50000:
                            consecutive_floats.append((i, val))
                            if len(consecutive_floats) >= 64:  # At least 64 consecutive floats
                                # Found a potential array
                                float_arrays.append({
                                    'start_offset': consecutive_floats[0][0],
                                    'length': len(consecutive_floats),
                                    'range': (min(v[1] for v in consecutive_floats), 
                                             max(v[1] for v in consecutive_floats)),
                                    'mean': sum(v[1] for v in consecutive_floats) / len(consecutive_floats),
                                    'first_10': [v[1] for v in consecutive_floats[:10]]
                                })
                                consecutive_floats = []  # Reset after finding one
                        else:
                            if len(consecutive_floats) >= 32:
                                float_arrays.append({
                                    'start_offset': consecutive_floats[0][0],
                                    'length': len(consecutive_floats),
                                    'range': (min(v[1] for v in consecutive_floats), 
                                             max(v[1] for v in consecutive_floats))
                                })
                            consecutive_floats = []
                    except:
                        if len(consecutive_floats) >= 32:
                            float_arrays.append({
                                'start_offset': consecutive_floats[0][0],
                                'length': len(consecutive_floats)
                            })
                        consecutive_floats = []
            
            # Remove duplicates and sort
            if float_arrays:
                # Remove overlapping arrays
                unique_arrays = []
                for arr in sorted(float_arrays, key=lambda x: x['start_offset']):
                    if not unique_arrays or arr['start_offset'] > unique_arrays[-1]['start_offset'] + unique_arrays[-1].get('length', 0) * 4:
                        unique_arrays.append(arr)
                
                result['suspected_formats'].append({
                    'type': 'float_arrays',
                    'count': len(unique_arrays),
                    'arrays': unique_arrays[:20]  # First 20
                })
            
            # Strategy 3: Look for channel-specific data blocks
            # Search for channel names followed by data
            eeg_channels = ['F3', 'F4', 'P3', 'P4', 'F7', 'F8', 'FZ', 'CZ', 'PZ', 'O1', 'O2', 'T3', 'T4']
            for channel in eeg_channels:
                channel_bytes = channel.encode('ascii')
                offset = data_start
                while True:
                    offset = file_data.find(channel_bytes, offset)
                    if offset == -1 or offset > file_size - 100:
                        break
                    
                    # Check if this looks like a channel marker (not just part of a string)
                    # Look for structured data after the channel name
                    data_offset = offset + len(channel_bytes)
                    if data_offset + 100 < file_size:
                        # Try to interpret following data
                        section = file_data[data_offset:data_offset+200]
                        
                        # Check for length field followed by data
                        if len(section) >= 4:
                            length = struct.unpack('<I', section[:4])[0]
                            if 0 < length < 10000:
                                result['channel_data'][channel] = {
                                    'offset': offset,
                                    'data_offset': data_offset,
                                    'suspected_length': length,
                                    'context': section[:50].hex()
                                }
                    
                    offset += len(channel_bytes)
            
            # Strategy 4: Look for frequency bin patterns
            # EEG spectra typically have 128-256 frequency bins
            # Look for arrays of ~128-256 floats
            for i in range(data_start, min(file_size - 512, data_start + 50000), 4):
                try:
                    # Try to read 128 floats (typical FFT size)
                    floats_128 = []
                    for j in range(128):
                        val = struct.unpack('<f', file_data[i+j*4:i+j*4+4])[0]
                        if not (val != val) and 0 <= val <= 10000:  # Reasonable power range
                            floats_128.append(val)
                        else:
                            break
                    
                    if len(floats_128) >= 64:  # At least half the expected size
                        # Check if values look like a spectrum (generally decreasing or has peaks)
                        if max(floats_128) > 0:
                            result['spectral_data'][f'spectrum_at_{i:08x}'] = {
                                'offset': i,
                                'bins': len(floats_128),
                                'max_power': max(floats_128),
                                'mean_power': sum(floats_128) / len(floats_128),
                                'first_10_bins': floats_128[:10]
                            }
                except:
                    pass
            
            # Limit results to prevent huge output
            if len(result['spectral_data']) > 20:
                # Keep only first 20
                keys = list(result['spectral_data'].keys())[:20]
                result['spectral_data'] = {k: result['spectral_data'][k] for k in keys}
    
    except Exception as e:
        logger.error(f"Error searching for EEG data: {e}")
        result['error'] = str(e)
        import traceback
        logger.debug(traceback.format_exc())
    
    return result

