"""Local support snapshots assembled from a small allowlist, never from EEG/log text."""
from __future__ import annotations

from importlib.metadata import PackageNotFoundError,version
import math
import platform
import re
import sys
import json
from pathlib import Path
from uuid import uuid4

EVENT_CODES=frozenset({'loader_ready','inspection_started','inspection_complete','inspection_failed','run_started',
    'run_stage','run_complete','run_failed','run_cancelled','output_opened','output_open_failed','run_restored','loader_closed','callback_error'})
STAGES=frozenset({'load','export','reports','verify','ready'})
CATEGORIES=frozenset({'permission_denied','missing_file','invalid_input','cancelled','os_error','processing_error'})
APP_MODULES=frozenset({'adapters.py','artifacts.py','desktop.py','diagnostics.py','export.py','inspection.py','intake.py','integrations.py','reports.py','review.py','runner.py','spectral.py','__main__.py','__init__.py'})


def error_category(error):
    if isinstance(error,PermissionError):return 'permission_denied'
    if isinstance(error,FileNotFoundError):return 'missing_file'
    if isinstance(error,InterruptedError):return 'cancelled'
    if isinstance(error,(ValueError,TypeError,KeyError)):return 'invalid_input'
    if isinstance(error,OSError):return 'os_error'
    return 'processing_error'


def progress_stage(message):
    """Classify known worker messages without retaining their filenames/text."""
    for prefix,stage in [('Loading ','load'),('Writing interactive','export'),('Rendering PNG','reports'),('Verifying run','verify')]:
        if message.startswith(prefix):return stage
    return None


def diagnostic_event(code,elapsed_ms,*,stage=None,error=None):
    if code not in EVENT_CODES:raise ValueError('Unsupported diagnostic event')
    result={'code':code,'elapsed_ms':_number(elapsed_ms)}
    if stage in STAGES:result['stage']=stage
    if error is not None:
        result['category']=error_category(error)
        for key,attr in [('os_error','errno'),('windows_error','winerror')]:
            value=getattr(error,attr,None)
            if type(value) is int and 0<=value<=65535:result[key]=value
        frames=[];trace=error.__traceback__
        while trace is not None:
            frame=trace.tb_frame;module=frame.f_globals.get('__name__','')
            if isinstance(module,str) and module.startswith('EEG_Paradox_Spectrum_Studio.'):
                name=Path(frame.f_code.co_filename).name
                if name in APP_MODULES:frames.append({'module':name,'line':trace.tb_lineno})
            trace=trace.tb_next
        if frames:result['app_frames']=frames[-8:]
    return result


def _number(value,maximum=1e12):
    return value if type(value) in (int,float) and math.isfinite(value) and 0<=value<=maximum else None


def sanitized_events(events):
    recent=[]
    for event in list(events)[-64:]:
        if not isinstance(event,dict) or not isinstance(event.get('code'),str) or event['code'] not in EVENT_CODES:continue
        safe={'code':event['code'],'elapsed_ms':_number(event.get('elapsed_ms'))}
        if type(event.get('session')) is int and 0<=event['session']<=1e9:safe['session']=event['session']
        if isinstance(event.get('stage'),str) and event['stage'] in STAGES:safe['stage']=event['stage']
        if isinstance(event.get('category'),str) and event['category'] in CATEGORIES:safe['category']=event['category']
        for key in ['os_error','windows_error']:
            if type(event.get(key)) is int and 0<=event[key]<=65535:safe[key]=event[key]
        if isinstance(event.get('app_frames'),list):
            frames=[{'module':f['module'],'line':f['line']} for f in event['app_frames'][-8:]
                    if isinstance(f,dict) and isinstance(f.get('module'),str) and f['module'] in APP_MODULES
                    and type(f.get('line')) is int and 0<f['line']<=1e7]
            if frames:safe['app_frames']=frames
        recent.append(safe)
    return recent


def read_history(path):
    path=Path(path)
    if not path.is_file():return []
    if path.stat().st_size>64000:raise ValueError('Diagnostic history exceeds its size limit')
    payload=json.loads(path.read_text(encoding='utf-8'))
    if not isinstance(payload,dict) or payload.get('schema')!='eeg-paradox.spectrum-diagnostic-history.v1' or not isinstance(payload.get('events'),list):
        raise ValueError('Invalid diagnostic history')
    return sanitized_events(payload['events'])


def write_history(path,events):
    path=Path(path);path.parent.mkdir(parents=True,exist_ok=True)
    temporary=path.with_name('.diagnostics_'+uuid4().hex+'.tmp')
    try:
        temporary.write_text(json.dumps({'schema':'eeg-paradox.spectrum-diagnostic-history.v1','events':sanitized_events(events)},allow_nan=False),encoding='utf-8')
        temporary.replace(path)
    finally:
        try:temporary.unlink(missing_ok=True)
        except OSError:pass


def build_diagnostics(events=(),*,completed_runs=0,run_active=False,inspection_active=False):
    from . import __version__
    dependencies={}
    for name in ['numpy','scipy','mne','pyedflib','matplotlib','reportlab']:
        try:value=version(name)
        except PackageNotFoundError:value='unavailable'
        dependencies[name]=value if re.fullmatch(r'[A-Za-z0-9.+_-]{1,64}',value) else 'unknown'
    system=platform.system();release=platform.release()
    return {'schema':'eeg-paradox.spectrum-diagnostics.v1','app_version':__version__,'surface':'loader',
        'privacy':'Local export: app status and dependency versions only. No EEG, recording identifiers, source paths, events or channel labels.',
        'runtime':{'python':'.'.join(map(str,sys.version_info[:3])),
                   'os_family':system if system in ('Windows','Linux','Darwin') else 'other',
                   'os_release':release if re.fullmatch(r'[0-9.]{1,32}',release) else 'unknown','dependencies':dependencies},
        'status':{'completed_runs':_number(completed_runs,1e6),'run_active':run_active is True,'inspection_active':inspection_active is True},
        'recent_events':sanitized_events(events)}
