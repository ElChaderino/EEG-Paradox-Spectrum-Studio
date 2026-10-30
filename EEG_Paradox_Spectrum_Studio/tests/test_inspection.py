import hashlib
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from EEG_Paradox_Spectrum_Studio import RunOptions,analyze_file
from EEG_Paradox_Spectrum_Studio.inspection import inspect_recording,estimate_selection


def test_preview_and_estimate_match_calibrated_edf_analysis(monkeypatch,tmp_path):
    monkeypatch.setenv('_MNE_FAKE_HOME_DIR',str(tmp_path/'mne'))
    from pyedflib import highlevel
    source=tmp_path/'preview.edf';t=np.arange(3072)/256;values=20*np.sin(2*np.pi*10*t)
    highlevel.write_edf(str(source),np.vstack([values,values,np.zeros_like(values)]),
        highlevel.make_signal_headers(['Cz','F3','Foto'],dimension='uV',sample_frequency=256,physical_min=-100,physical_max=100))
    before=hashlib.sha256(source.read_bytes()).hexdigest();info=inspect_recording(source)
    assert info['duration_seconds']==12 and info['sample_rate_hz']==256
    assert [c['name'] for c in info['channels'] if c['eligible']]==['Cz','F3']
    assert info['channels'][-1]['excluded_reason']=='auxiliary label'
    estimate=estimate_selection(info,RunOptions(duration=None));actual=analyze_file(source,duration=None)
    assert estimate['window_count']==len(actual.times_seconds)
    assert estimate['spectral_cells']==actual.psd_uv2_hz.size and estimate['fits_limit']
    assert estimate['start_seconds']==0 and estimate['stop_seconds']==12
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
    assert sorted(p.name for p in tmp_path.iterdir() if p.is_file())==['preview.edf']


def test_native_header_preview_and_task_condition_bridge(monkeypatch,tmp_path):
    monkeypatch.setenv('_MNE_FAKE_HOME_DIR',str(tmp_path/'mne'))
    from EEG_Paradox_Auto_Pipeline.eeg_auto_pipeline.native_intake import CONVERTER_ROOT
    monkeypatch.syspath_prepend(str(CONVERTER_ROOT));from wineeg_converter import core
    path=tmp_path/'task.eeg';t=np.arange(3072)/256
    core.write_legacy_eeg(path,sample_rate_hz=256,channels=[('Cz',.01,0.)],
        digital_samples=[np.rint(2000*np.sin(2*np.pi*10*t)).astype(int).tolist()])
    info=inspect_recording(path)
    assert info['duration_seconds']==12 and info['channels'][0]['eligible']
    assert 'Full conversion' in info['notice'] and not (tmp_path/'intake').exists()
    result=analyze_file(path,condition='TASK',conversion_dir=tmp_path/'intake')
    conversion=result.metadata['input_provenance']['conversion']
    assert result.metadata['condition']=='TASK'
    assert conversion['converter_condition']=='UNKNOWN' and conversion['declared_condition']=='TASK'
    assert result.band_power_uv2['alpha'][0,0]==pytest.approx(200,rel=.001)


def test_selection_cost_refuses_invalid_channels_and_exposes_limit():
    from EEG_Paradox_Spectrum_Studio import SpectrumConfig
    info={'sample_rate_hz':250.,'samples':250*36000,'origin_seconds':100.,
          'channels':[{'name':'Cz','eligible':True},{'name':'Foto','eligible':False}]}
    estimate=estimate_selection(info,RunOptions(duration=None))
    assert estimate['start_seconds']==100 and estimate['stop_seconds']==36100
    assert not estimate['fits_limit']
    for channels in [[],['Foto'],['Cz','Cz']]:
        with pytest.raises(ValueError,match='eligible'):estimate_selection(info,RunOptions(),channels)
    with pytest.raises(ValueError,match='Nyquist'):estimate_selection(info,RunOptions(config=SpectrumConfig(fmax_hz=200)))
    with pytest.raises(ValueError,match='complete'):estimate_selection(info,RunOptions(start=3599,duration=1))


@pytest.mark.parametrize('rate,window,low,high',[
    (250.,4.,np.nextafter(.5,np.inf),45.),
    (250.,4.,.5,np.nextafter(45.,0)),
    (256.,3.7,256/947*2,256/947*101),
    (250.3,2.37,.5,45.),
])
def test_cost_estimate_matches_actual_fft_bin_edges(rate,window,low,high):
    from EEG_Paradox_Spectrum_Studio import SpectrumConfig,analyze_array
    samples=int(round(window*rate))*2
    info={'sample_rate_hz':rate,'samples':samples,'origin_seconds':0.,
          'channels':[{'name':'Cz','eligible':True}]}
    options=RunOptions(duration=None,config=SpectrumConfig(window,.5,low,high))
    estimate=estimate_selection(info,options)
    actual=analyze_array(np.zeros((1,samples)),rate,['Cz'],unit='uV',config=options.config)
    assert estimate['frequency_bins']==len(actual.frequencies_hz)
    assert estimate['spectral_cells']==actual.psd_uv2_hz.size


@pytest.mark.parametrize('start,samples',[(.002,1125),(.010,1127)])
def test_whole_recording_from_between_samples_retains_the_final_fft_window(start,samples):
    from EEG_Paradox_Spectrum_Studio import analyze_raw,analyze_array
    from EEG_Paradox_Spectrum_Studio.intake import ArrayRecording
    rate=250.;origin=80.;t=np.arange(samples)/rate
    values=(20*np.sin(2*np.pi*10*t))[None]
    raw=ArrayRecording(values,['Cz'],rate,'uV',first_time=origin)
    options=RunOptions(start=start,duration=None)
    info={'sample_rate_hz':rate,'samples':samples,'origin_seconds':origin,
          'channels':[{'name':'Cz','eligible':True}]}
    estimate=estimate_selection(info,options)
    actual=analyze_raw(raw,start=start,duration=None)
    begin=round(start*rate)
    expected=analyze_array(values[:,begin:],rate,['Cz'],unit='uV',start_seconds=origin+begin/rate)
    assert len(actual.times_seconds)==estimate['window_count']==2
    assert actual.metadata['selected_stop_seconds']==pytest.approx(origin+samples/rate)
    np.testing.assert_equal(actual.psd_uv2_hz,expected.psd_uv2_hz)
    np.testing.assert_equal(actual.times_seconds,expected.times_seconds)
    np.testing.assert_equal(actual.trace_uv,values[:,begin:])


def test_preview_refuses_native_companions_and_unconverted_bxs(tmp_path):
    path=tmp_path/'data.eeg';path.write_bytes(b'not interpreted');path.with_suffix('.vhdr').write_text('header')
    with pytest.raises(ValueError,match='vhdr'):inspect_recording(path)
    bxs=tmp_path/'data.bxs';bxs.write_bytes(b'not interpreted')
    with pytest.raises(ValueError,match='calibrated conversion'):inspect_recording(bxs)


def test_recent_run_index_is_bounded_deduplicated_and_legacy_compatible(tmp_path):
    from EEG_Paradox_Spectrum_Studio.desktop import StudioDesktop,recent_run_paths
    app=StudioDesktop.__new__(StudioDesktop);app.recent_file=tmp_path/'recent.json'
    app.log=SimpleNamespace(insert=lambda *args:None)
    app.recent_file.write_text(json.dumps({'run':str(tmp_path/'legacy')}))
    assert recent_run_paths(app.recent_file)==[str(tmp_path/'legacy')]
    for i in range(25):app.remember_completed(SimpleNamespace(root=tmp_path/str(i)))
    app.remember_completed(SimpleNamespace(root=tmp_path/'24'))
    paths=recent_run_paths(app.recent_file)
    assert len(paths)==20 and paths[0]==str(tmp_path/'24') and len(set(paths))==20
    for invalid in [[],None,{'runs':'not a list'},{'runs':[False]}]:
        app.recent_file.write_text(json.dumps(invalid))
        with pytest.raises(ValueError,match='Invalid recent-run index'):recent_run_paths(app.recent_file)
    app.remember_completed(SimpleNamespace(root=tmp_path/'recovered'))
    assert recent_run_paths(app.recent_file)==[str(tmp_path/'recovered')]


def test_open_buttons_use_selected_batch_result(monkeypatch,tmp_path):
    from EEG_Paradox_Spectrum_Studio.desktop import StudioDesktop
    import EEG_Paradox_Spectrum_Studio.desktop as desktop
    a=tmp_path/'first.html';b=tmp_path/'second.html';a.write_text('first');b.write_text('second')
    app=StudioDesktop.__new__(StudioDesktop);app.results=[SimpleNamespace(viewer=a),SimpleNamespace(viewer=b)];app.selected_index=0
    calls=[];monkeypatch.setattr(desktop.os,'startfile',calls.append)
    app.open_result('viewer');app.selected_index=1;app.open_result('viewer')
    assert calls==[str(a),str(b)]
