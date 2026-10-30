"""
Batch Processor - Production-Grade Processing
Process multiple BXS files with comprehensive error handling, progress tracking,
resume capability, and quality assurance.

Copyright (C) 2025 EEG Paradox Clinical System Contributors
Licensed under GNU General Public License v3.0
"""

import logging
from pathlib import Path
from typing import Dict, List, Optional, Union
from datetime import datetime
import json
from concurrent.futures import ThreadPoolExecutor, ProcessPoolExecutor, as_completed

from .utils import (
    validate_directory,
    ProgressTracker,
    save_checkpoint,
    load_checkpoint,
    clear_checkpoint,
    ConversionError
)
from .bxs_enhanced_converter import (
    convert_bxs_to_annotated_edf,
    validate_conversion
)
from .subject_info_parser import parse_subject_file

logger = logging.getLogger(__name__)

class BatchProcessor:
    """
    Production-grade batch processor for BXS files.
    """
    
    def __init__(
        self,
        bxs_folder: Union[str, Path],
        subject_file: Optional[Union[str, Path]] = None,
        output_dir: Optional[Union[str, Path]] = None,
        parallel: bool = True,
        max_workers: int = 4
    ):
        """
        Initialize batch processor.
        
        Args:
            bxs_folder: Folder containing BXS files
            subject_file: Optional path to Subject.txt file
            output_dir: Output directory (default: bxs_folder/processed)
            parallel: Enable parallel processing
            max_workers: Maximum number of parallel workers
        """
        self.bxs_folder = validate_directory(bxs_folder, must_exist=True)
        self.output_dir = validate_directory(
            output_dir if output_dir else self.bxs_folder.parent / "processed",
            create_if_missing=True
        )
        self.subject_file = Path(subject_file) if subject_file else None
        self.parallel = parallel
        self.max_workers = max_workers
        
        # Load subject info if available
        self.subject_info = {}
        if self.subject_file and self.subject_file.exists():
            try:
                self.subject_info = parse_subject_file(self.subject_file).to_dict()
                logger.info(f"Loaded subject info: {self.subject_info.get('name', 'Unknown')}")
            except Exception as e:
                logger.warning(f"Failed to load subject info: {e}")
        
        # Processing state
        self.processed_files = []
        self.failed_files = []
        self.checkpoint_file = self.output_dir / ".batch_checkpoint.pkl"
        
        logger.info(f"Initialized BatchProcessor: {len(list(self.bxs_folder.glob('*.bxs')))} BXS files")
    
    def process_all(
        self,
        resume: bool = False,
        validate_output: bool = True
    ) -> Dict[str, any]:
        """
        Process all BXS files in folder.
        
        Args:
            resume: Resume from checkpoint if available
            validate_output: Validate each converted EDF
            
        Returns:
            Processing results dictionary
        """
        # Load checkpoint if resuming
        if resume:
            checkpoint = load_checkpoint(self.checkpoint_file)
            if checkpoint:
                self.processed_files = checkpoint.get('processed_files', [])
                logger.info(f"Resuming from checkpoint: {len(self.processed_files)} files already processed")
        
        # Find all BXS files
        bxs_files = list(self.bxs_folder.glob("*.bxs"))
        
        if not bxs_files:
            logger.warning(f"No BXS files found in {self.bxs_folder}")
            return {
                'total_files': 0,
                'processed': 0,
                'failed': 0,
                'processed_files': [],
                'failed_files': []
            }
        
        # Filter out already processed files
        if resume and self.processed_files:
            processed_stems = {Path(f).stem for f in self.processed_files}
            bxs_files = [f for f in bxs_files if f.stem not in processed_stems]
            logger.info(f"After resume filter: {len(bxs_files)} files remaining")
        
        # Initialize progress tracker
        total_files = len(bxs_files) + len(self.processed_files)
        progress = ProgressTracker(total_files, "Batch Processing")
        
        # Process files
        if self.parallel and len(bxs_files) > 1:
            results = self._process_parallel(bxs_files, progress, validate_output)
        else:
            results = self._process_sequential(bxs_files, progress, validate_output)
        
        # Update progress
        progress.finish()
        
        # Clear checkpoint on success
        if self.checkpoint_file.exists():
            clear_checkpoint(self.checkpoint_file)
        
        # Compile results
        final_results = {
            'total_files': total_files,
            'processed': len(self.processed_files),
            'failed': len(self.failed_files),
            'processed_files': self.processed_files,
            'failed_files': self.failed_files,
            'output_dir': str(self.output_dir)
        }
        
        logger.info(
            f"Batch processing complete: {final_results['processed']} processed, "
            f"{final_results['failed']} failed"
        )
        
        return final_results
    
    def _process_sequential(
        self,
        bxs_files: List[Path],
        progress: ProgressTracker,
        validate_output: bool
    ) -> Dict[str, any]:
        """Process files sequentially."""
        for bxs_file in bxs_files:
            try:
                logger.info(f"Processing {bxs_file.name}...")
                
                # Convert with annotations
                edf_path = convert_bxs_to_annotated_edf(
                    bxs_file,
                    self.output_dir,
                    subject_info=self.subject_info,
                    segment_durations=[60.0, 60.0, 60.0],
                    segment_labels=['EC', 'EO', 'EOT']
                )
                
                # Validate if requested
                if validate_output:
                    validate_conversion(edf_path, expected_duration=180.0)
                
                self.processed_files.append(str(edf_path))
                
                # Save checkpoint
                checkpoint_data = {
                    'processed_files': self.processed_files,
                    'failed_files': self.failed_files
                }
                save_checkpoint(self.checkpoint_file, checkpoint_data)
                
                progress.update(1, f"✓ {bxs_file.name}")
                
            except Exception as e:
                logger.error(f"✗ Failed to process {bxs_file.name}: {e}")
                self.failed_files.append({
                    'file': str(bxs_file),
                    'error': str(e)
                })
                progress.update(1, f"✗ {bxs_file.name}")
        
        return {'processed': len(self.processed_files), 'failed': len(self.failed_files)}
    
    def _process_parallel(
        self,
        bxs_files: List[Path],
        progress: ProgressTracker,
        validate_output: bool
    ) -> Dict[str, any]:
        """Process files in parallel."""
        def process_single_file(bxs_file: Path) -> Dict[str, any]:
            """Process a single BXS file."""
            try:
                edf_path = convert_bxs_to_annotated_edf(
                    bxs_file,
                    self.output_dir,
                    subject_info=self.subject_info,
                    segment_durations=[60.0, 60.0, 60.0],
                    segment_labels=['EC', 'EO', 'EOT']
                )
                
                if validate_output:
                    validate_conversion(edf_path, expected_duration=180.0)
                
                return {
                    'success': True,
                    'file': str(bxs_file),
                    'output': str(edf_path)
                }
            except Exception as e:
                return {
                    'success': False,
                    'file': str(bxs_file),
                    'error': str(e)
                }
        
        # Use ThreadPoolExecutor for I/O-bound operations
        with ThreadPoolExecutor(max_workers=self.max_workers) as executor:
            futures = {executor.submit(process_single_file, f): f for f in bxs_files}
            
            for future in as_completed(futures):
                result = future.result()
                
                if result['success']:
                    self.processed_files.append(result['output'])
                    progress.update(1, f"✓ {Path(result['file']).name}")
                else:
                    self.failed_files.append({
                        'file': result['file'],
                        'error': result['error']
                    })
                    progress.update(1, f"✗ {Path(result['file']).name}")
                
                # Save checkpoint periodically
                if len(self.processed_files) % 5 == 0:
                    checkpoint_data = {
                        'processed_files': self.processed_files,
                        'failed_files': self.failed_files
                    }
                    save_checkpoint(self.checkpoint_file, checkpoint_data)
        
        return {'processed': len(self.processed_files), 'failed': len(self.failed_files)}
    
    def resume_from_checkpoint(self) -> Dict[str, any]:
        """Resume processing from checkpoint."""
        return self.process_all(resume=True)
    
    def validate_batch(self) -> Dict[str, any]:
        """
        Validate all processed EDF files.
        
        Returns:
            Validation results dictionary
        """
        validation_results = {
            'total': len(self.processed_files),
            'valid': 0,
            'invalid': 0,
            'errors': []
        }
        
        for edf_path_str in self.processed_files:
            try:
                validate_conversion(edf_path_str, expected_duration=180.0)
                validation_results['valid'] += 1
            except Exception as e:
                validation_results['invalid'] += 1
                validation_results['errors'].append({
                    'file': edf_path_str,
                    'error': str(e)
                })
        
        logger.info(
            f"Batch validation: {validation_results['valid']} valid, "
            f"{validation_results['invalid']} invalid"
        )
        
        return validation_results
    
    def generate_report(self) -> Dict[str, any]:
        """
        Generate processing report.
        
        Returns:
            Report dictionary
        """
        report = {
            'timestamp': datetime.now().isoformat(),
            'bxs_folder': str(self.bxs_folder),
            'output_dir': str(self.output_dir),
            'subject_info': self.subject_info,
            'statistics': {
                'total_files': len(self.processed_files) + len(self.failed_files),
                'processed': len(self.processed_files),
                'failed': len(self.failed_files),
                'success_rate': (
                    len(self.processed_files) / 
                    (len(self.processed_files) + len(self.failed_files)) * 100
                    if (len(self.processed_files) + len(self.failed_files)) > 0 else 0
                )
            },
            'processed_files': self.processed_files,
            'failed_files': self.failed_files
        }
        
        # Save report to file
        report_file = self.output_dir / "batch_processing_report.json"
        with open(report_file, 'w', encoding='utf-8') as f:
            json.dump(report, f, indent=2)
        
        logger.info(f"Generated processing report: {report_file.name}")
        
        return report

def process_bxs_batch(
    bxs_folder: Union[str, Path],
    subject_file: Optional[Union[str, Path]] = None,
    output_dir: Optional[Union[str, Path]] = None,
    process_rapid_reporter: bool = False,
    parallel: bool = True,
    resume: bool = False
) -> Dict[str, any]:
    """
    Process all BXS files in a folder.
    
    Args:
        bxs_folder: Folder containing BXS files
        subject_file: Optional path to Subject.txt file
        output_dir: Output directory
        process_rapid_reporter: If True, process through rapid_reporter CLI
        parallel: Enable parallel processing
        resume: Resume from checkpoint if available
        
    Returns:
        Processing results dictionary
    """
    processor = BatchProcessor(
        bxs_folder=bxs_folder,
        subject_file=subject_file,
        output_dir=output_dir,
        parallel=parallel
    )
    
    # Process all files
    results = processor.process_all(resume=resume)
    
    # Generate report
    report = processor.generate_report()
    
    # Optional: Process through rapid_reporter
    if process_rapid_reporter and results['processed'] > 0:
        logger.info("Processing through rapid_reporter CLI...")
        try:
            # Import and use rapid_reporter CLI
            import sys
            parent_dir = Path(__file__).parent.parent
            if str(parent_dir) not in sys.path:
                sys.path.insert(0, str(parent_dir))
            
            from cli import run_cli
            import argparse
            
            # Create mock args
            class Args:
                def __init__(self, files, output):
                    self.files = files
                    self.output = output
                    self.validate = True
                    self.site_map = "site_map.yaml"
            
            args = Args(
                files=processor.processed_files,
                output=str(processor.output_dir / "rapid_reporter_output")
            )
            
            run_cli(args)
            logger.info("✓ Rapid reporter processing complete")
            
        except Exception as e:
            logger.warning(f"Rapid reporter processing failed: {e}")
    
    return results

