"""
BXS Format Analyzer - Reverse Engineering Tools
Deep binary analysis of BXS file structure for format understanding.

Reverse Engineering Team Approach:
- Hex dump analysis with pattern recognition
- Identify header structure, channel metadata, segment markers
- Document byte offsets and data structures
- Pattern matching for TQ7/Van Deusen format signatures

Copyright (C) 2025 EEG Paradox Clinical System Contributors
Licensed under GNU General Public License v3.0
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union
import struct

from .utils import validate_file_path, FormatError

logger = logging.getLogger(__name__)

# Try to import OLE2 parsing libraries
try:
    import olefile
    OLEFILE_AVAILABLE = True
except ImportError:
    OLEFILE_AVAILABLE = False
    logger.debug("olefile not available - install with: pip install olefile")

def analyze_ole2_structure(bxs_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Analyze OLE2/Compound Document structure of BXS file.
    Extracts stream and storage information.
    
    Args:
        bxs_path: Path to BXS file
        
    Returns:
        Dictionary with OLE2 structure analysis
    """
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    
    if not OLEFILE_AVAILABLE:
        return {
            'error': 'olefile library not available. Install with: pip install olefile',
            'available': False
        }
    
    analysis = {
        'file_path': str(bxs_file),
        'is_ole2': False,
        'streams': [],
        'storages': [],
        'stream_details': {},
        'available': True
    }
    
    try:
        if not olefile.isOleFile(str(bxs_file)):
            analysis['error'] = 'File is not a valid OLE2/Compound Document'
            return analysis
        
        analysis['is_ole2'] = True
        
        with olefile.OleFileIO(str(bxs_file)) as ole:
            # List all streams and storages
            all_items = ole.listdir()
            
            for item in all_items:
                item_path = '/'.join(item)
                
                # Check if it's a stream (has data) or storage (directory)
                if ole.exists(item_path):
                    try:
                        # Try to get stream size
                        stream_size = ole.get_size(item_path)
                        
                        if stream_size > 0:
                            # It's a stream
                            analysis['streams'].append({
                                'name': item_path,
                                'size': stream_size,
                                'type': 'stream'
                            })
                            
                            # Get first bytes for analysis
                            try:
                                stream_data = ole.openstream(item_path).read(min(256, stream_size))
                                analysis['stream_details'][item_path] = {
                                    'size': stream_size,
                                    'first_bytes_hex': stream_data[:64].hex() if len(stream_data) >= 64 else stream_data.hex(),
                                    'first_bytes': list(stream_data[:32]),
                                    'has_ascii': any(32 <= b <= 126 for b in stream_data[:256])
                                }
                                
                                # Look for ASCII strings in stream
                                ascii_strings = []
                                current_string = b''
                                for byte in stream_data[:256]:
                                    if 32 <= byte <= 126:
                                        current_string += bytes([byte])
                                    else:
                                        if len(current_string) >= 3:
                                            ascii_strings.append(current_string.decode('ascii', errors='ignore'))
                                        current_string = b''
                                
                                if ascii_strings:
                                    analysis['stream_details'][item_path]['ascii_strings'] = ascii_strings[:10]
                                    
                            except Exception as e:
                                logger.warning(f"Could not read stream {item_path}: {e}")
                        else:
                            # It's a storage (directory)
                            analysis['storages'].append({
                                'name': item_path,
                                'type': 'storage'
                            })
                    except Exception as e:
                        logger.warning(f"Error analyzing {item_path}: {e}")
            
            # Sort streams by size (largest first) - data streams are usually largest
            analysis['streams'].sort(key=lambda x: x['size'], reverse=True)
            
    except Exception as e:
        logger.error(f"Error analyzing OLE2 structure: {e}")
        analysis['error'] = str(e)
    
    return analysis


def extract_ole2_stream(bxs_path: Union[str, Path], stream_name: str) -> Optional[bytes]:
    """
    Extract a specific stream from OLE2 BXS file.
    
    Args:
        bxs_path: Path to BXS file
        stream_name: Name of stream to extract (e.g., 'Stream1' or 'Root Entry/Data')
        
    Returns:
        Stream data as bytes, or None if not found
    """
    if not OLEFILE_AVAILABLE:
        logger.error("olefile library not available")
        return None
    
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    
    try:
        if not olefile.isOleFile(str(bxs_file)):
            logger.error("File is not a valid OLE2/Compound Document")
            return None
        
        with olefile.OleFileIO(str(bxs_file)) as ole:
            if not ole.exists(stream_name):
                logger.warning(f"Stream '{stream_name}' not found")
                # Try to find similar stream names
                all_items = ole.listdir()
                similar = [item for item in all_items if stream_name.lower() in '/'.join(item).lower()]
                if similar:
                    logger.info(f"Similar streams found: {similar}")
                return None
            
            stream_data = ole.openstream(stream_name).read()
            logger.info(f"Extracted stream '{stream_name}': {len(stream_data)} bytes")
            return stream_data
            
    except Exception as e:
        logger.error(f"Error extracting stream {stream_name}: {e}")
        return None

def analyze_bxs_structure(bxs_path: Union[str, Path], include_ole2: bool = True) -> Dict[str, Any]:
    """
    Perform complete binary structure analysis of BXS file.
    
    Args:
        bxs_path: Path to BXS file
        
    Returns:
        Dictionary with structure analysis results
    """
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    
    analysis = {
        'file_path': str(bxs_file),
        'file_size': bxs_file.stat().st_size,
        'header_info': {},
        'suspected_segments': [],
        'channel_info': [],
        'format_version': None,
        'ole2_analysis': None,
        'metadata': {}
    }
    
    try:
        with open(bxs_file, 'rb') as f:
            # Read first 1024 bytes for header analysis
            header_data = f.read(1024)
            analysis['header_info'] = _analyze_header(header_data)
            
            # Check if it's OLE2 format
            if header_data[:8] == b'\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1':
                analysis['file_format'] = 'OLE2/Compound Document'
                analysis['is_ole2'] = True
                
                # Try to parse OLE2 structure if requested
                if include_ole2 and OLEFILE_AVAILABLE:
                    try:
                        ole2_analysis = analyze_ole2_structure(bxs_file)
                        analysis['ole2_analysis'] = ole2_analysis
                        analysis['ole2_structure'] = ole2_analysis  # Keep for backward compatibility
                    except Exception as e:
                        logger.warning(f"Error parsing OLE2 structure: {e}")
                        analysis['ole2_error'] = str(e)
                elif include_ole2:
                    analysis['ole2_note'] = 'olefile library not available - install with: pip install olefile'
            else:
                analysis['file_format'] = 'Unknown binary'
                analysis['is_ole2'] = False
            
            # Try to identify format version
            analysis['format_version'] = identify_format_version(bxs_file)
            
            # Search for segment markers
            f.seek(0)
            file_data = f.read()
            analysis['suspected_segments'] = find_segment_markers(bxs_file)
            
            # Extract channel information
            analysis['channel_info'] = extract_channel_info(bxs_file)
            
    except Exception as e:
        logger.error(f"Error analyzing BXS structure: {e}")
        raise FormatError(f"Failed to analyze BXS structure: {e}")
    
    logger.info(f"Analyzed BXS structure: {bxs_file.name} ({analysis['file_size']} bytes)")
    
    return analysis

def _analyze_header(header_data: bytes) -> Dict[str, Any]:
    """
    Analyze BXS file header.
    
    Args:
        header_data: First 1024 bytes of file
        
    Returns:
        Header analysis dictionary
    """
    header_info = {
        'size': len(header_data),
        'magic_bytes': None,
        'suspected_fields': [],
        'text_strings': []
    }
    
    # Look for magic bytes or signatures
    if len(header_data) >= 4:
        header_info['magic_bytes'] = header_data[:4].hex()
    
    # Extract readable text strings
    text_strings = []
    current_string = b''
    for byte in header_data:
        if 32 <= byte <= 126:  # Printable ASCII
            current_string += bytes([byte])
        else:
            if len(current_string) >= 4:
                try:
                    text_strings.append(current_string.decode('ascii', errors='ignore'))
                except:
                    pass
            current_string = b''
    
    header_info['text_strings'] = text_strings
    
    # Look for common patterns
    # TQ7 files might have specific signatures
    if b'TQ7' in header_data:
        header_info['suspected_format'] = 'TQ7'
    if b'BioExplorer' in header_data:
        header_info['suspected_format'] = 'BioExplorer'
    
    return header_info

def find_segment_markers(bxs_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """
    Search for embedded segment boundary markers in BXS file.
    
    Args:
        bxs_path: Path to BXS file
        
    Returns:
        List of suspected segment markers with byte offsets
    """
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    
    markers = []
    
    try:
        with open(bxs_file, 'rb') as f:
            data = f.read()
            
            # Look for common marker patterns
            # These are educated guesses - actual markers may vary
            
            # Pattern 1: Look for repeated byte sequences that might indicate segment boundaries
            # (60 seconds at 250 Hz = 15000 samples per channel)
            # For 4 channels, that's 60000 samples = potential marker locations
            
            # Pattern 2: Look for ASCII markers like "EC", "EO", "EOT"
            for pattern in [b'EC', b'EO', b'EOT', b'Eyes Closed', b'Eyes Open']:
                offset = 0
                while True:
                    offset = data.find(pattern, offset)
                    if offset == -1:
                        break
                    markers.append({
                        'offset': offset,
                        'pattern': pattern.decode('ascii', errors='ignore'),
                        'confidence': 'low'  # Low confidence without format spec
                    })
                    offset += len(pattern)
            
            # Pattern 3: Look for timestamp-like structures
            # (This is speculative - actual format may differ)
            
    except Exception as e:
        logger.warning(f"Error searching for segment markers: {e}")
    
    logger.info(f"Found {len(markers)} suspected segment markers")
    
    return markers

def extract_channel_info(bxs_path: Union[str, Path]) -> List[Dict[str, Any]]:
    """
    Extract channel information from BXS file binary.
    
    Args:
        bxs_path: Path to BXS file
        
    Returns:
        List of channel information dictionaries
    """
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    
    channel_info = []
    
    try:
        # Try to extract channel names from filename or header
        # BXS files converted from BioExplorer may have channel info in header
        
        with open(bxs_file, 'rb') as f:
            header = f.read(512)
            
            # Look for channel names in header (common EEG channel names)
            eeg_channels = [
                'Fp1', 'Fp2', 'F3', 'F4', 'F7', 'F8', 'Fz',
                'C3', 'C4', 'Cz', 'P3', 'P4', 'Pz',
                'O1', 'O2', 'T3', 'T4', 'T5', 'T6'
            ]
            
            for channel in eeg_channels:
                if channel.encode('ascii') in header:
                    channel_info.append({
                        'name': channel,
                        'found_in_header': True,
                        'confidence': 'medium'
                    })
        
        # If no channels found in header, try to infer from filename
        if not channel_info:
            stem = bxs_file.stem.upper()
            for channel in eeg_channels:
                if channel.upper() in stem:
                    channel_info.append({
                        'name': channel,
                        'found_in_filename': True,
                        'confidence': 'high'
                    })
                    break
        
    except Exception as e:
        logger.warning(f"Error extracting channel info: {e}")
    
    logger.info(f"Extracted {len(channel_info)} channel(s)")
    
    return channel_info

def dump_bxs_header(
    bxs_path: Union[str, Path],
    output_file: Optional[Union[str, Path]] = None,
    bytes_to_dump: int = 1024
) -> str:
    """
    Generate hex dump of BXS file header with annotations.
    
    Args:
        bxs_path: Path to BXS file
        output_file: Optional output file for dump
        bytes_to_dump: Number of bytes to dump
        
    Returns:
        Hex dump string
    """
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    
    try:
        with open(bxs_file, 'rb') as f:
            data = f.read(bytes_to_dump)
        
        # Generate hex dump
        hex_lines = []
        for i in range(0, len(data), 16):
            chunk = data[i:i+16]
            hex_str = ' '.join(f'{b:02x}' for b in chunk)
            ascii_str = ''.join(chr(b) if 32 <= b <= 126 else '.' for b in chunk)
            hex_lines.append(f'{i:08x}: {hex_str:<48} {ascii_str}')
        
        dump_text = '\n'.join(hex_lines)
        
        # Add header info
        header_text = f"Hex Dump: {bxs_file.name}\n"
        header_text += f"File Size: {bxs_file.stat().st_size} bytes\n"
        header_text += f"Dumping first {len(data)} bytes\n"
        header_text += "=" * 80 + "\n\n"
        
        full_dump = header_text + dump_text
        
        # Write to file if specified
        if output_file:
            output_path = Path(output_file)
            output_path.parent.mkdir(parents=True, exist_ok=True)
            output_path.write_text(full_dump, encoding='utf-8')
            logger.info(f"Hex dump written to: {output_path}")
        
        return full_dump
        
    except Exception as e:
        logger.error(f"Error generating hex dump: {e}")
        raise FormatError(f"Failed to generate hex dump: {e}")

def identify_format_version(bxs_path: Union[str, Path]) -> Optional[str]:
    """
    Detect BXS format version/variant.
    
    Args:
        bxs_path: Path to BXS file
        
    Returns:
        Format version string or None if unknown
    """
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    
    try:
        with open(bxs_file, 'rb') as f:
            header = f.read(512)
        
        # Check for format signatures
        if b'TQ7' in header:
            return 'TQ7'
        if b'BioExplorer' in header:
            return 'BioExplorer'
        if b'Van Deusen' in header:
            return 'Van_Deusen_TQ7'
        
        # Check file size patterns (TQ7 files are typically ~180 seconds)
        file_size = bxs_file.stat().st_size
        # Rough estimate: 4 channels, 250 Hz, 180 seconds, 2 bytes per sample
        # = 4 * 250 * 180 * 2 = 360,000 bytes + header
        if 300000 < file_size < 500000:
            return 'suspected_TQ7_format'
        
        return 'unknown'
        
    except Exception as e:
        logger.warning(f"Error identifying format version: {e}")
        return None


def analyze_ole2_structure(bxs_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Analyze OLE2/Compound Document structure of BXS file.
    
    BXS files use Microsoft OLE2 format, which is a structured storage
    format with streams and storages (similar to .doc, .xls files).
    
    Args:
        bxs_path: Path to BXS file
        
    Returns:
        Dictionary with OLE2 structure analysis
    """
    if not OLEFILE_AVAILABLE:
        raise ImportError("olefile library required for OLE2 analysis. Install with: pip install olefile")
    
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    
    analysis = {
        'is_ole2': False,
        'streams': [],
        'storages': [],
        'root_storage': None,
        'metadata': {}
    }
    
    try:
        if not olefile.isOleFile(str(bxs_file)):
            analysis['is_ole2'] = False
            return analysis
        
        analysis['is_ole2'] = True
        
        with olefile.OleFileIO(str(bxs_file)) as ole:
            # List all streams and storages
            streams = []
            storages = []
            
            for entry in ole.listdir():
                if entry[0] == '\x01Ole':  # Root storage
                    continue
                
                entry_path = '/'.join(entry)
                
                if ole.get_type(entry) == olefile.STGTY_STREAM:
                    streams.append(entry_path)
                    # Try to read stream metadata
                    try:
                        stream_size = ole.get_size(entry)
                        streams[-1] = {
                            'name': entry_path,
                            'size': stream_size,
                            'type': 'stream'
                        }
                    except:
                        streams[-1] = {
                            'name': entry_path,
                            'type': 'stream'
                        }
                elif ole.get_type(entry) == olefile.STGTY_STORAGE:
                    storages.append(entry_path)
                    storages[-1] = {
                        'name': entry_path,
                        'type': 'storage'
                    }
            
            analysis['streams'] = streams
            analysis['storages'] = storages
            
            # Try to extract metadata from common streams
            # BioExplorer might store metadata in specific streams
            metadata_streams = ['\x05SummaryInformation', '\x05DocumentSummaryInformation']
            
            for meta_stream in metadata_streams:
                try:
                    if ole.exists(meta_stream):
                        meta_data = ole.openstream(meta_stream).read()
                        analysis['metadata'][meta_stream] = {
                            'size': len(meta_data),
                            'preview': meta_data[:256].hex() if len(meta_data) > 256 else meta_data.hex()
                        }
                except Exception as e:
                    logger.debug(f"Could not read metadata stream {meta_stream}: {e}")
            
            # Look for EEG data streams (might be named with channel info)
            eeg_keywords = ['data', 'signal', 'channel', 'eeg', 'ecg', 'F3', 'F4', 'P3', 'P4']
            potential_data_streams = []
            
            for stream in streams:
                stream_name = stream['name'] if isinstance(stream, dict) else stream
                stream_lower = stream_name.lower()
                for keyword in eeg_keywords:
                    if keyword.lower() in stream_lower:
                        potential_data_streams.append(stream_name)
                        break
            
            analysis['potential_data_streams'] = potential_data_streams
            
    except Exception as e:
        logger.error(f"Error analyzing OLE2 structure: {e}")
        analysis['error'] = str(e)
    
    return analysis


def extract_ole2_stream(bxs_path: Union[str, Path], stream_name: str, output_path: Optional[Union[str, Path]] = None) -> bytes:
    """
    Extract a specific stream from OLE2 BXS file.
    
    Args:
        bxs_path: Path to BXS file
        stream_name: Name of stream to extract
        output_path: Optional path to save extracted stream
        
    Returns:
        Stream data as bytes
    """
    if not OLEFILE_AVAILABLE:
        raise ImportError("olefile library required. Install with: pip install olefile")
    
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    
    try:
        with olefile.OleFileIO(str(bxs_file)) as ole:
            if not ole.exists(stream_name):
                raise FormatError(f"Stream '{stream_name}' not found in OLE2 file")
            
            stream_data = ole.openstream(stream_name).read()
            
            if output_path:
                output_file = Path(output_path)
                output_file.parent.mkdir(parents=True, exist_ok=True)
                output_file.write_bytes(stream_data)
                logger.info(f"Extracted stream '{stream_name}' to: {output_file}")
            
            return stream_data
            
    except Exception as e:
        logger.error(f"Error extracting OLE2 stream: {e}")
        raise FormatError(f"Failed to extract stream: {e}")

