"""Read recording context and estimate a selection before spectral processing."""
from __future__ import annotations

from pathlib import Path
from bisect import bisect_left,bisect_right
import math

import numpy as np

from .adapters import _AUXILIARY
from .intake import FORMATS,open_recording,sha256,suffix,native_channel_types
from .recording_context import native_recording_context


def inspect_recording(path,*,sample_rate=None,unit=None,converter_root=None):
    """No filtering, spectral calculation, native conversion or source writes.

    Standard formats use the same calibrated reader as a run. Native WinEEG
    inspection reads its header/descriptors; a run still requires the full audit.
    Tabular formats must be decoded to establish their declared timebase.
    """
    path=Path(path).resolve(strict=True);ext=suffix(path)
    if ext=='.bxs':raise ValueError('BXS preview needs its calibrated conversion. Run once, then inspect the retained EDF under intake/.')
    if ext=='.eeg':
        if path.with_suffix('.vhdr').exists():raise ValueError('Select the BrainVision .vhdr header instead of its EEG binary companion')
        from EEG_Paradox_Auto_Pipeline.eeg_auto_pipeline.native_intake import _converter,CONVERTER_ROOT
        core,_=_converter(Path(converter_root or CONVERTER_ROOT));eeg=core.read_legacy_eeg(path)
        frames=eeg.header['logical_frame_count']
        if not 0<frames<=eeg.physical_frame_count:raise ValueError('Native logical frame count is outside the physical samples')
        names=[c.label for c in eeg.channels];types=native_channel_types(eeg);kinds=[types[name] for name in names]
        units={name:'' if kind=='stim' else 'uV' for name,kind in zip(names,kinds)}
        bads=[];rate=float(eeg.header['sample_rate_hz']);origin=0.;annotations=eeg.header['primary_mark_count']+eeg.header['extended_mark_count']
        source_hash=sha256(path)
        context=native_recording_context(eeg)
        import hashlib
        if hashlib.sha256(eeg.raw).hexdigest()!=source_hash:raise ValueError('Recording changed during inspection')
        note='Native descriptor preview only. Full conversion, calibration and event audits are still required by Run.'
    else:
        with open_recording(path,sample_rate=sample_rate,unit=unit) as (raw,provenance):
            names=list(raw.ch_names);kinds=raw.get_channel_types();bads=raw.info.get('bads',[])
            units=provenance.get('physical_units',{})
            if not units:units={name:getattr(raw,'_eeg_paradox_data_unit','V') for name in names}
            rate=float(raw.info['sfreq']);frames=raw.n_times;origin=float(getattr(raw,'first_time',0.));annotations=len(raw.annotations)
            source_hash=provenance['source_sha256'];note='Same calibrated reader as Run; source/companion integrity checked. No spectral processing performed.'
            context=provenance['recording_context']
    channels=[]
    for name,kind in zip(names,kinds):
        reason='marked bad' if name in bads else 'auxiliary label' if _AUXILIARY.search(name) else 'non-EEG type' if kind!='eeg' else ''
        physical_name=next((key for key in units if key==name or key.removeprefix('EEG ')==name),name)
        channels.append({'name':name,'type':kind,'unit':units.get(physical_name,'unknown'),
                         'eligible':not bool(reason),'excluded_reason':reason})
    return {'source':path.name,'source_sha256':source_hash,'format':FORMATS[ext],
            'sample_rate_hz':rate,'samples':int(frames),'duration_seconds':frames/rate,
            'origin_seconds':origin,'annotations':int(annotations),'channels':channels,'notice':note,
            'recording_context':context}


def estimate_selection(info,options,channels=None):
    """Estimate the exact sample grid and spectral storage without allocating it."""
    rate=info['sample_rate_hz'];cfg=options.config
    if not np.isfinite([options.start,rate,cfg.window_seconds,cfg.hop_seconds,cfg.fmin_hz,cfg.fmax_hz]).all():
        raise ValueError('Selection settings must be finite')
    if options.start<0 or not 0<cfg.hop_seconds<=cfg.window_seconds or not 0<=cfg.fmin_hz<cfg.fmax_hz<=rate/2:
        raise ValueError('Selection/FFT settings do not fit this recording and Nyquist limit')
    eligible=[item['name'] for item in info['channels'] if item['eligible']]
    names=list(channels if channels is not None else options.channels or eligible)
    if not names or len(set(names))!=len(names) or not set(names)<=set(eligible) or len(names)>64:
        raise ValueError('Select 1–64 unique eligible EEG channels')
    start=int(round(options.start*rate));stop=info['samples']
    if options.duration is not None:
        if not math.isfinite(options.duration) or options.duration<=0:raise ValueError('Duration must be positive and finite')
        stop=min(stop,start+int(round(options.duration*rate)))
    window=int(round(cfg.window_seconds*rate));hop=int(round(cfg.hop_seconds*rate))
    if window<8 or hop<1 or stop-start<window:raise ValueError('Selection needs at least one complete FFT window')
    count=(stop-start-window)//hop+1
    # Match rfftfreq's floating-point grid exactly, without allocating its array.
    # A tolerance around bin boundaries can include a bin that Run excludes.
    spacing=1./(window*(1./rate));indices=range(window//2+1)
    frequency=lambda index:index*spacing
    bins=bisect_right(indices,cfg.fmax_hz,key=frequency)-bisect_left(indices,cfg.fmin_hz,key=frequency)
    if bins<2:raise ValueError('Frequency range needs at least two unpadded FFT bins')
    cells=len(names)*count*bins
    return {'channels':names,'window_count':count,'frequency_bins':bins,'spectral_cells':cells,
            'limit_cells':cfg.max_cells,'fits_limit':cells<=cfg.max_cells,
            'psd_megabytes':cells*4/1e6,'start_seconds':info['origin_seconds']+start/rate,
            'stop_seconds':info['origin_seconds']+stop/rate,'window_seconds':window/rate,'hop_seconds':hop/rate}
