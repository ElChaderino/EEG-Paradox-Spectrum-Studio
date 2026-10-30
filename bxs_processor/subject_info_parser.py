"""
Subject Info Parser
Parse and validate Subject.txt files with comprehensive error handling.

Copyright (C) 2025 EEG Paradox Clinical System Contributors
Licensed under GNU General Public License v3.0
"""

import logging
from pathlib import Path
from typing import Dict, Optional, List, Union
from dataclasses import dataclass
from datetime import datetime
import re

from .utils import ValidationError, validate_file_path

logger = logging.getLogger(__name__)

@dataclass
class SubjectInfo:
    """Structured subject information."""
    name: str
    subject_id: str
    dob: str
    gender: str
    handedness: str
    test_date: str
    technician: str
    medications: str
    notes: str
    
    def to_dict(self) -> Dict:
        """Convert to dictionary."""
        return {
            'name': self.name,
            'subject_id': self.subject_id,
            'dob': self.dob,
            'gender': self.gender,
            'handedness': self.handedness,
            'test_date': self.test_date,
            'technician': self.technician,
            'medications': self.medications,
            'notes': self.notes
        }
    
    def validate(self) -> bool:
        """
        Validate subject information.
        
        Returns:
            True if valid
            
        Raises:
            ValidationError: If validation fails
        """
        if not self.name or not self.name.strip():
            raise ValidationError("Subject name is required")
        
        if not self.subject_id or not self.subject_id.strip():
            raise ValidationError("Subject ID is required")
        
        # Validate date formats (basic check)
        date_pattern = r'\d{1,2}[/-]\d{1,2}[/-]\d{2,4}'
        if self.dob and not re.match(date_pattern, self.dob):
            logger.warning(f"DOB format may be invalid: {self.dob}")
        
        if self.test_date and not re.match(date_pattern, self.test_date):
            logger.warning(f"Test date format may be invalid: {self.test_date}")
        
        return True

def parse_subject_file(
    subject_file: Union[str, Path],
    strict: bool = False
) -> SubjectInfo:
    """
    Parse Subject.txt file format.
    
    Expected format (9 lines):
    1. Name
    2. Subject ID/Code
    3. DOB (MM/DD/YYYY)
    4. Gender
    5. Handedness
    6. Test Date (MM/DD/YYYY)
    7. Technician
    8. Medications
    9. Notes (may be multi-line)
    
    Args:
        subject_file: Path to Subject.txt file
        strict: If True, raise errors on missing required fields
        
    Returns:
        SubjectInfo object
        
    Raises:
        ValidationError: If file format is invalid
        FileNotFoundError: If file doesn't exist
    """
    subject_path = validate_file_path(subject_file, must_exist=True)
    
    try:
        lines = subject_path.read_text(encoding='utf-8').strip().split('\n')
    except Exception as e:
        raise ValidationError(f"Failed to read subject file: {e}")
    
    # Remove empty lines and strip whitespace
    lines = [line.strip() for line in lines if line.strip()]
    
    if len(lines) < 8:
        if strict:
            raise ValidationError(
                f"Subject file must have at least 8 lines, got {len(lines)}"
            )
        logger.warning(
            f"Subject file has only {len(lines)} lines, some fields will be empty"
        )
    
    # Parse fields with defaults
    name = lines[0] if len(lines) > 0 else ""
    subject_id = lines[1] if len(lines) > 1 else ""
    dob = lines[2] if len(lines) > 2 else ""
    gender = lines[3] if len(lines) > 3 else ""
    handedness = lines[4] if len(lines) > 4 else ""
    test_date = lines[5] if len(lines) > 5 else ""
    technician = lines[6] if len(lines) > 6 else ""
    medications = lines[7] if len(lines) > 7 else ""
    
    # Notes may be multi-line (lines 8+)
    notes = '\n'.join(lines[8:]) if len(lines) > 8 else ""
    
    # Create SubjectInfo object
    subject_info = SubjectInfo(
        name=name,
        subject_id=subject_id,
        dob=dob,
        gender=gender,
        handedness=handedness,
        test_date=test_date,
        technician=technician,
        medications=medications,
        notes=notes
    )
    
    # Validate if strict mode
    if strict:
        subject_info.validate()
    else:
        try:
            subject_info.validate()
        except ValidationError as e:
            logger.warning(f"Subject info validation warning: {e}")
    
    logger.info(f"Parsed subject info: {subject_info.name} ({subject_info.subject_id})")
    
    return subject_info

def parse_subject_file_to_dict(
    subject_file: Union[str, Path],
    strict: bool = False
) -> Dict:
    """
    Parse Subject.txt file and return as dictionary.
    
    Args:
        subject_file: Path to Subject.txt file
        strict: If True, raise errors on missing required fields
        
    Returns:
        Dictionary with subject information
    """
    subject_info = parse_subject_file(subject_file, strict=strict)
    return subject_info.to_dict()

