import hashlib
import json
from pathlib import Path

import numpy as np
import pytest

from EEG_Paradox_Spectrum_Studio import analyze_file,RunOptions,run_recording
from EEG_Paradox_Spectrum_Studio.desktop import options_from_fields


def signal():
    t=np.arange(12*256)/256
    return 20*np.sin(2*np.pi*10*t)


@pytest.fixture(autouse=True)
def runtime(monkeypatch,tmp_path):
    monkeypatch.setenv('_MNE_FAKE_HOME_DIR',str(tmp_path/'mne'))
    monkeypatch.setenv('MNE_LOGGING_LEVEL','ERROR')
    monkeypatch.setenv('MPLCONFIGDIR',str(tmp_path/'mpl'))


@pytest.mark.parametrize('ext',['.csv','.tsv','.json','.npz'])
def test_calibrated_tabular_inputs_preserve_sinusoid_power(ext,tmp_path):
    path=tmp_path/('signal'+ext);data=signal()
    if ext in ('.csv','.tsv'):
        sep='\t' if ext=='.tsv' else ','
        np.savetxt(path,np.column_stack([np.arange(len(data))/256,data]),delimiter=sep,header='time'+sep+'Cz',comments='')
        kwargs={'unit':'uV'}
    elif ext=='.json':
        path.write_text(json.dumps({'channels':['Cz'],'data':[data.tolist()],'sfreq':256,'unit':'uV'}));kwargs={}
    else:np.savez(path,channels=['Cz'],data=data[None],sfreq=256,unit='uV');kwargs={}
    result=analyze_file(path,**kwargs)
    assert result.band_power_uv2['alpha'][0,0]==pytest.approx(200,rel=1e-5)
    assert result.metadata['source_sha256']==hashlib.sha256(path.read_bytes()).hexdigest()
    assert result.metadata['input_provenance']['source_files'][0]['name']==path.name


def test_tabular_metadata_and_time_gaps_are_not_guessed(tmp_path):
    path=tmp_path/'data.csv';np.savetxt(path,signal()[:,None],delimiter=',',header='Cz',comments='')
    with pytest.raises(ValueError,match='explicit'):analyze_file(path)
    a=analyze_file(path,unit='uV',sample_rate=256);assert a.valid.all()
    times=np.arange(3072)/256;times[1500:]+=1
    np.savetxt(path,np.column_stack([times,signal()]),delimiter=',',header='time,Cz',comments='')
    with pytest.raises(ValueError,match='Irregular'):analyze_file(path,unit='uV')
    path=tmp_path/'data.json';path.write_text(json.dumps({'channels':['Cz'],'data':[signal().tolist()],'sfreq':256,'unit':'uV'}))
    with pytest.raises(ValueError,match='conflicts'):analyze_file(path,unit='V')
    with pytest.raises(ValueError,match='conflicts'):analyze_file(path,sample_rate=128)


def test_original_time_bad_annotations_and_whole_selection(tmp_path):
    path=tmp_path/'signal.json'
    path.write_text(json.dumps({'channels':['Cz'],'data':[signal().tolist()],'sfreq':256,'unit':'uV','start_seconds':100,
      'annotations':[{'onset_seconds':105,'duration_seconds':1,'description':'BAD_reviewed'}]}))
    r=analyze_file(path,duration=None)
    assert r.times_seconds[0]==102 and r.times_seconds[-1]==110
    assert not r.valid[0,(r.times_seconds-2<106)&(r.times_seconds+2>105)].any()


def test_fif_reader_round_trip_and_short_scope(tmp_path):
    import mne
    path=tmp_path/'recording_raw.fif'
    raw=mne.io.RawArray(signal()[None]*1e-6,mne.create_info(['Cz'],256,['eeg']),verbose='ERROR')
    raw.save(path,overwrite=False,verbose='ERROR');raw.close()
    r=analyze_file(path)
    assert r.band_power_uv2['alpha'][0,0]==pytest.approx(200,rel=1e-5)


def test_eeglab_reader_with_embedded_calibrated_samples(tmp_path):
    from scipy.io import savemat
    path=tmp_path/'synthetic.set'
    savemat(path,{'EEG':{'data':signal()[None],'srate':256.,'nbchan':1.,'pnts':3072.,'trials':1.,'xmin':0.,'chanlocs':{'labels':'Cz'}}},appendmat=False)
    r=analyze_file(path)
    assert r.band_power_uv2['alpha'][0,0]==pytest.approx(200,rel=1e-5)


def test_native_converter_bridge_keeps_source_identity_and_outputs_in_run(monkeypatch,tmp_path):
    import cracker.formats.bxs_converter as converter_module
    from pyedflib import highlevel
    source=tmp_path/'source.bxs';source.write_bytes(b'engineering bridge fixture')
    def convert(self,bxs_path,output_dir):
        output=Path(output_dir)/'converted.edf'
        highlevel.write_edf(str(output),signal()[None],highlevel.make_signal_headers(['Cz'],dimension='uV',sample_frequency=256,physical_min=-100,physical_max=100))
        return output,1.
    monkeypatch.setattr(converter_module.BXSConverter,'convert',convert)
    r=analyze_file(source,conversion_dir=tmp_path/'run_intake')
    assert r.metadata['source_sha256']==hashlib.sha256(source.read_bytes()).hexdigest()
    assert r.metadata['input_provenance']['conversion']['kind']=='main_BXSConverter'
    assert r.band_power_uv2['alpha'][0,0]==pytest.approx(200,rel=.001)
    assert not (source.parent/'temp_conversions').exists()


def test_main_converter_prefix_unit_is_microvolts_without_millionfold_amplitude_error(tmp_path):
    from pyedflib import highlevel
    path=tmp_path/'prefix.edf'
    headers=highlevel.make_signal_headers(['Cz','F3'],dimension='u',sample_frequency=256,physical_min=-100,physical_max=100)
    headers[1]['dimension']='uV'
    highlevel.write_edf(str(path),np.vstack([signal(),signal()]),headers)
    result=analyze_file(path)
    np.testing.assert_allclose(result.band_power_uv2['alpha'][:,0],[200,200],rtol=.001)
    assert 'unit_normalization' in result.metadata['input_provenance']


@pytest.mark.parametrize('index_count,map_count,accepted',[(65535,0,True),(12,0,False),(65535,1,False)])
def test_wineeg_empty_table_bridge_retains_marks_and_rejects_other_mismatches(tmp_path,monkeypatch,index_count,map_count,accepted):
    import struct
    from EEG_Paradox_Auto_Pipeline.eeg_auto_pipeline.native_intake import CONVERTER_ROOT
    monkeypatch.syspath_prepend(str(CONVERTER_ROOT))
    from wineeg_converter import core
    source=tmp_path/'legacy.eeg'
    core.write_legacy_eeg(source,sample_rate_hz=256,channels=[('Cz',.01,0.)],
        digital_samples=[np.rint(signal()*100).astype(int).tolist()],
        marks=[core.LegacyMark(5000,'review marker')])
    original=bytearray(source.read_bytes())
    for name,value in [('index_count',index_count),('event_map_count',map_count)]:
        _,offset,fmt,_=next(field for field in core.HEADER_FIELDS if field[0]==name)
        struct.pack_into('<'+fmt,original,offset,value)
    source.write_bytes(original)
    if not accepted:
        with pytest.raises(ValueError,match='mapped-event tables differ'):
            analyze_file(source,conversion_dir=tmp_path/'intake')
    else:
        result=analyze_file(source,conversion_dir=tmp_path/'intake')
        assert result.band_power_uv2['alpha'][0,0]==pytest.approx(200,rel=.001)
        assert any(item['description']=='EVENT_0001' and item['onset_seconds']==5
                   for item in result.metadata['annotations'])
        conversion=result.metadata['input_provenance']['conversion']
        assert conversion['mapped_event_table_notice']['original_index_count']==65535
        audit=json.loads(next((tmp_path/'intake').glob('*.conversion_audit.json')).read_text())
        assert audit['status']=='passed'
    assert source.read_bytes()==original


def test_report_generation_retains_numerics_and_creates_readable_pdf(tmp_path):
    from EEG_Paradox_Spectrum_Studio import analyze_array,attach_review
    from EEG_Paradox_Spectrum_Studio.reports import generate_reports
    r=analyze_array(signal()[None],256,['Cz'],unit='uV',source='Report engineering fixture')
    attach_review(r);before=r.psd_uv2_hz.copy()
    summary=generate_reports(r,tmp_path/'reports')
    np.testing.assert_equal(r.psd_uv2_hz,before)
    assert summary['mean_band_power_uv2']['alpha'][0]==pytest.approx(200,rel=1e-5)
    from PIL import Image
    for figure in summary['figures']:
        with Image.open(tmp_path/'reports'/figure['file']) as image:assert image.width>1500 and image.height>1000
    import fitz
    with fitz.open(tmp_path/'reports/Spectrum_Studio_Report.pdf') as document:
        assert len(document)==1+len(summary['figures'])
        assert 'unknown' in document[0].get_text()


def test_native_intake_notices_remain_visible_in_pdf_and_gallery(tmp_path):
    from EEG_Paradox_Spectrum_Studio import analyze_array,attach_review
    from EEG_Paradox_Spectrum_Studio.reports import generate_reports,write_gallery
    import fitz
    r=analyze_array(signal()[None],256,['Cz'],unit='uV',source='Native intake engineering fixture')
    r.metadata['input_provenance']={'conversion':{
        'mapped_event_table_notice':{'status':'unavailable_stale_empty_table_sentinel'},
        'quantization_notice':'Range-adaptive EDF quantization measured. Native integers retained.'}}
    attach_review(r)
    summary=generate_reports(r,tmp_path/'reports')
    gallery=write_gallery(tmp_path,summary)
    assert 'mapped events unavailable' in gallery.read_text()
    with fitz.open(tmp_path/'reports/Spectrum_Studio_Report.pdf') as document:
        text=' '.join(page.get_text() for page in document)
        assert 'mapped events unavailable' in text and 'Native integers retained' in text
        for page in document:
            for block in page.get_text('blocks'):
                assert block[1]>=0 and block[3]<=page.rect.height


def test_brainvision_companions_units_markers_and_integrity(tmp_path):
    header=tmp_path/'signals.vhdr';binary=tmp_path/'signals.eeg';markers=tmp_path/'signals.vmrk'
    signal().astype('<f4').tofile(binary)
    header.write_text('Brain Vision Data Exchange Header File Version 1.0\n[Common Infos]\nCodepage=UTF-8\nDataFile=signals.eeg\nMarkerFile=signals.vmrk\nDataFormat=BINARY\nDataOrientation=MULTIPLEXED\nNumberOfChannels=1\nSamplingInterval=3906.25\n[Binary Infos]\nBinaryFormat=IEEE_FLOAT_32\n[Channel Infos]\nCh1=Cz,,1,µV\n',encoding='utf-8')
    markers.write_text('Brain Vision Data Exchange Marker File, Version 1.0\n[Common Infos]\nDataFile=signals.eeg\n[Marker Infos]\nMk1=Stimulus,S 1,1281,1,0\nMk2=Bad Interval,BAD_artifact,1537,256,0\n',encoding='utf-8')
    r=analyze_file(header)
    assert r.band_power_uv2['alpha'][0,0]==pytest.approx(200,rel=1e-5)
    assert len(r.metadata['input_provenance']['source_files'])==3
    assert len(r.metadata['annotations'])==2
    assert not r.valid.all()
    from EEG_Paradox_Spectrum_Studio.intake import open_recording
    with pytest.raises(ValueError,match='changed'):
        with open_recording(header) as (raw,info):binary.write_bytes(binary.read_bytes()+b'\0\0\0\0')


def test_native_inputs_require_a_retained_conversion_directory(tmp_path):
    path=tmp_path/'native.eeg';path.write_bytes(b'invalid fixture')
    with pytest.raises(ValueError,match='conversion_dir'):analyze_file(path)
    header=path.with_suffix('.vhdr');header.write_text('header fixture')
    with pytest.raises(ValueError,match='select the .vhdr'):analyze_file(path,conversion_dir=tmp_path/'converted')


def test_desktop_options_use_explicit_context_and_validate_without_loading():
    fields={'start':'0','duration':'120','window':'4','hop':'.5','fmin':'.5','fmax':'45','whole':False,
            'reference':'unknown','condition':'EC','simultaneous':'unknown','sample_rate':'','unit':'','channels':'Cz, F3'}
    opts=options_from_fields(fields);assert opts.channels==('Cz','F3') and opts.simultaneous is None
    assert options_from_fields({**fields,'whole':True}).duration is None
    for change in [{'hop':'0'},{'start':'nan'},{'duration':'2'},{'fmax':'0'},{'sample_rate':'-1'}]:
        with pytest.raises(ValueError):options_from_fields({**fields,**change})


def test_run_folder_has_complete_hash_inventory_and_no_overwrite(tmp_path):
    source=tmp_path/'signal.json';source.write_text(json.dumps({'channels':['Cz'],'data':[signal().tolist()],'sfreq':256,'unit':'uV'}))
    output=tmp_path/'run'
    run=run_recording(source,output,make_reports=False)
    assert run.report.is_file() and run.viewer.is_file()
    manifest=json.loads((output/'run_manifest.json').read_text());assert manifest['status']=='complete'
    for name,digest in manifest['files_sha256'].items():assert hashlib.sha256((output/name).read_bytes()).hexdigest()==digest
    from EEG_Paradox_Spectrum_Studio.runner import read_completed_run
    restored=read_completed_run(output)
    assert restored==run
    from EEG_Paradox_Spectrum_Studio.runner import refresh_run
    refresh_run(output)
    with pytest.raises(FileExistsError):run_recording(source,output,make_reports=False)
    run.report.write_text('user edit')
    with pytest.raises(ValueError,match='changed'):refresh_run(output)
    with pytest.raises(ValueError,match='changed'):read_completed_run(output)


@pytest.mark.parametrize('kind',['root','viewer','report'])
def test_desktop_open_denial_is_visible_and_keeps_completed_output(monkeypatch,tmp_path,kind):
    from types import SimpleNamespace
    from EEG_Paradox_Spectrum_Studio.desktop import StudioDesktop
    import EEG_Paradox_Spectrum_Studio.desktop as desktop
    viewer=tmp_path/'viewer.html';viewer.write_text('fixture')
    report=tmp_path/'report.html';report.write_text('fixture')
    app=StudioDesktop.__new__(StudioDesktop)
    artifact=SimpleNamespace(root=tmp_path,viewer=viewer,report=report)
    app.results=[artifact];app.root=object();statuses=[];logs=[];dialogs=[]
    app.status=SimpleNamespace(set=statuses.append)
    app.log=SimpleNamespace(insert=lambda _,text:logs.append(text),see=lambda _:None)
    app.messagebox=SimpleNamespace(showerror=lambda title,message,**kwargs:dialogs.append(message))
    def denied(path):
        error=PermissionError('Access is denied');error.winerror=5;raise error
    monkeypatch.setattr(desktop.os,'startfile',denied)
    app.open_result(kind)
    assert app.results==[artifact] and viewer.exists() and report.exists()
    assert 'Windows could not open' in statuses[-1]
    assert str(getattr(artifact,kind)) in dialogs[-1]
    assert 'Run_Spectrum_Studio.cmd' in dialogs[-1]
    assert 'Access is denied' in ''.join(logs)


def test_completed_run_restore_does_not_accept_incomplete_folder(tmp_path):
    from EEG_Paradox_Spectrum_Studio.runner import read_completed_run
    (tmp_path/'run_manifest.json').write_text(json.dumps({'schema':'eeg-paradox.spectrum-run.v1','status':'complete','files_sha256':{}}))
    (tmp_path/'run_status.json').write_text(json.dumps({'status':'failed'}))
    with pytest.raises(ValueError,match='completed'):read_completed_run(tmp_path)


def test_recent_completed_run_restores_buttons_without_reprocessing(tmp_path):
    from types import SimpleNamespace
    from EEG_Paradox_Spectrum_Studio.desktop import StudioDesktop
    source=tmp_path/'signal.json';source.write_text(json.dumps({'channels':['Cz'],'data':[signal().tolist()],'sfreq':256,'unit':'uV'}))
    artifact=run_recording(source,tmp_path/'run',make_reports=False)
    app=StudioDesktop.__new__(StudioDesktop);app.results=[];logs=[];statuses=[];states=[]
    app.recent_file=tmp_path/'state/last_completed_run.json'
    app.log=SimpleNamespace(insert=lambda _,text:logs.append(text),see=lambda _:None)
    app.status=SimpleNamespace(set=statuses.append)
    app.open_buttons=[SimpleNamespace(configure=lambda **kwargs:states.append(kwargs['state'])) for _ in range(3)]
    app.remember_completed(artifact)
    app.restore_recent()
    assert app.results==[artifact] and states==['normal']*3
    assert 'Restored completed run' in statuses[-1]
    artifact.viewer.write_text('changed after generation')
    app.results=[];states.clear();app.restore_recent()
    assert app.results==[] and not states
    assert 'changed since generation' in ''.join(logs)


def test_failed_and_cancelled_runs_are_not_marked_complete(tmp_path):
    path=tmp_path/'bad.json';path.write_text('{}')
    with pytest.raises(ValueError):run_recording(path,tmp_path/'failed',make_reports=False)
    assert json.loads((tmp_path/'failed/run_status.json').read_text())['status']=='failed'
    assert not (tmp_path/'failed/run_manifest.json').exists()
    with pytest.raises(InterruptedError):run_recording(path,tmp_path/'cancelled',cancelled=lambda:True,make_reports=False)
    assert json.loads((tmp_path/'cancelled/run_status.json').read_text())['status']=='cancelled'
