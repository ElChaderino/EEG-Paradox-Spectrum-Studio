import json
from types import SimpleNamespace

from EEG_Paradox_Spectrum_Studio.diagnostics import build_diagnostics,diagnostic_event,progress_stage
from EEG_Paradox_Spectrum_Studio.desktop import StudioDesktop


def test_diagnostics_preserve_open_failure_codes_without_exception_or_recording_data(tmp_path):
    error=PermissionError('PRIVATE_PATIENT C:/PRIVATE_RECORDING.edf');error.winerror=5
    event=diagnostic_event('output_open_failed',120,error=error)
    event.update(source='PRIVATE_PATIENT',path='C:/PRIVATE_RECORDING.edf',data=[1,2,3],message=str(error))
    report=build_diagnostics([event],completed_runs=2,run_active=False)
    assert report['recent_events']==[{'code':'output_open_failed','elapsed_ms':120,'category':'permission_denied','windows_error':5}]
    text=json.dumps(report,allow_nan=False)
    assert 'PRIVATE_PATIENT' not in text and 'PRIVATE_RECORDING' not in text
    assert report['status']['completed_runs']==2
    assert report['runtime']['dependencies']['numpy']!='unavailable'
    from EEG_Paradox_Spectrum_Studio.desktop import options_from_fields
    try:options_from_fields({'start':'PRIVATE_PATIENT'})
    except ValueError as caught:
        traced=diagnostic_event('run_failed',130,error=caught)
        assert any(frame['module']=='desktop.py' for frame in traced['app_frames'])
        assert 'PRIVATE_PATIENT' not in json.dumps(traced)
    from EEG_Paradox_Spectrum_Studio.artifacts import read_object
    manifest=tmp_path/'PRIVATE_PATIENT.json';manifest.write_text(json.dumps('PRIVATE_PATIENT'))
    try:read_object(manifest,'PRIVATE_PATIENT manifest')
    except ValueError as caught:
        traced=diagnostic_event('run_failed',140,error=caught)
        assert any(frame['module']=='artifacts.py' for frame in traced['app_frames'])
        assert 'PRIVATE_PATIENT' not in json.dumps(traced)


def test_diagnostics_event_bound_and_progress_classification():
    events=[diagnostic_event('run_stage',i,stage='reports') for i in range(100)]
    report=build_diagnostics([*events,{'code':'PRIVATE_STRING','elapsed_ms':0}],completed_runs=float('nan'))
    assert len(report['recent_events'])==63
    assert report['status']['completed_runs'] is None
    assert progress_stage('Loading primary: PRIVATE_FILE.EEG')=='load'
    assert progress_stage('Rendering PNG figures and PDF report')=='reports'
    assert progress_stage('Recording 1/2: PRIVATE_FILE.EEG') is None


def test_native_save_diagnostics_writes_only_allowlisted_snapshot(tmp_path):
    app=StudioDesktop.__new__(StudioDesktop);destination=tmp_path/'diagnostics.json'
    app.root=object();app.dialog=SimpleNamespace(asksaveasfilename=lambda **kwargs:str(destination))
    app.results=[SimpleNamespace(root='PRIVATE_PATH',source='PRIVATE_RECORDING')]
    app.worker=None;app.inspect_worker=None;app.diagnostic_events=[];statuses=[]
    app.status=SimpleNamespace(set=statuses.append);app.record_diagnostic('run_started');app.save_diagnostics()
    report=json.loads(destination.read_text());assert report['surface']=='loader'
    assert report['recent_events'][0]['code']=='run_started'
    assert 'PRIVATE' not in destination.read_text()
    assert 'Nothing was sent' in statuses[-1]


def test_diagnostic_history_is_bounded_sanitized_and_survives_reopening(tmp_path):
    from EEG_Paradox_Spectrum_Studio.diagnostics import read_history,write_history
    path=tmp_path/'history.json'
    events=[{**diagnostic_event('run_stage',i,stage='reports'),'session':1,'message':'PRIVATE_HISTORY'} for i in range(100)]
    write_history(path,events)
    assert len(read_history(path))==64 and read_history(path)[-1]['elapsed_ms']==99
    assert 'PRIVATE_HISTORY' not in path.read_text()
    app=StudioDesktop.__new__(StudioDesktop);app.diagnostic_file=path;app.diagnostic_events=read_history(path);app.diagnostic_session=2
    app.record_diagnostic('loader_ready')
    restored=read_history(path)
    assert len(restored)==64 and restored[-1]['code']=='loader_ready' and restored[-1]['session']==2
    untrusted={**restored[-1],'app_frames':[{'module':'C:/PRIVATE_FILE.py','line':10},{'module':'desktop.py','line':12,'source':'PRIVATE_RECORDING'}]}
    write_history(path,[untrusted])
    assert read_history(path)[0]['app_frames']==[{'module':'desktop.py','line':12}]
    assert 'PRIVATE' not in path.read_text()
