#!/usr/bin/env python3
"""
Format Support
Framework for file format handling

Copyright (C) 2025 EEG Paradox Clinical System Contributors
Licensed under GNU General Public License v3.0
"""

import logging
from typing import Dict, List, Any, Optional
from pathlib import Path

logger = logging.getLogger(__name__)


class FormatSupport:
    """Framework for format support"""
    
    def __init__(self):
        """Initialize format support"""
        self.supported_formats = {
            '.edf': 'EDF',
            '.bdf': 'BDF',
            '.fif': 'FIF',
            '.gdf': 'GDF',
            '.bxs': 'BXS',
            '.csv': 'CSV',
            '.json': 'JSON',
            '.vhdr': 'BrainVision',
            '.cnt': 'Neuroscan',
            '.set': 'EEGLAB'
        }
    
    def can_handle(self, file_path: Path) -> bool:
        """Check if format can be handled"""
        ext = file_path.suffix.lower()
        return ext in self.supported_formats
    
    def get_format_type(self, file_path: Path) -> Optional[str]:
        """Get format type for a file"""
        ext = file_path.suffix.lower()
        return self.supported_formats.get(ext)

