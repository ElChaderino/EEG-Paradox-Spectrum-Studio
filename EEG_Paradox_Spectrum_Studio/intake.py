"""Calibrated file intake, using the main setup's readers and native converters.

SPDX-License-Identifier: GPL-3.0-only
"""
from __future__ import annotations

import csv
import hashlib
import json
import re
from contextlib import contextmanager
from pathlib import Path

import numpy as np

from .spectral import UNIT_TO_UV
from .recording_context import declared_recording_context, native_recording_context, raw_recording_context

FORMATS = {'.edf':'EDF/EDF+', '.bdf':'BDF/BDF+', '.fif':'MNE FIF', '.fif.gz':'MNE FIF gzip',
           '.gdf':'GDF', '.vhdr':'BrainVision header', '.cnt':'Neuroscan CNT', '.set':'EEGLAB',
           '.csv':'CSV signal table', '.tsv':'TSV signal table', '.json':'JSON signals',
           '.npz':'Calibrated signal NPZ', '.bxs':'BioExplorer BXS (setup converter)',
           '.eeg':'WinEEG native (setup converter)'}


def suffix(path):
    return '.fif.gz' if str(path).lower().endswith('.fif.gz') else Path(path).suffix.lower()


def sha256(path):
    digest=hashlib.sha256()
    with Path(path).open('rb') as handle:
        for chunk in iter(lambda:handle.read(1024*1024),b''):digest.update(chunk)
    return digest.hexdigest()


def native_channel_types(eeg):
    """Keep native polygraphic, marker and synchronization lanes out of EEG.

    Codes follow the maintained converter's descriptor table. Kind 2 reference
    electrodes remain EEG; unfamiliar codes keep the reader's EEG default.
    """
    kinds={1:'misc',4:'stim',5:'stim'}
    return {channel.label:kinds.get(channel.kind,'eeg') for channel in eeg.channels}


def _unit(value):
    value=str(value).strip()
    aliases={'v':'V','mv':'mV','uv':'uV','µv':'uV','μv':'uV','nv':'nV','u':'uV'}
    if value.lower() not in aliases:raise ValueError('Declare calibrated V, mV, uV or nV; signal units are never guessed')
    return aliases[value.lower()]


class ArrayRecording:
    def __init__(self,data,names,rate,unit,*,first_time=0,annotations=(),channel_types=None):
        self.values=np.asarray(data,dtype=np.float64)
        if self.values.ndim!=2 or self.values.shape[0]!=len(names) or not 1<=len(names)<=64:
            raise ValueError('Signals must be 1-64 channels by samples, with matching channel names')
        if not np.isfinite(rate) or rate<=0 or not np.isfinite(first_time) or first_time<0:
            raise ValueError('Sample rate must be positive; original-time origin must be nonnegative')
        if len(set(names))!=len(names) or any(not isinstance(x,str) or not x.strip() for x in names):
            raise ValueError('Channel labels must be unique nonempty strings')
        self.ch_names=list(names);self.info={'sfreq':float(rate),'bads':[]};self.first_time=float(first_time)
        self._eeg_paradox_data_unit=_unit(unit)
        self.kinds=list(channel_types) if channel_types is not None else ['eeg']*len(names)
        if len(self.kinds)!=len(names):raise ValueError('Channel types must match channel names')
        self.annotations=list(annotations)
    @property
    def n_times(self):return self.values.shape[1]
    def get_channel_types(self):return self.kinds
    def get_data(self,picks=None,start=0,stop=None):
        indices=[self.ch_names.index(x) for x in picks] if picks is not None else list(range(len(self.ch_names)))
        return self.values[indices,start:stop]
    def close(self):pass


class _PrefixUnitRecording:
    """Decode the main BXS converter's 'u' dimension from physical EDF samples.

    MNE treats this prefix-only spelling as unscaled. Use pyEDFlib's calibrated
    physical values for those channels instead of assuming MNE has converted
    them to volts. Standard EDF channels retain the original MNE data path.
    """
    def __init__(self,raw,path,units):
        import pyedflib
        self.raw=raw;self.reader=pyedflib.EdfReader(str(path));self.indices={}
        for index,label in enumerate(self.reader.getSignalLabels()):
            if units[label].lower()=='u':
                name=next((name for name in raw.ch_names if name==label or re.sub(r'^EEG\s+','',label)==name),None)
                if name is not None:self.indices[name]=index
    def __getattr__(self,name):return getattr(self.raw,name)
    def get_data(self,picks=None,start=0,stop=None):
        names=list(picks) if picks is not None else list(self.raw.ch_names)
        data=self.raw.get_data(picks=names,start=start,stop=stop)
        for row,name in enumerate(names):
            if name not in self.indices:continue
            index=self.indices[name]
            if not np.isclose(self.reader.getSampleFrequency(index),self.info['sfreq']):
                raise ValueError("Prefix-unit EDF channels need a common sampling rate; use calibrated uniform-rate intake")
            values=self.reader.readSignal(index,start=start,n=data.shape[1])
            if len(values)!=data.shape[1]:raise ValueError('EDF physical samples do not match the common recording clock')
            data[row]=values*1e-6
        return data
    def close(self):
        try:self.reader.close()
        finally:self.raw.close()


def _annotations(items,rate,origin):
    normalized=[]
    if not isinstance(items,list):raise ValueError('Annotations must be a list')
    for item in items:
        onset=item.get('onset_seconds',item.get('onset_s',item.get('onset')))
        if onset is None and 'sample' in item:onset=origin+float(item['sample'])/rate
        duration=float(item.get('duration_seconds',item.get('duration_s',item.get('duration',0))))
        if onset is None or not np.isfinite([float(onset),duration]).all() or float(onset)<0 or duration<0:
            raise ValueError('Annotations need finite original-time onset and nonnegative duration')
        normalized.append({'onset':float(onset),'duration':duration,
                           'description':str(item.get('description',item.get('label',item.get('code','event'))))})
    return normalized


def _tabular(path,rate,unit):
    ext=suffix(path)
    if ext in ('.csv','.tsv'):
        with path.open(encoding='utf-8-sig',newline='') as handle:
            reader=csv.reader(handle,delimiter='\t' if ext=='.tsv' else ',')
            names=next(reader,[]);rows=list(reader)
        if not rows or not names:raise ValueError('Signal table is empty')
        try:values=np.asarray(rows,dtype=float)
        except (TypeError,ValueError) as exc:raise ValueError('Every signal table row must contain numeric channel samples') from exc
        if values.ndim!=2 or values.shape[1]!=len(names):raise ValueError('Signal table columns do not match its header')
        columns=[i for i,name in enumerate(names) if name.strip().lower() in ('time','time_seconds','timestamp')]
        if len(columns)>1:raise ValueError('Use only one original-time column')
        origin=0.
        if columns:
            index=columns[0];times=values[:,index];diff=np.diff(times)
            if len(diff)<1 or not np.isfinite(times).all() or times[0]<0 or not (diff>0).all():
                raise ValueError('Time column must be finite, nonnegative and strictly increasing in seconds')
            inferred=1/float(np.median(diff))
            if not np.allclose(diff,1/inferred,rtol=1e-4,atol=1e-8):
                raise ValueError('Irregular/gapped time column: retain gaps as NaN rows or use an annotated recording')
            if rate is not None and not np.isclose(rate,inferred,rtol=1e-4):raise ValueError('Declared sample rate conflicts with the time column')
            rate=inferred;origin=float(times[0]);values=np.delete(values,index,axis=1);names.pop(index)
        if rate is None or unit is None:raise ValueError('CSV/TSV requires an explicit signal unit and sample rate (or regular seconds column)')
        return ArrayRecording(values.T,names,float(rate),unit,first_time=origin)
    if ext=='.json':
        obj=json.loads(path.read_text(encoding='utf-8-sig'))
        if not isinstance(obj,dict) or 'data' not in obj:raise ValueError('JSON must contain continuous data, channels, sfreq and unit')
    else:
        with np.load(path,allow_pickle=False) as archive:
            if 'data' not in archive:raise ValueError('NPZ must contain calibrated continuous data, channels, sfreq and unit; spectra/maps are not signal samples')
            obj={key:archive[key].tolist() for key in archive.files}
    embedded_rate=obj.get('sfreq',obj.get('sample_rate_hz'))
    embedded_unit=obj.get('unit',obj.get('data_unit'))
    if rate is not None and embedded_rate is not None and not np.isclose(rate,float(embedded_rate)):
        raise ValueError('Declared sample rate conflicts with file metadata')
    if unit is not None and embedded_unit is not None and _unit(unit)!=_unit(embedded_unit):
        raise ValueError('Declared signal unit conflicts with file metadata')
    rate=embedded_rate if embedded_rate is not None else rate
    unit=embedded_unit if embedded_unit is not None else unit
    if rate is None or unit is None:raise ValueError('Signal JSON/NPZ needs explicit sfreq and unit metadata or import settings')
    origin=float(obj.get('start_seconds',0));rate=float(rate)
    raw=ArrayRecording(obj['data'],obj.get('channels',obj.get('channel_names',[])),rate,unit,
                          first_time=origin,annotations=_annotations(obj.get('annotations',obj.get('events',[])),rate,origin),
                          channel_types=obj.get('channel_types'))
    raw._spectrum_recording_context=declared_recording_context(obj)
    return raw


def _dependencies(path,raw):
    paths={path}
    paths.update(Path(x).resolve() for x in (getattr(raw,'filenames',None) or []) if x)
    if suffix(path)=='.vhdr':
        content=path.read_bytes();encoding='utf-8-sig' if b'Codepage=UTF-8' in content else 'cp1252'
        for item in re.findall(r'^(?:DataFile|MarkerFile)\s*=\s*(.+)$',content.decode(encoding,errors='replace'),re.M|re.I):
            paths.add((path.parent/item.strip()).resolve())
    if suffix(path)=='.set' and path.with_suffix('.fdt').is_file():paths.add(path.with_suffix('.fdt'))
    return sorted(paths,key=str)


@contextmanager
def open_recording(path,*,sample_rate=None,unit=None,conversion_dir=None,converter_root=None,condition='UNKNOWN'):
    """Open calibrated signals; all referenced files are hashed before/after use."""
    path=Path(path).resolve(strict=True);ext=suffix(path)
    if ext not in FORMATS:raise ValueError('Unsupported recording type. Choose '+', '.join(FORMATS))
    primary_hash=sha256(path);conversion=None;read_path=path;raw=None;detected_context=None;native_descriptors=None
    if ext in ('.bxs','.eeg'):
        if conversion_dir is None:raise ValueError('Native recording intake needs a new conversion_dir to retain its calibrated conversion and audit')
        folder=Path(conversion_dir);folder.mkdir(parents=True,exist_ok=False)
        if ext=='.eeg':
            if path.with_suffix('.vhdr').exists():raise ValueError('This looks like BrainVision binary data; select the .vhdr header instead')
            try:from EEG_Paradox_Auto_Pipeline.eeg_auto_pipeline.native_intake import convert_native_recording
            except ImportError as exc:raise RuntimeError('WinEEG import needs the main setup Auto Pipeline and maintained converter') from exc
            code=re.sub('[^A-Za-z0-9_-]','_',path.stem)[:64] or 'recording'
            # The setup's narrow handler accepts only FFFF empty tables pointing
            # at settings beyond the possible table extent. Actual mapped-event
            # mismatches still fail the maintained converter's audit.
            intake_condition=condition if str(condition).upper() in {'EO','EC','ERP','SLEEP','UNKNOWN'} else 'UNKNOWN'
            converted=convert_native_recording(path,folder,client_code=code,condition=intake_condition,converter_root=converter_root,
                                               allow_empty_mapped_event_sentinel=True)
            if converted['status']!='validated':raise ValueError('Native conversion audit did not pass')
            from EEG_Paradox_Auto_Pipeline.eeg_auto_pipeline.native_intake import _converter,CONVERTER_ROOT
            core,_=_converter(Path(converter_root or CONVERTER_ROOT))
            native=core.read_legacy_eeg(path)
            if hashlib.sha256(native.raw).hexdigest()!=primary_hash:
                raise ValueError('Native recording changed while reading its layout')
            detected_context=native_recording_context(native)
            native_types=native_channel_types(native)
            native_descriptors=[{'name':channel.label,'kind':int(channel.kind),
                                 'descriptor_type':native_types[channel.label]} for channel in native.channels]
            del native
            read_path=Path(converted['edf_path'])
            conversion={'kind':'validated_WinEEG_intake','edf_sha256':converted['edf_sha256'],
                        'quantization_notice':converted['quantization_notice'],'audit_file':Path(converted['audit_path']).name,
                        'intake_file':Path(converted['manifest_path']).name,'reference_status':converted['reference_status'],
                        'mapped_event_table_notice':converted.get('mapped_event_table_notice'),
                        'declared_condition':condition,'converter_condition':intake_condition}
        else:
            try:from cracker.formats.bxs_converter import BXSConverter
            except ImportError as exc:raise RuntimeError('BXS import needs the main setup BXSConverter and its converter dependencies') from exc
            converter=BXSConverter();read_path,scale=converter.convert(path,folder)
            conversion={'kind':'main_BXSConverter','edf_sha256':sha256(read_path),
                        'reported_scale_uv_per_count':float(scale) if np.isfinite(scale) else None,
                        'method':'official executable' if converter.exe_path else 'main coded converter'}
    try:
        read_ext=suffix(read_path)
        if read_ext in ('.csv','.tsv','.json','.npz'):raw=_tabular(read_path,sample_rate,unit)
        else:
            import mne
            readers={'.edf':mne.io.read_raw_edf,'.bdf':mne.io.read_raw_bdf,'.fif':mne.io.read_raw_fif,
                     '.fif.gz':mne.io.read_raw_fif,'.gdf':mne.io.read_raw_gdf,'.vhdr':mne.io.read_raw_brainvision,
                     '.cnt':mne.io.read_raw_cnt,'.set':mne.io.read_raw_eeglab}
            extra={'infer_types':True} if read_ext in ('.edf','.bdf') else {}
            raw=readers[read_ext](str(read_path),preload=False,verbose='ERROR',**extra)
        if native_descriptors is not None:
            overrides={}
            for descriptor in native_descriptors:
                name=next((name for name in raw.ch_names if name==descriptor['name'] or re.sub(r'^EEG\s+','',descriptor['name'])==name),None)
                if name is None:raise ValueError('Converted native channel label does not match its descriptor: '+descriptor['name'])
                descriptor['reader_name']=name
                if descriptor['descriptor_type']!='eeg':overrides[name]=descriptor['descriptor_type']
            if overrides:raw.set_channel_types(overrides,on_unit_change='ignore',verbose='ERROR')
            effective=dict(zip(raw.ch_names,raw.get_channel_types()))
            for descriptor in native_descriptors:descriptor['effective_type']=effective[descriptor['reader_name']]
        dependencies=_dependencies(read_path,raw)
        hashes={str(item):sha256(item) for item in dependencies}
        provenance={'format':FORMATS[ext],'source_sha256':primary_hash,
                    'source_files':[{'name':Path(item).name,'sha256':value} for item,value in hashes.items()],
                    'conversion':conversion,'sample_rate_hz':float(raw.info['sfreq']),
                    'duration_seconds':raw.n_times/float(raw.info['sfreq'])}
        if native_descriptors is not None:provenance['native_channel_descriptors']=native_descriptors
        sampling_rates=None
        if read_ext in ('.edf','.bdf'):
            import pyedflib
            with pyedflib.EdfReader(str(read_path)) as reader:
                provenance['physical_units']=dict(zip(reader.getSignalLabels(),
                    [reader.getPhysicalDimension(i).strip() for i in range(reader.signals_in_file)]))
                original_rates=dict(zip(reader.getSignalLabels(),reader.getSampleFrequencies()))
                sampling_rates={name:next((rate for label,rate in original_rates.items()
                    if name==label or re.sub(r'^EEG\s+','',label)==name),None) for name in raw.ch_names}
            if any(value.lower()=='u' for value in provenance['physical_units'].values()):
                raw=_PrefixUnitRecording(raw,read_path,provenance['physical_units'])
                provenance['unit_normalization']="Prefix-only 'u' explicitly denotes microvolts in the main converter contract; read calibrated physical samples via pyEDFlib"
        if detected_context is None:
            detected_context=raw_recording_context(raw,file_format=FORMATS[read_ext]
                if read_ext not in ('.csv','.tsv','.json','.npz') else None,sampling_rates=sampling_rates)
        raw._spectrum_recording_context=detected_context
        provenance['recording_context']=detected_context
        yield raw,provenance
        if sha256(path)!=primary_hash or any(sha256(Path(item))!=digest for item,digest in hashes.items()):
            raise ValueError('A recording or companion file changed during analysis')
    finally:
        if raw is not None:raw.close()


def analyze_recording(path,*,sample_rate=None,unit=None,conversion_dir=None,converter_root=None,**options):
    from .adapters import analyze_raw,_eeg_picks
    with open_recording(path,sample_rate=sample_rate,unit=unit,conversion_dir=conversion_dir,
                        converter_root=converter_root,condition=options.get('condition','UNKNOWN')) as (raw,provenance):
        picks=_eeg_picks(raw,options.get('picks'))
        if 'physical_units' in provenance:
            units=provenance['physical_units']
            for name in picks:
                original=next((key for key in units if key==name or re.sub(r'^EEG\s+','',key)==name),name)
                try:_unit(units.get(original,''))
                except ValueError as exc:raise ValueError(f'Channel {name}: unknown/unverified physical dimension; use calibrated intake') from exc
        result=analyze_raw(raw,source=Path(path).name,**{**options,'picks':picks})
    result.metadata['source_sha256']=provenance['source_sha256']
    result.metadata['input_provenance']=provenance
    from .review import attach_review
    attach_review(result)
    return result
