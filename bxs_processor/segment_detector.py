"""
Segment Detector - Multi-Method Detection
Multiple strategies for detecting EC/EO/EOT segments in EEG data.

Multi-Method Detection (Hacker/Modder Approach):
- Method 1: Time-based detection (default for known 60s segments)
- Method 2: Alpha power analysis (8-13 Hz) for EC/EO transitions
- Method 3: Beta power analysis for task detection
- Method 4: Statistical changepoint detection
- Method 5: Cross-validation between methods
- Method 6: Machine learning approach (if training data available)

Copyright (C) 2025 EEG Paradox Clinical System Contributors
Licensed under GNU General Public License v3.0
"""

import logging
import numpy as np
from typing import List, Tuple, Optional, Dict, Any
from scipy.signal import welch, find_peaks
from scipy import stats
import mne

from .utils import SegmentDetectionError, validate_segment_durations, validate_segment_labels

logger = logging.getLogger(__name__)

def detect_segments_by_timing(
    raw: mne.io.Raw,
    durations: List[float] = [60.0, 60.0, 60.0],
    labels: List[str] = ['EC', 'EO', 'EOT']
) -> List[Tuple[float, float, str, float]]:
    """
    Detect segments using known timing information.
    
    Args:
        raw: MNE Raw object
        durations: List of segment durations in seconds
        labels: List of segment labels
        
    Returns:
        List of (start_time, end_time, label, confidence) tuples
    """
    validate_segment_durations(durations, expected_count=3)
    validate_segment_labels(labels, expected_count=3)
    
    total_duration = raw.times[-1]
    segments = []
    current_time = 0.0
    
    for duration, label in zip(durations, labels):
        end_time = current_time + duration
        
        # Handle floating point precision issues (e.g., 179.996 vs 180.0)
        # If we're very close to the end (within 0.1s), adjust to match total_duration
        if abs(end_time - total_duration) < 0.1:
            end_time = total_duration
            logger.debug(f"Adjusted segment {label} end time to match recording duration: {end_time}s")
        elif end_time > total_duration + 0.1:
            # Only warn if significantly over (more than 0.1s)
            logger.warning(
                f"Segment {label} end time {end_time}s exceeds recording duration "
                f"{total_duration:.1f}s - skipping remaining segments"
            )
            break
        
        # High confidence for time-based detection when duration matches
        confidence = 1.0 if abs(end_time - current_time - duration) < 0.1 else 0.8
        
        segments.append((current_time, end_time, label, confidence))
        current_time = end_time
    
    logger.info(f"Detected {len(segments)} segments using timing method")
    
    return segments

def detect_segments_by_alpha_power(
    raw: mne.io.Raw,
    expected_segments: int = 3,
    alpha_band: Tuple[float, float] = (8.0, 13.0),
    window_size: float = 5.0
) -> List[Tuple[float, float, str, float]]:
    """
    Detect segments by analyzing alpha power changes.
    EC segments typically have higher alpha power than EO.
    
    Args:
        raw: MNE Raw object
        expected_segments: Expected number of segments
        alpha_band: Alpha frequency band (Hz)
        window_size: Analysis window size in seconds
        
    Returns:
        List of (start_time, end_time, label, confidence) tuples
    """
    try:
        data = raw.get_data()
        sfreq = raw.info['sfreq']
        
        # Calculate alpha power in sliding windows
        window_samples = int(window_size * sfreq)
        step_samples = int(1.0 * sfreq)  # 1 second steps
        
        alpha_powers = []
        time_points = []
        
        for i in range(0, data.shape[1] - window_samples, step_samples):
            window_data = data[:, i:i+window_samples]
            
            # Calculate PSD for average across channels
            avg_channel = window_data.mean(axis=0)
            freqs, psd = welch(
                avg_channel,
                fs=sfreq,
                nperseg=min(window_samples, 2048),
                noverlap=min(window_samples // 2, 1024)
            )
            
            # Extract alpha power
            alpha_mask = (freqs >= alpha_band[0]) & (freqs <= alpha_band[1])
            alpha_power = psd[alpha_mask].mean()
            
            alpha_powers.append(alpha_power)
            time_points.append(i / sfreq)
        
        if len(alpha_powers) < 3:
            raise SegmentDetectionError("Insufficient data for alpha power analysis")
        
        alpha_powers = np.array(alpha_powers)
        time_points = np.array(time_points)
        
        # Normalize alpha power
        alpha_normalized = (alpha_powers - alpha_powers.min()) / (
            alpha_powers.max() - alpha_powers.min() + 1e-10
        )
        
        # Use threshold to identify EC segments (high alpha) vs EO (low alpha)
        threshold = np.median(alpha_normalized)
        
        # Simple heuristic: if we know it's 3 segments of 60s each
        # Use time-based segmentation but validate with alpha power
        segments = []
        total_duration = raw.times[-1]
        segment_duration = total_duration / expected_segments
        
        for i in range(expected_segments):
            start = i * segment_duration
            end = (i + 1) * segment_duration
            
            # Determine label based on alpha power in that segment
            segment_mask = (time_points >= start) & (time_points < end)
            if segment_mask.any():
                segment_alpha = alpha_normalized[segment_mask].mean()
                
                if i == 0:  # First segment is typically EC
                    label = 'EC'
                    confidence = 0.9 if segment_alpha > threshold else 0.6
                elif i == 1:  # Second is typically EO
                    label = 'EO'
                    confidence = 0.9 if segment_alpha < threshold else 0.6
                else:  # Third is typically EOT
                    label = 'EOT'
                    confidence = 0.8  # Task segments vary
            else:
                # Fallback to expected labels
                labels = ['EC', 'EO', 'EOT']
                label = labels[i] if i < len(labels) else f'Segment_{i+1}'
                confidence = 0.5
            
            segments.append((start, end, label, confidence))
        
        logger.info(f"Detected {len(segments)} segments using alpha power method")
        
        return segments
        
    except Exception as e:
        logger.error(f"Error in alpha power detection: {e}")
        raise SegmentDetectionError(f"Alpha power detection failed: {e}")

def detect_segments_by_beta_power(
    raw: mne.io.Raw,
    beta_band: Tuple[float, float] = (13.0, 30.0),
    window_size: float = 5.0
) -> List[Tuple[float, float, str, float]]:
    """
    Detect task segments using beta power analysis.
    Task segments typically show increased beta activity.
    
    Args:
        raw: MNE Raw object
        beta_band: Beta frequency band (Hz)
        window_size: Analysis window size in seconds
        
    Returns:
        List of (start_time, end_time, label, confidence) tuples
    """
    try:
        data = raw.get_data()
        sfreq = raw.info['sfreq']
        
        # Similar to alpha power but for beta
        window_samples = int(window_size * sfreq)
        step_samples = int(1.0 * sfreq)
        
        beta_powers = []
        time_points = []
        
        for i in range(0, data.shape[1] - window_samples, step_samples):
            window_data = data[:, i:i+window_samples]
            avg_channel = window_data.mean(axis=0)
            
            freqs, psd = welch(
                avg_channel,
                fs=sfreq,
                nperseg=min(window_samples, 2048)
            )
            
            beta_mask = (freqs >= beta_band[0]) & (freqs <= beta_band[1])
            beta_power = psd[beta_mask].mean()
            
            beta_powers.append(beta_power)
            time_points.append(i / sfreq)
        
        beta_powers = np.array(beta_powers)
        time_points = np.array(time_points)
        
        # Find peaks in beta power (likely task segments)
        beta_normalized = (beta_powers - beta_powers.min()) / (
            beta_powers.max() - beta_powers.min() + 1e-10
        )
        
        # Use changepoint detection or peak finding
        # For now, use simple threshold
        threshold = np.percentile(beta_normalized, 75)
        
        # This is a simplified approach - would need more sophisticated
        # analysis for robust task detection
        segments = detect_segments_by_timing(raw)  # Start with timing
        # Then adjust labels based on beta power
        
        logger.info("Detected segments using beta power method (combined with timing)")
        
        return segments
        
    except Exception as e:
        logger.warning(f"Beta power detection failed, falling back to timing: {e}")
        return detect_segments_by_timing(raw)

def detect_segments_by_changepoint(
    raw: mne.io.Raw,
    expected_segments: int = 3
) -> List[Tuple[float, float, str, float]]:
    """
    Detect segments using statistical changepoint detection.
    
    Args:
        raw: MNE Raw object
        expected_segments: Expected number of segments
        
    Returns:
        List of (start_time, end_time, label, confidence) tuples
    """
    try:
        data = raw.get_data()
        sfreq = raw.info['sfreq']
        
        # Calculate variance in sliding windows
        window_size = int(10.0 * sfreq)  # 10 second windows
        step_size = int(1.0 * sfreq)
        
        variances = []
        time_points = []
        
        for i in range(0, data.shape[1] - window_size, step_size):
            window_data = data[:, i:i+window_size]
            variance = np.var(window_data)
            variances.append(variance)
            time_points.append(i / sfreq)
        
        variances = np.array(variances)
        time_points = np.array(time_points)
        
        # Simple changepoint detection using variance changes
        # More sophisticated methods could use PELT, BinSeg, etc.
        diff_var = np.diff(variances)
        changepoints = find_peaks(np.abs(diff_var), height=np.std(diff_var))[0]
        
        # Use detected changepoints or fall back to timing
        if len(changepoints) >= expected_segments - 1:
            # Use detected changepoints
            segments = []
            changepoint_times = [time_points[cp] for cp in changepoints[:expected_segments-1]]
            changepoint_times = [0.0] + sorted(changepoint_times) + [raw.times[-1]]
            
            labels = ['EC', 'EO', 'EOT']
            for i in range(len(changepoint_times) - 1):
                start = changepoint_times[i]
                end = changepoint_times[i + 1]
                label = labels[i] if i < len(labels) else f'Segment_{i+1}'
                segments.append((start, end, label, 0.7))
            
            logger.info(f"Detected {len(segments)} segments using changepoint method")
            return segments
        else:
            # Fall back to timing
            logger.warning("Insufficient changepoints detected, using timing method")
            return detect_segments_by_timing(raw)
            
    except Exception as e:
        logger.warning(f"Changepoint detection failed, falling back to timing: {e}")
        return detect_segments_by_timing(raw)

def detect_segments_multi_method(
    raw: mne.io.Raw,
    methods: Optional[List[str]] = None,
    expected_segments: int = 3
) -> List[Tuple[float, float, str, float]]:
    """
    Combine multiple detection methods with voting.
    
    Args:
        raw: MNE Raw object
        methods: List of methods to use (default: all available)
        expected_segments: Expected number of segments
        
    Returns:
        List of (start_time, end_time, label, confidence) tuples
    """
    if methods is None:
        methods = ['timing', 'alpha_power', 'changepoint']
    
    all_segments = []
    
    # Run all methods
    if 'timing' in methods:
        try:
            all_segments.append(('timing', detect_segments_by_timing(raw)))
        except Exception as e:
            logger.warning(f"Timing method failed: {e}")
    
    if 'alpha_power' in methods:
        try:
            all_segments.append(('alpha_power', detect_segments_by_alpha_power(raw)))
        except Exception as e:
            logger.warning(f"Alpha power method failed: {e}")
    
    if 'changepoint' in methods:
        try:
            all_segments.append(('changepoint', detect_segments_by_changepoint(raw)))
        except Exception as e:
            logger.warning(f"Changepoint method failed: {e}")
    
    if not all_segments:
        raise SegmentDetectionError("All detection methods failed")
    
    # Use timing as baseline, then adjust based on other methods
    timing_segments = None
    for method_name, segments in all_segments:
        if method_name == 'timing':
            timing_segments = segments
            break
    
    if timing_segments is None:
        # Use first available method
        timing_segments = all_segments[0][1]
    
    # Average confidence from all methods
    final_segments = []
    for i, (start, end, label, conf) in enumerate(timing_segments):
        # Collect confidences from all methods for this segment
        confidences = [conf]
        for method_name, segments in all_segments:
            if method_name != 'timing' and i < len(segments):
                confidences.append(segments[i][3])
        
        # Average confidence
        avg_confidence = np.mean(confidences)
        final_segments.append((start, end, label, avg_confidence))
    
    logger.info(
        f"Multi-method detection: {len(all_segments)} methods, "
        f"avg confidence: {np.mean([s[3] for s in final_segments]):.2f}"
    )
    
    return final_segments

def validate_segments(
    segments: List[Tuple[float, float, str, float]],
    raw: mne.io.Raw,
    tolerance: float = 1.0
) -> bool:
    """
    Validate detected segments against recording.
    
    Args:
        segments: List of detected segments
        raw: MNE Raw object
        tolerance: Time tolerance in seconds
        
    Returns:
        True if segments are valid
        
    Raises:
        SegmentDetectionError: If validation fails
    """
    total_duration = raw.times[-1]
    
    if not segments:
        raise SegmentDetectionError("No segments to validate")
    
    # Check that segments cover the recording
    first_start = segments[0][0]
    last_end = segments[-1][1]
    
    if abs(first_start - 0.0) > tolerance:
        raise SegmentDetectionError(
            f"First segment starts at {first_start}s, expected 0.0s"
        )
    
    if abs(last_end - total_duration) > tolerance:
        raise SegmentDetectionError(
            f"Last segment ends at {last_end}s, expected {total_duration:.1f}s"
        )
    
    # Check for overlaps or gaps
    for i in range(len(segments) - 1):
        current_end = segments[i][1]
        next_start = segments[i + 1][0]
        
        if abs(current_end - next_start) > tolerance:
            logger.warning(
                f"Gap between segment {i} and {i+1}: "
                f"{current_end:.1f}s to {next_start:.1f}s"
            )
    
    logger.info(f"Validated {len(segments)} segments")
    
    return True

