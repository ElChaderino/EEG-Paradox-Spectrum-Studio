"""
BXS Processor Module - Grace Hopper Style Implementation
Modular BXS file processing for ClinicalQ/Swingle-style EEG data.

This module provides comprehensive BXS to EDF conversion with automatic segment
annotations, subject metadata injection, and production-grade batch processing.

Development Philosophy:
- Grace Hopper Precision: Meticulous attention to detail, comprehensive documentation
- Multi-Team Approach: Reverse engineers, developers, modders, and hackers
- Production-Grade: Battle-tested code with multiple fallback strategies
- Modular Isolation: Complete separation from rapid_reporter main codebase

Copyright (C) 2025 EEG Paradox Clinical System Contributors
Licensed under GNU General Public License v3.0
"""

import sys
from pathlib import Path
import logging

# Add parent directory to path for imports from rapid_reporter
_parent_dir = Path(__file__).parent.parent
if str(_parent_dir) not in sys.path:
    sys.path.insert(0, str(_parent_dir))

# Configure module-level logging
_logger = logging.getLogger(__name__)
if not _logger.handlers:
    _handler = logging.StreamHandler()
    _formatter = logging.Formatter(
        '[%(asctime)s] %(levelname)s %(name)s: %(message)s',
        datefmt='%Y-%m-%d %H:%M:%S'
    )
    _handler.setFormatter(_formatter)
    _logger.addHandler(_handler)
    _logger.setLevel(logging.INFO)

# Module metadata
__version__ = '1.0.0'
__author__ = 'EEG Paradox Development Team'
__description__ = 'Modular BXS Processor for ClinicalQ/Swingle-style EEG data'

# Import core modules with graceful error handling
try:
    from .bxs_format_analyzer import (
        analyze_bxs_structure,
        find_segment_markers,
        extract_channel_info,
        dump_bxs_header,
        identify_format_version,
        analyze_ole2_structure,
        extract_ole2_stream
    )
    _FORMAT_ANALYZER_AVAILABLE = True
except ImportError as e:
    _logger.warning(f"bxs_format_analyzer not available: {e}")
    _FORMAT_ANALYZER_AVAILABLE = False
    # Create stub functions
    def analyze_bxs_structure(*args, **kwargs):
        raise NotImplementedError("bxs_format_analyzer not available")
    def find_segment_markers(*args, **kwargs):
        raise NotImplementedError("bxs_format_analyzer not available")
    def extract_channel_info(*args, **kwargs):
        raise NotImplementedError("bxs_format_analyzer not available")
    def dump_bxs_header(*args, **kwargs):
        raise NotImplementedError("bxs_format_analyzer not available")
    def identify_format_version(*args, **kwargs):
        raise NotImplementedError("bxs_format_analyzer not available")
    def analyze_ole2_structure(*args, **kwargs):
        raise NotImplementedError("bxs_format_analyzer not available")
    def extract_ole2_stream(*args, **kwargs):
        raise NotImplementedError("bxs_format_analyzer not available")

try:
    from .ole2_data_extractor import (
        extract_properties_stream,
        analyze_mini_streams,
        search_eeg_data_patterns,
        extract_all_streams_analysis
    )
    _OLE2_EXTRACTOR_AVAILABLE = True
except ImportError as e:
    _logger.warning(f"ole2_data_extractor not available: {e}")
    _OLE2_EXTRACTOR_AVAILABLE = False
    def extract_properties_stream(*args, **kwargs):
        raise NotImplementedError("ole2_data_extractor not available")
    def analyze_mini_streams(*args, **kwargs):
        raise NotImplementedError("ole2_data_extractor not available")
    def search_eeg_data_patterns(*args, **kwargs):
        raise NotImplementedError("ole2_data_extractor not available")
    def extract_all_streams_analysis(*args, **kwargs):
        raise NotImplementedError("ole2_data_extractor not available")

try:
    from .bxs_enhanced_converter import (
        convert_bxs_to_annotated_edf,
        convert_with_multiple_strategies,
        validate_conversion,
        rollback_conversion
    )
    _CONVERTER_AVAILABLE = True
except ImportError as e:
    _logger.warning(f"bxs_enhanced_converter not available: {e}")
    _CONVERTER_AVAILABLE = False
    def convert_bxs_to_annotated_edf(*args, **kwargs):
        raise NotImplementedError("bxs_enhanced_converter not available")
    def convert_with_multiple_strategies(*args, **kwargs):
        raise NotImplementedError("bxs_enhanced_converter not available")
    def validate_conversion(*args, **kwargs):
        raise NotImplementedError("bxs_enhanced_converter not available")
    def rollback_conversion(*args, **kwargs):
        raise NotImplementedError("bxs_enhanced_converter not available")

try:
    from .direct_bxs_converter import (
        convert_bxs_to_edf_direct,
        extract_eeg_data_from_bxs
    )
    _DIRECT_CONVERTER_AVAILABLE = True
except ImportError as e:
    _logger.warning(f"direct_bxs_converter not available: {e}")
    _DIRECT_CONVERTER_AVAILABLE = False
    def convert_bxs_to_edf_direct(*args, **kwargs):
        raise NotImplementedError("direct_bxs_converter not available")
    def extract_eeg_data_from_bxs(*args, **kwargs):
        raise NotImplementedError("direct_bxs_converter not available")

try:
    from .subject_info_parser import (
        parse_subject_file,
        SubjectInfo
    )
    _SUBJECT_PARSER_AVAILABLE = True
except ImportError as e:
    _logger.warning(f"subject_info_parser not available: {e}")
    _SUBJECT_PARSER_AVAILABLE = False
    def parse_subject_file(*args, **kwargs):
        raise NotImplementedError("subject_info_parser not available")
    SubjectInfo = None

try:
    from .segment_detector import (
        detect_segments_by_timing,
        detect_segments_by_alpha_power,
        detect_segments_by_beta_power,
        detect_segments_by_changepoint,
        detect_segments_multi_method,
        validate_segments
    )
    _SEGMENT_DETECTOR_AVAILABLE = True
except ImportError as e:
    _logger.warning(f"segment_detector not available: {e}")
    _SEGMENT_DETECTOR_AVAILABLE = False
    def detect_segments_by_timing(*args, **kwargs):
        raise NotImplementedError("segment_detector not available")
    def detect_segments_by_alpha_power(*args, **kwargs):
        raise NotImplementedError("segment_detector not available")
    def detect_segments_by_beta_power(*args, **kwargs):
        raise NotImplementedError("segment_detector not available")
    def detect_segments_by_changepoint(*args, **kwargs):
        raise NotImplementedError("segment_detector not available")
    def detect_segments_multi_method(*args, **kwargs):
        raise NotImplementedError("segment_detector not available")
    def validate_segments(*args, **kwargs):
        raise NotImplementedError("segment_detector not available")

try:
    from .edf_header_enhancer import (
        enhance_edf_header,
        inject_subject_metadata
    )
    _EDF_ENHANCER_AVAILABLE = True
except ImportError as e:
    _logger.warning(f"edf_header_enhancer not available: {e}")
    _EDF_ENHANCER_AVAILABLE = False
    def enhance_edf_header(*args, **kwargs):
        raise NotImplementedError("edf_header_enhancer not available")
    def inject_subject_metadata(*args, **kwargs):
        raise NotImplementedError("edf_header_enhancer not available")

try:
    from .batch_processor import (
        process_bxs_batch,
        BatchProcessor
    )
    _BATCH_PROCESSOR_AVAILABLE = True
except ImportError as e:
    _logger.warning(f"batch_processor not available: {e}")
    _BATCH_PROCESSOR_AVAILABLE = False
    def process_bxs_batch(*args, **kwargs):
        raise NotImplementedError("batch_processor not available")
    BatchProcessor = None

try:
    from .edf_processor import (
        process_edf_file,
        process_edf_folder
    )
    from .rapid_reporter_integration import (
        prepare_edf_for_rapid_reporter,
        process_edf_folder_for_rapid_reporter
    )
    _EDF_PROCESSOR_AVAILABLE = True
    _RAPID_REPORTER_INTEGRATION_AVAILABLE = True
except ImportError as e:
    _logger.warning(f"edf_processor not available: {e}")
    _EDF_PROCESSOR_AVAILABLE = False
    _RAPID_REPORTER_INTEGRATION_AVAILABLE = False
    def process_edf_file(*args, **kwargs):
        raise NotImplementedError("edf_processor not available")
    def process_edf_folder(*args, **kwargs):
        raise NotImplementedError("edf_processor not available")
    def prepare_edf_for_rapid_reporter(*args, **kwargs):
        raise NotImplementedError("rapid_reporter_integration not available")
    def process_edf_folder_for_rapid_reporter(*args, **kwargs):
        raise NotImplementedError("rapid_reporter_integration not available")

try:
    from .ng_parser import (
        parse_ng_header,
        extract_ng_metadata,
        find_eeg_data_in_ng,
        extract_segment_marker
    )
    _NG_PARSER_AVAILABLE = True
except ImportError as e:
    _logger.warning(f"ng_parser not available: {e}")
    _NG_PARSER_AVAILABLE = False
    def parse_ng_header(*args, **kwargs):
        raise NotImplementedError("ng_parser not available")
    def extract_ng_metadata(*args, **kwargs):
        raise NotImplementedError("ng_parser not available")
    def find_eeg_data_in_ng(*args, **kwargs):
        raise NotImplementedError("ng_parser not available")
    def extract_segment_marker(*args, **kwargs):
        raise NotImplementedError("ng_parser not available")

try:
    from .utils import (
        BXSProcessorError,
        ConversionError,
        ValidationError,
        setup_logging,
        validate_file_path
    )
    _UTILS_AVAILABLE = True
except ImportError as e:
    _logger.warning(f"utils not available: {e}")
    _UTILS_AVAILABLE = False
    class BXSProcessorError(Exception):
        pass
    class ConversionError(BXSProcessorError):
        pass
    class ValidationError(BXSProcessorError):
        pass
    def setup_logging(*args, **kwargs):
        pass
    def validate_file_path(*args, **kwargs):
        return True

# Public API exports
__all__ = [
    # Version info
    '__version__',
    '__author__',
    '__description__',
    
    # Format analyzer
    'analyze_bxs_structure',
    'find_segment_markers',
    'extract_channel_info',
    'dump_bxs_header',
    'identify_format_version',
    'analyze_ole2_structure',
    'extract_ole2_stream',
    
    # OLE2 data extractor
    'extract_properties_stream',
    'analyze_mini_streams',
    'search_eeg_data_patterns',
    'extract_all_streams_analysis',
    
    # Enhanced converter
    'convert_bxs_to_annotated_edf',
    'convert_with_multiple_strategies',
    'validate_conversion',
    'rollback_conversion',
    
    # Direct converter (no bxs2edf.exe dependency)
    'convert_bxs_to_edf_direct',
    'extract_eeg_data_from_bxs',
    
    # Subject parser
    'parse_subject_file',
    'SubjectInfo',
    
    # Segment detector
    'detect_segments_by_timing',
    'detect_segments_by_alpha_power',
    'detect_segments_by_beta_power',
    'detect_segments_by_changepoint',
    'detect_segments_multi_method',
    'validate_segments',
    
    # EDF enhancer
    'enhance_edf_header',
    'inject_subject_metadata',
    
    # Batch processor
    'process_bxs_batch',
    'BatchProcessor',
    
    # EDF processor
    'process_edf_file',
    'process_edf_folder',
    
    # Rapid Reporter integration
    'prepare_edf_for_rapid_reporter',
    'process_edf_folder_for_rapid_reporter',
    
    # NG file parser
    'parse_ng_header',
    'extract_ng_metadata',
    'find_eeg_data_in_ng',
    'extract_segment_marker',
    
    # Utilities
    'BXSProcessorError',
    'ConversionError',
    'ValidationError',
    'setup_logging',
    'validate_file_path',
]

# Module availability flags (for conditional feature usage)
MODULE_STATUS = {
    'format_analyzer': _FORMAT_ANALYZER_AVAILABLE,
    'converter': _CONVERTER_AVAILABLE,
    'direct_converter': _DIRECT_CONVERTER_AVAILABLE,
    'subject_parser': _SUBJECT_PARSER_AVAILABLE,
    'segment_detector': _SEGMENT_DETECTOR_AVAILABLE,
    'edf_enhancer': _EDF_ENHANCER_AVAILABLE,
    'batch_processor': _BATCH_PROCESSOR_AVAILABLE,
    'edf_processor': _EDF_PROCESSOR_AVAILABLE,
    'rapid_reporter_integration': _RAPID_REPORTER_INTEGRATION_AVAILABLE,
    'ole2_extractor': _OLE2_EXTRACTOR_AVAILABLE,
    'ng_parser': _NG_PARSER_AVAILABLE,
    'utils': _UTILS_AVAILABLE,
}

if all(MODULE_STATUS.values()):
    _logger.info(f"✅ BXS Processor Module v{__version__} fully loaded")
else:
    missing = [k for k, v in MODULE_STATUS.items() if not v]
    _logger.warning(f"⚠️  BXS Processor Module v{__version__} loaded with missing components: {', '.join(missing)}")

