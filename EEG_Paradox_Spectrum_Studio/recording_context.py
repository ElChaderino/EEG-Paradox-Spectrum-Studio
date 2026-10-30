"""Carry file-backed display geometry and channel clock evidence into review.

Stored channel alignment is distinct from hardware simultaneity. No reference,
measured anatomy or acquisition history is inferred from labels or filenames.
SPDX-License-Identifier: GPL-3.0-only
"""
from __future__ import annotations

import copy
import re

import numpy as np


SCHEMA = 'eeg-paradox.recording-context.v1'
_DIFFERENTIAL = re.compile(r'[-–/]')


def scalp_time_aligned(metadata):
    if metadata.get('simultaneous') is False:
        return False
    timing = metadata.get('recording_context', {}).get('timing', {})
    if timing.get('aligned') is False:
        return False
    return metadata.get('simultaneous') is True or timing.get('aligned') is True


def timing_description(metadata):
    if metadata.get('simultaneous') is False:
        return 'Recorded separately (operator declaration)'
    timing = metadata.get('recording_context', {}).get('timing', {})
    if timing.get('aligned') is False:
        return 'Recorded separately (file declaration)'
    if metadata.get('simultaneous') is True:
        return 'Recorded together (operator declaration)'
    if timing.get('aligned') is True:
        return 'Shared file clock (detected); hardware simultaneity unconfirmed'
    return 'Channel timing unknown'


def _context(timing, positions=None, source=None, **extra):
    return {'schema': SCHEMA, 'timing': timing, 'positions_xy': positions or {},
            'position_source': source, **extra}


def native_recording_context(eeg):
    """Read WinEEG's stored channel descriptors, not a label-derived template.

    Descriptor fields 0x14/0x16 store the montage's theta/phi in degrees.
    WinEEG's documented axes are right / anterior / up. A display-only
    azimuthal projection uses theta / 90 as normalized scalp radius.
    See WinEEG User Manual, Montage Parameters: Electrodes dialog, pp148-149.
    These are saved montage coordinates; they are not digitized anatomy.
    """
    header = eeg.header
    complete = (header['logical_frame_count'] == eeg.physical_frame_count
                and eeg.physical_frame_count > 0 and eeg.trailing_sample_bytes == 0)
    timing = {'aligned': True if complete else None,
              'source': 'WinEEG native time-major channel frames',
              'sample_rate_hz': float(header['sample_rate_hz']),
              'logical_frames': int(header['logical_frame_count']),
              'physical_frames': int(eeg.physical_frame_count),
              'trailing_sample_bytes': int(eeg.trailing_sample_bytes),
              'meaning': 'Channels share stored sample frames; hardware simultaneity and prior processing are not established.'}
    positions, descriptors, omitted = {}, {}, []
    for channel in eeg.channels:
        name = channel.label
        theta, phi = channel.x, channel.y
        # Reference/auxiliary descriptors often contain placeholder zeroes.
        # Differential channel names cannot be represented by a single sensor.
        if (channel.kind != 0 or _DIFFERENTIAL.search(name) or
                not 0 <= theta <= 90 or not 0 <= phi <= 360 or
                theta == 0 and name.upper() != 'CZ'):
            omitted.append(name)
            continue
        angle = np.deg2rad(phi)
        positions[name] = [float(theta / 90 * np.cos(angle)), float(theta / 90 * np.sin(angle))]
        descriptors[name] = {'theta_degrees': theta, 'phi_degrees': phi}
    return _context(timing, positions,
                    'WinEEG embedded montage; schematic polar projection; not measured electrode positions',
                    native_coordinates=descriptors, unpositioned_channels=omitted)


def raw_recording_context(raw, *, file_format=None, sampling_rates=None):
    """Use reader-supplied positions, with no standard montage lookup.

    File readers expose a common stored time axis. Different-rate EDF/BDF
    channels are kept unknown here because their reader can resample them.
    In-memory arrays alone establish no acquisition timing.
    """
    existing = getattr(raw, '_spectrum_recording_context', None)
    if existing is not None:
        return copy.deepcopy(existing)
    aligned = True if file_format else None
    reason = str(file_format) + ' stored channel time axis' if file_format else 'In-memory signals; acquisition timing unspecified'
    if file_format == 'GDF':
        aligned = None
        reason = 'GDF original channel sampling rates have not been verified'
    if sampling_rates is not None:
        from .adapters import _eeg_picks
        try:
            picks = _eeg_picks(raw, None)
        except ValueError:
            picks = []
        if not picks or any(sampling_rates.get(name) is None or
                            not np.isclose(sampling_rates[name], raw.info['sfreq'], rtol=0, atol=1e-9) for name in picks):
            aligned = None
            reason = 'Unequal or unresolved original channel rates; reader resampling cannot confirm acquisition alignment'
    timing = {'aligned': aligned, 'source': reason,
              'meaning': 'Stored time alignment only; hardware simultaneity and prior processing are not established.'}
    positions = {}
    getter = getattr(raw, 'get_montage', None)
    montage = getter() if getter else None
    if montage is not None:
        geometry = montage.get_positions()
        # Unknown/MRI/device coordinates need an explicit transform first.
        if geometry['coord_frame'] == 'head':
            for name, point in geometry['ch_pos'].items():
                if _DIFFERENTIAL.search(name):
                    continue
                x, y, z = np.asarray(point, dtype=float)
                radial = np.hypot(x, y)
                if not np.isfinite([x, y, z]).all() or np.linalg.norm(point) < 1e-6:
                    continue
                radius = np.arctan2(radial, z) / (np.pi / 2)
                if radius > 1.5:
                    continue
                positions[name] = [float(radius*x/radial) if radial else 0.,
                                   float(radius*y/radial) if radial else 0.]
    source = 'Embedded reader montage in head coordinates; display projection; measurement status unconfirmed' if positions else None
    return _context(timing, positions, source)


def declared_recording_context(obj):
    """Optional JSON/NPZ metadata is a file declaration, never a guessed layout."""
    simultaneous = obj.get('simultaneous')
    if simultaneous is not None and type(simultaneous) is not bool:
        raise ValueError('Embedded simultaneous must be true, false or null')
    positions = obj.get('positions_xy', {})
    source = obj.get('position_source')
    names = obj.get('channels', obj.get('channel_names', []))
    # NPZ can encode an ordered numeric matrix without enabling pickle.
    if isinstance(positions, list) and len(positions) == len(names):
        positions = dict(zip(names, positions))
    if not isinstance(positions, dict) or not set(positions) <= set(names):
        raise ValueError('Embedded positions_xy must use existing channel names')
    if positions and (not isinstance(source, str) or not source.strip()):
        raise ValueError('Embedded positions_xy need a position_source')
    for point in positions.values():
        if (not isinstance(point, (list, tuple)) or len(point) != 2 or
                not all(type(v) in (int, float) for v in point) or
                not np.isfinite(point).all() or np.linalg.norm(point) > 1.5):
            raise ValueError('Embedded positions must be finite normalized scalp XY within radius 1.5')
    return _context({'aligned': simultaneous, 'source': 'JSON/NPZ simultaneous declaration',
                     'meaning': 'Timing declared by the signal file author.'},
                    positions, 'Signal file declaration: ' + source if positions else None)


def attach_recording_context(result, context):
    from .review import attach_review
    context = copy.deepcopy(context)
    names = result.metadata['channel_names']
    context['positions_xy'] = {name: value for name, value in context['positions_xy'].items() if name in names}
    if context['timing'].get('aligned') is False and result.metadata.get('simultaneous') is True:
        raise ValueError('Operator timing conflicts with the signal file declaration of sequential channels')
    result.metadata['recording_context'] = context
    positions = context['positions_xy']
    if positions:
        attach_review(result, positions=positions, position_source=context['position_source'])
    return result
