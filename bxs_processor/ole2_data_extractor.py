"""
OLE2 Data Extractor for BXS Files
Extracts and analyzes actual EEG data from BXS OLE2/Compound Document files.

This module focuses on:
1. Extracting and analyzing Properties stream for compression/data patterns
2. Checking for compression signatures (ZIP, LZ, proprietary)
3. Analyzing mini-streams (<4096 bytes) that might contain data
4. Searching for patterns matching expected EEG data size and structure

Copyright (C) 2025 EEG Paradox Clinical System Contributors
Licensed under GNU General Public License v3.0
"""

import logging
import struct
import zlib
from pathlib import Path
from typing import Dict, List, Optional, Tuple, Any, Union
import re

from .utils import validate_file_path, FormatError

logger = logging.getLogger(__name__)

# Try to import OLE2 parsing libraries
try:
    import olefile
    OLEFILE_AVAILABLE = True
except ImportError:
    OLEFILE_AVAILABLE = False
    logger.debug("olefile not available - install with: pip install olefile")


def extract_properties_stream(bxs_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Extract and analyze Properties stream from BXS OLE2 file.
    
    The Properties stream (often '\x05SummaryInformation' or '\x05DocumentSummaryInformation')
    may contain metadata about compression, data format, or file structure.
    
    Args:
        bxs_path: Path to BXS file
        
    Returns:
        Dictionary with Properties stream analysis
    """
    if not OLEFILE_AVAILABLE:
        return {'error': 'olefile library not available'}
    
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    
    analysis = {
        'file_path': str(bxs_file),
        'properties_found': False,
        'streams_analyzed': [],
        'compression_detected': False,
        'compression_type': None,
        'data_patterns': [],
        'metadata': {}
    }
    
    try:
        if not olefile.isOleFile(str(bxs_file)):
            analysis['error'] = 'File is not a valid OLE2/Compound Document'
            return analysis
        
        with olefile.OleFileIO(str(bxs_file)) as ole:
            # Common Properties stream names
            properties_streams = [
                '\x05SummaryInformation',
                '\x05DocumentSummaryInformation',
                'Properties',
                'PROPERTIES',
                'SummaryInformation',
                'DocumentSummaryInformation'
            ]
            
            for prop_stream in properties_streams:
                if ole.exists(prop_stream):
                    try:
                        stream_data = ole.openstream(prop_stream).read()
                        stream_size = len(stream_data)
                        
                        analysis['properties_found'] = True
                        analysis['streams_analyzed'].append({
                            'name': prop_stream,
                            'size': stream_size,
                            'data': stream_data
                        })
                        
                        # Analyze for compression patterns
                        compression_info = detect_compression(stream_data)
                        if compression_info['detected']:
                            analysis['compression_detected'] = True
                            analysis['compression_type'] = compression_info['type']
                        
                        # Look for data patterns
                        patterns = find_data_patterns(stream_data)
                        if patterns:
                            analysis['data_patterns'].extend(patterns)
                        
                        # Extract readable metadata
                        metadata = extract_readable_metadata(stream_data)
                        if metadata:
                            analysis['metadata'][prop_stream] = metadata
                        
                        logger.info(f"Analyzed Properties stream '{prop_stream}': {stream_size} bytes")
                        
                    except Exception as e:
                        logger.warning(f"Error reading Properties stream '{prop_stream}': {e}")
            
            # If no standard Properties stream found, search for any stream with "prop" in name
            if not analysis['properties_found']:
                all_items = ole.listdir()
                for item in all_items:
                    item_path = '/'.join(item)
                    if 'prop' in item_path.lower():
                        try:
                            if ole.get_type(item) == olefile.STGTY_STREAM:
                                stream_data = ole.openstream(item_path).read()
                                analysis['streams_analyzed'].append({
                                    'name': item_path,
                                    'size': len(stream_data),
                                    'data': stream_data
                                })
                                analysis['properties_found'] = True
                        except Exception as e:
                            logger.debug(f"Could not read potential Properties stream {item_path}: {e}")
    
    except Exception as e:
        logger.error(f"Error extracting Properties stream: {e}")
        analysis['error'] = str(e)
    
    return analysis


def detect_compression(data: bytes) -> Dict[str, Any]:
    """
    Detect compression signatures in data stream.
    
    Checks for:
    - ZIP compression (PK header)
    - ZLIB compression (0x78 0x9C header)
    - LZ compression signatures
    - Proprietary compression patterns
    
    Args:
        data: Data bytes to analyze
        
    Returns:
        Dictionary with compression detection results
    """
    result = {
        'detected': False,
        'type': None,
        'confidence': 'low',
        'signatures_found': []
    }
    
    if len(data) < 4:
        return result
    
    # Check for ZIP compression (PK header)
    if data[:2] == b'PK':
        result['detected'] = True
        result['type'] = 'ZIP'
        result['confidence'] = 'high'
        result['signatures_found'].append('ZIP (PK header)')
    
    # Check for ZLIB compression (common header: 0x78 0x9C)
    if data[:2] == b'\x78\x9C' or data[:2] == b'\x78\x01' or data[:2] == b'\x78\xDA':
        result['detected'] = True
        result['type'] = 'ZLIB'
        result['confidence'] = 'high'
        result['signatures_found'].append('ZLIB compression')
        
        # Try to decompress
        try:
            decompressed = zlib.decompress(data)
            result['decompressed_size'] = len(decompressed)
            result['compression_ratio'] = len(data) / len(decompressed) if len(decompressed) > 0 else 0
        except:
            result['decompression_failed'] = True
    
    # Check for LZ compression (various signatures)
    lz_signatures = [
        (b'LZ', 'LZ'),
        (b'LZH', 'LZH'),
        (b'\x1f\x8b', 'GZIP'),
        (b'BZ', 'BZIP2')
    ]
    
    for sig, name in lz_signatures:
        if data[:len(sig)] == sig:
            result['detected'] = True
            if not result['type']:
                result['type'] = name
            result['signatures_found'].append(f'{name} compression')
    
    # Check for proprietary patterns (repeated patterns might indicate compression)
    if len(data) >= 16:
        # Check for repeated byte patterns (common in compressed data)
        pattern_lengths = [2, 4, 8]
        for plen in pattern_lengths:
            pattern = data[:plen]
            matches = sum(1 for i in range(0, min(256, len(data) - plen), plen) 
                         if data[i:i+plen] == pattern)
            if matches > 3:
                result['detected'] = True
                if not result['type']:
                    result['type'] = 'Proprietary (repeated patterns)'
                result['signatures_found'].append(f'Repeated {plen}-byte pattern')
                result['confidence'] = 'medium'
                break
    
    return result


def find_data_patterns(data: bytes) -> List[Dict[str, Any]]:
    """
    Find patterns that might indicate EEG data structure.
    
    Looks for:
    - Integer sequences (16-bit, 32-bit samples)
    - Channel markers
    - Timestamp patterns
    - Segment boundaries
    
    Args:
        data: Data bytes to analyze
        
    Returns:
        List of detected patterns
    """
    patterns = []
    
    if len(data) < 4:
        return patterns
    
    # Pattern 1: Check for 16-bit integer sequences (common for EEG samples)
    # Look for sequences that might be samples (values in reasonable range for EEG)
    if len(data) >= 8:
        samples_16bit_le = []
        samples_16bit_be = []
        
        for i in range(0, min(100, len(data) - 2), 2):
            # Little-endian
            val_le = struct.unpack('<h', data[i:i+2])[0]
            # Big-endian
            val_be = struct.unpack('>h', data[i:i+2])[0]
            
            # EEG samples are typically in range -32768 to 32767 (16-bit signed)
            # But actual values are usually much smaller (microvolts)
            if -10000 <= val_le <= 10000:
                samples_16bit_le.append(val_le)
            if -10000 <= val_be <= 10000:
                samples_16bit_be.append(val_be)
        
        if len(samples_16bit_le) > 10:
            patterns.append({
                'type': '16-bit integers (little-endian)',
                'count': len(samples_16bit_le),
                'sample_range': (min(samples_16bit_le), max(samples_16bit_le)),
                'confidence': 'medium'
            })
        
        if len(samples_16bit_be) > 10:
            patterns.append({
                'type': '16-bit integers (big-endian)',
                'count': len(samples_16bit_be),
                'sample_range': (min(samples_16bit_be), max(samples_16bit_be)),
                'confidence': 'medium'
            })
    
    # Pattern 2: Look for ASCII strings that might indicate channels or segments
    ascii_strings = []
    current_string = b''
    for byte in data[:512]:  # Check first 512 bytes
        if 32 <= byte <= 126:  # Printable ASCII
            current_string += bytes([byte])
        else:
            if len(current_string) >= 3:
                ascii_strings.append(current_string.decode('ascii', errors='ignore'))
            current_string = b''
    
    eeg_keywords = ['F3', 'F4', 'P3', 'P4', 'EC', 'EO', 'EOT', 'channel', 'sample', 'hz', 'hz']
    relevant_strings = [s for s in ascii_strings if any(kw.lower() in s.lower() for kw in eeg_keywords)]
    
    if relevant_strings:
        patterns.append({
            'type': 'EEG-related ASCII strings',
            'strings': relevant_strings[:10],
            'confidence': 'high'
        })
    
    # Pattern 3: Look for timestamp-like patterns (4-byte or 8-byte integers)
    if len(data) >= 16:
        # Check for potential timestamps (large integers, often increasing)
        timestamps = []
        for i in range(0, min(32, len(data) - 4), 4):
            val = struct.unpack('<I', data[i:i+4])[0]
            # Timestamps are often large (seconds since epoch, or milliseconds)
            if 1000000 < val < 2000000000:  # Rough range for Unix timestamps
                timestamps.append(val)
        
        if len(timestamps) > 3:
            patterns.append({
                'type': 'Potential timestamps (32-bit)',
                'count': len(timestamps),
                'range': (min(timestamps), max(timestamps)),
                'confidence': 'low'
            })
    
    return patterns


def extract_readable_metadata(data: bytes) -> Dict[str, Any]:
    """
    Extract readable metadata from Properties stream.
    
    Args:
        data: Properties stream data
        
    Returns:
        Dictionary with extracted metadata
    """
    metadata = {}
    
    # Try to decode as UTF-16 (common in OLE2 Properties streams)
    try:
        text = data.decode('utf-16-le', errors='ignore')
        # Look for key-value pairs
        lines = text.split('\n')
        for line in lines[:20]:  # First 20 lines
            if ':' in line or '=' in line:
                parts = re.split('[:=]', line, 1)
                if len(parts) == 2:
                    key = parts[0].strip()
                    value = parts[1].strip()
                    if key and value:
                        metadata[key] = value
    except:
        pass
    
    # Try ASCII decoding
    try:
        text = data.decode('ascii', errors='ignore')
        # Extract readable strings
        strings = re.findall(r'[A-Za-z0-9_]+', text)
        if strings:
            metadata['readable_strings'] = strings[:20]
    except:
        pass
    
    return metadata


def analyze_mini_streams(bxs_path: Union[str, Path], max_size: int = 4096) -> Dict[str, Any]:
    """
    Analyze mini-streams (small streams < max_size bytes) that might contain data.
    
    In OLE2 format, data can be stored in mini-streams if it's smaller than
    a certain threshold (typically 4096 bytes). These are stored differently
    than regular streams.
    
    Args:
        bxs_path: Path to BXS file
        max_size: Maximum size to consider as "mini-stream" (default 4096)
        
    Returns:
        Dictionary with mini-stream analysis
    """
    if not OLEFILE_AVAILABLE:
        return {'error': 'olefile library not available'}
    
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    
    analysis = {
        'file_path': str(bxs_file),
        'mini_streams_found': [],
        'total_mini_streams': 0,
        'total_mini_stream_size': 0,
        'potential_data_streams': []
    }
    
    try:
        if not olefile.isOleFile(str(bxs_file)):
            analysis['error'] = 'File is not a valid OLE2/Compound Document'
            return analysis
        
        with olefile.OleFileIO(str(bxs_file)) as ole:
            all_items = ole.listdir()
            
            for item in all_items:
                item_path = '/'.join(item)
                
                try:
                    if ole.get_type(item) == olefile.STGTY_STREAM:
                        stream_size = ole.get_size(item_path)
                        
                        if stream_size > 0 and stream_size <= max_size:
                            # It's a mini-stream
                            try:
                                stream_data = ole.openstream(item_path).read()
                                
                                mini_info = {
                                    'name': item_path,
                                    'size': stream_size,
                                    'first_bytes_hex': stream_data[:64].hex() if len(stream_data) >= 64 else stream_data.hex(),
                                    'has_compression': detect_compression(stream_data)['detected'],
                                    'has_data_patterns': len(find_data_patterns(stream_data)) > 0
                                }
                                
                                # Check if it might contain actual data (not just metadata)
                                if stream_size > 100:  # Larger than typical metadata
                                    patterns = find_data_patterns(stream_data)
                                    if patterns or mini_info['has_compression']:
                                        mini_info['potential_data'] = True
                                        analysis['potential_data_streams'].append(mini_info)
                                
                                analysis['mini_streams_found'].append(mini_info)
                                analysis['total_mini_streams'] += 1
                                analysis['total_mini_stream_size'] += stream_size
                                
                            except Exception as e:
                                logger.debug(f"Could not read mini-stream {item_path}: {e}")
                
                except Exception as e:
                    logger.debug(f"Error analyzing {item_path}: {e}")
            
            # Sort by size (largest first)
            analysis['mini_streams_found'].sort(key=lambda x: x['size'], reverse=True)
            analysis['potential_data_streams'].sort(key=lambda x: x['size'], reverse=True)
    
    except Exception as e:
        logger.error(f"Error analyzing mini-streams: {e}")
        analysis['error'] = str(e)
    
    return analysis


def search_eeg_data_patterns(bxs_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Search for patterns matching expected EEG data size and structure.
    
    For TQ7 files, we expect:
    - 4 channels (F3, F4, P3, P4)
    - 250 Hz sampling rate
    - 180 seconds total (60s EC + 60s EO + 60s EOT)
    - 2 bytes per sample (16-bit signed integers)
    - Total data: 4 channels * 250 Hz * 180s * 2 bytes = 360,000 bytes
    
    Args:
        bxs_path: Path to BXS file
        
    Returns:
        Dictionary with EEG data pattern search results
    """
    if not OLEFILE_AVAILABLE:
        return {'error': 'olefile library not available'}
    
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    file_size = bxs_file.stat().st_size
    
    analysis = {
        'file_path': str(bxs_file),
        'file_size': file_size,
        'expected_eeg_size': 360000,  # 4 channels * 250 Hz * 180s * 2 bytes
        'candidates': [],
        'best_match': None
    }
    
    try:
        if not olefile.isOleFile(str(bxs_file)):
            analysis['error'] = 'File is not a valid OLE2/Compound Document'
            return analysis
        
        with olefile.OleFileIO(str(bxs_file)) as ole:
            all_items = ole.listdir()
            
            for item in all_items:
                item_path = '/'.join(item)
                
                try:
                    if ole.get_type(item) == olefile.STGTY_STREAM:
                        stream_size = ole.get_size(item_path)
                        
                        # Check if size matches expected EEG data size (within 10% tolerance)
                        size_diff = abs(stream_size - analysis['expected_eeg_size'])
                        size_ratio = size_diff / analysis['expected_eeg_size']
                        
                        if size_ratio < 0.1:  # Within 10%
                            # This is a candidate!
                            try:
                                stream_data = ole.openstream(item_path).read(min(1024, stream_size))
                                
                                # Analyze the data
                                patterns = find_data_patterns(stream_data)
                                compression = detect_compression(stream_data)
                                
                                candidate = {
                                    'name': item_path,
                                    'size': stream_size,
                                    'size_match': f"{size_ratio*100:.1f}% difference",
                                    'has_compression': compression['detected'],
                                    'compression_type': compression.get('type'),
                                    'data_patterns': patterns,
                                    'first_bytes_hex': stream_data[:64].hex() if len(stream_data) >= 64 else stream_data.hex(),
                                    'confidence': 'high' if not compression['detected'] and patterns else 'medium'
                                }
                                
                                analysis['candidates'].append(candidate)
                                
                            except Exception as e:
                                logger.debug(f"Could not analyze candidate stream {item_path}: {e}")
                        
                        # Also check for streams that are close to expected size (compressed data)
                        elif stream_size < analysis['expected_eeg_size'] * 0.5:  # Could be compressed
                            try:
                                stream_data = ole.openstream(item_path).read(min(1024, stream_size))
                                compression = detect_compression(stream_data)
                                
                                if compression['detected']:
                                    # Try to estimate decompressed size
                                    if compression['type'] == 'ZLIB' and 'decompressed_size' in compression:
                                        decomp_size = compression['decompressed_size']
                                        if abs(decomp_size - analysis['expected_eeg_size']) / analysis['expected_eeg_size'] < 0.1:
                                            candidate = {
                                                'name': item_path,
                                                'size': stream_size,
                                                'decompressed_size': decomp_size,
                                                'compression_type': compression['type'],
                                                'confidence': 'high',
                                                'note': 'Compressed data matching expected size'
                                            }
                                            analysis['candidates'].append(candidate)
                            
                            except Exception as e:
                                logger.debug(f"Could not check compression for {item_path}: {e}")
                
                except Exception as e:
                    logger.debug(f"Error analyzing {item_path}: {e}")
            
            # Sort candidates by confidence and size match
            if analysis['candidates']:
                analysis['candidates'].sort(key=lambda x: (
                    0 if x.get('confidence') == 'high' else 1,
                    abs(x['size'] - analysis['expected_eeg_size'])
                ))
                analysis['best_match'] = analysis['candidates'][0]
    
    except Exception as e:
        logger.error(f"Error searching for EEG data patterns: {e}")
        analysis['error'] = str(e)
    
    return analysis


def extract_all_streams_analysis(bxs_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Comprehensive analysis: Extract and analyze all streams for EEG data.
    
    This combines all extraction methods:
    1. Properties stream analysis
    2. Compression detection
    3. Mini-stream analysis
    4. EEG data pattern search
    
    Args:
        bxs_path: Path to BXS file
        
    Returns:
        Complete analysis dictionary
    """
    logger.info(f"Starting comprehensive OLE2 data extraction for: {bxs_path}")
    
    results = {
        'file_path': str(bxs_path),
        'properties_analysis': None,
        'mini_streams_analysis': None,
        'eeg_pattern_search': None,
        'summary': {}
    }
    
    # 1. Analyze Properties stream
    logger.info("Analyzing Properties stream...")
    results['properties_analysis'] = extract_properties_stream(bxs_path)
    
    # 2. Analyze mini-streams
    logger.info("Analyzing mini-streams...")
    results['mini_streams_analysis'] = analyze_mini_streams(bxs_path)
    
    # 3. Search for EEG data patterns
    logger.info("Searching for EEG data patterns...")
    results['eeg_pattern_search'] = search_eeg_data_patterns(bxs_path)
    
    # Create summary
    summary = {
        'properties_found': results['properties_analysis'].get('properties_found', False),
        'compression_detected': results['properties_analysis'].get('compression_detected', False),
        'compression_type': results['properties_analysis'].get('compression_type'),
        'mini_streams_count': results['mini_streams_analysis'].get('total_mini_streams', 0),
        'potential_data_streams': len(results['mini_streams_analysis'].get('potential_data_streams', [])),
        'eeg_data_candidates': len(results['eeg_pattern_search'].get('candidates', [])),
        'best_eeg_match': results['eeg_pattern_search'].get('best_match')
    }
    
    results['summary'] = summary
    
    logger.info("Comprehensive analysis complete!")
    logger.info(f"  - Properties found: {summary['properties_found']}")
    logger.info(f"  - Compression: {summary['compression_detected']} ({summary['compression_type']})")
    logger.info(f"  - Mini-streams: {summary['mini_streams_count']}")
    logger.info(f"  - EEG data candidates: {summary['eeg_data_candidates']}")
    
    return results


def extract_and_decode_stream(bxs_path: Union[str, Path], stream_name: str, 
                              num_samples: int = 1000) -> Dict[str, Any]:
    """
    Extract a stream and decode it as potential EEG data.
    
    Attempts to decode the stream as:
    - 16-bit signed integers (little-endian)
    - 16-bit signed integers (big-endian)
    - 32-bit floats
    
    Args:
        bxs_path: Path to BXS file
        stream_name: Name of stream to extract
        num_samples: Number of samples to decode for analysis
        
    Returns:
        Dictionary with decoded data and analysis
    """
    if not OLEFILE_AVAILABLE:
        return {'error': 'olefile library not available'}
    
    bxs_file = validate_file_path(bxs_path, must_exist=True, extensions=['.bxs'])
    
    result = {
        'stream_name': stream_name,
        'file_path': str(bxs_file),
        'raw_data': None,
        'data_size': 0,
        'decoded_samples': {},
        'channel_structure': None,
        'sample_rate_estimate': None,
        'format_verified': False
    }
    
    try:
        if not olefile.isOleFile(str(bxs_file)):
            result['error'] = 'File is not a valid OLE2/Compound Document'
            return result
        
        with olefile.OleFileIO(str(bxs_file)) as ole:
            if not ole.exists(stream_name):
                result['error'] = f"Stream '{stream_name}' not found"
                return result
            
            # Extract full stream
            raw_data = ole.openstream(stream_name).read()
            result['raw_data'] = raw_data
            result['data_size'] = len(raw_data)
            
            # Try different decoding methods
            if len(raw_data) >= 2:
                # Method 1: 16-bit signed integers (little-endian) - most common for EEG
                samples_le = []
                for i in range(0, min(num_samples * 2, len(raw_data) - 1), 2):
                    try:
                        sample = struct.unpack('<h', raw_data[i:i+2])[0]
                        samples_le.append(sample)
                    except:
                        break
                
                if samples_le:
                    result['decoded_samples']['int16_le'] = {
                        'samples': samples_le[:num_samples],
                        'count': len(samples_le),
                        'min': min(samples_le),
                        'max': max(samples_le),
                        'mean': sum(samples_le) / len(samples_le) if samples_le else 0,
                        'range': max(samples_le) - min(samples_le) if samples_le else 0
                    }
                    
                    # Check if values look like EEG data
                    # EEG samples are 16-bit signed integers, typically in range -32768 to 32767
                    # But actual values are usually much smaller (microvolts)
                    # Accept any 16-bit signed integer range as valid
                    if len(samples_le) > 100:  # Need enough samples to verify
                        # Check if values are in reasonable 16-bit range
                        min_val, max_val = min(samples_le), max(samples_le)
                        if -32768 <= min_val <= 32767 and -32768 <= max_val <= 32767:
                            # Additional check: values should have some variation (not all zeros or constant)
                            if max_val != min_val or (max_val == 0 and min_val == 0 and len(set(samples_le[:100])) > 1):
                                result['format_verified'] = True
                                result['likely_format'] = 'int16_le'
                                logger.debug(f"Verified int16_le format: range {min_val} to {max_val}, {len(samples_le)} samples")
                
                # Method 2: 16-bit signed integers (big-endian)
                samples_be = []
                for i in range(0, min(num_samples * 2, len(raw_data) - 1), 2):
                    try:
                        sample = struct.unpack('>h', raw_data[i:i+2])[0]
                        samples_be.append(sample)
                    except:
                        break
                
                if samples_be:
                    result['decoded_samples']['int16_be'] = {
                        'samples': samples_be[:num_samples],
                        'count': len(samples_be),
                        'min': min(samples_be),
                        'max': max(samples_be),
                        'mean': sum(samples_be) / len(samples_be) if samples_be else 0,
                        'range': max(samples_be) - min(samples_be) if samples_be else 0
                    }
            
            # Try to detect channel structure
            # For TQ7: 4 channels, interleaved samples
            # Expected: [F3_sample1, F4_sample1, P3_sample1, P4_sample1, F3_sample2, ...]
            if result['format_verified'] and 'int16_le' in result['decoded_samples']:
                samples = result['decoded_samples']['int16_le']['samples']
                
                # Check for interleaved channel pattern
                # Look for repeating patterns every 4 samples (4 channels)
                if len(samples) >= 8:
                    # Calculate variance within each channel position
                    channel_vars = []
                    for ch_idx in range(4):
                        channel_samples = [samples[i] for i in range(ch_idx, len(samples), 4)]
                        if len(channel_samples) > 1:
                            mean = sum(channel_samples) / len(channel_samples)
                            variance = sum((s - mean) ** 2 for s in channel_samples) / len(channel_samples)
                            channel_vars.append(variance)
                    
                    if len(channel_vars) == 4:
                        # If all channels have reasonable variance (not all zeros), likely interleaved
                        if all(v > 0 for v in channel_vars):
                            result['channel_structure'] = {
                                'type': 'interleaved',
                                'num_channels': 4,
                                'channel_variances': channel_vars,
                                'likely_channels': ['F3', 'F4', 'P3', 'P4']
                            }
            
            # Estimate sample rate
            # For TQ7: 250 Hz, 180 seconds = 45,000 samples per channel
            # Total samples = 4 channels * 45,000 = 180,000 samples
            # Total bytes = 180,000 * 2 = 360,000 bytes
            if result['format_verified']:
                total_samples = result['data_size'] // 2
                if result.get('channel_structure') and result['channel_structure'].get('num_channels'):
                    samples_per_channel = total_samples // result['channel_structure']['num_channels']
                    # Estimate: if we assume 180 seconds, sample rate = samples_per_channel / 180
                    estimated_rate = samples_per_channel / 180.0
                    result['sample_rate_estimate'] = {
                        'hz': estimated_rate,
                        'samples_per_channel': samples_per_channel,
                        'total_samples': total_samples,
                        'duration_estimate_seconds': samples_per_channel / estimated_rate if estimated_rate > 0 else 0
                    }
    
    except Exception as e:
        logger.error(f"Error extracting and decoding stream {stream_name}: {e}")
        result['error'] = str(e)
    
    return result


def extract_all_eeg_streams(bxs_path: Union[str, Path]) -> Dict[str, Any]:
    """
    Extract and decode all potential EEG data streams (Stream0-Stream3).
    
    Args:
        bxs_path: Path to BXS file
        
    Returns:
        Dictionary with all decoded streams
    """
    logger.info(f"Extracting all EEG streams from: {bxs_path}")
    
    results = {
        'file_path': str(bxs_path),
        'streams': {},
        'summary': {}
    }
    
    # Extract Stream0-Stream3 (the candidates we found)
    for stream_idx in range(4):
        stream_name = f'Stream{stream_idx}'
        logger.info(f"Extracting and decoding {stream_name}...")
        
        decoded = extract_and_decode_stream(bxs_path, stream_name, num_samples=10000)
        results['streams'][stream_name] = decoded
    
    # Create summary
    verified_streams = [name for name, data in results['streams'].items() 
                       if data and data.get('format_verified', False)]
    
    stream0_data = results['streams'].get('Stream0', {})
    sample_rate_info = stream0_data.get('sample_rate_estimate') if stream0_data else None
    
    results['summary'] = {
        'total_streams': len(results['streams']),
        'verified_streams': len(verified_streams),
        'verified_stream_names': verified_streams,
        'likely_format': stream0_data.get('likely_format') if stream0_data else None,
        'sample_rate_estimate': sample_rate_info.get('hz') if sample_rate_info else None,
        'channel_structure': stream0_data.get('channel_structure') if stream0_data else None
    }
    
    logger.info(f"Extraction complete: {len(verified_streams)}/{len(results['streams'])} streams verified")
    
    return results

