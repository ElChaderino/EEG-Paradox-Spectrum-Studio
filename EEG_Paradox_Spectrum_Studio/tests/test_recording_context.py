"""File evidence must enable useful maps without manufacturing acquisition facts."""
import copy
import hashlib
import json
from types import SimpleNamespace

import numpy as np
import pytest

from EEG_Paradox_Spectrum_Studio import analyze_file, analyze_raw
from EEG_Paradox_Spectrum_Studio.inspection import inspect_recording
from EEG_Paradox_Spectrum_Studio.recording_context import (
    native_recording_context, raw_recording_context, scalp_time_aligned, timing_description)
from EEG_Paradox_Spectrum_Studio.review import fingerprint


@pytest.fixture(autouse=True)
def runtime(monkeypatch, tmp_path):
    monkeypatch.setenv('_MNE_FAKE_HOME_DIR', str(tmp_path/'mne'))
    monkeypatch.setenv('MPLCONFIGDIR', str(tmp_path/'mpl'))


def signals():
    t = np.arange(3072)/256
    return np.array([20*np.sin(2*np.pi*f*t) for f in (10, 11, 12)])


def native(tmp_path, monkeypatch):
    from EEG_Paradox_Auto_Pipeline.eeg_auto_pipeline.native_intake import CONVERTER_ROOT
    monkeypatch.syspath_prepend(str(CONVERTER_ROOT))
    from wineeg_converter import core
    path = tmp_path/'saved.eeg'
    core.write_legacy_eeg(path, sample_rate_hz=256,
                          channels=[(name, .01, 0.) for name in ('F3', 'F4', 'Cz')],
                          digital_samples=np.rint(signals()*100).astype(int).tolist())
    return path, core


def test_native_file_positions_and_clock_survive_the_converter(tmp_path, monkeypatch):
    import struct
    path, core = native(tmp_path, monkeypatch)
    eeg = core.read_legacy_eeg(path)
    data = bytearray(eeg.raw)
    # Deliberately differ from the bundled template: the saved values must win.
    struct.pack_into('<hh', data, eeg.header['channel_table_offset']+0x14, 48, 129)
    path.write_bytes(data)
    before = hashlib.sha256(data).hexdigest()
    preview = inspect_recording(path)
    result = analyze_file(path, conversion_dir=tmp_path/'intake')
    context = result.metadata['recording_context']
    assert context == preview['recording_context']
    assert context['native_coordinates']['F3'] == {'theta_degrees':48, 'phi_degrees':129}
    np.testing.assert_allclose(result.review['montage']['positions_xy']['F3'],
                               [48/90*np.cos(np.deg2rad(129)), 48/90*np.sin(np.deg2rad(129))])
    assert context['positions_xy']['F3'][0] < 0 < context['positions_xy']['F4'][0]
    assert result.metadata['simultaneous'] is None  # No hardware claim invented.
    assert result.metadata['reference'] == 'unknown'
    assert scalp_time_aligned(result.metadata)
    assert 'not measured' in result.review['montage']['source']
    assert result.review['fingerprint'] == fingerprint(result)
    assert result.band_power_uv2['alpha'][0,0] == pytest.approx(200, rel=.001)
    assert hashlib.sha256(path.read_bytes()).hexdigest() == before
    sequential = analyze_file(path, simultaneous=False, conversion_dir=tmp_path/'intake_no')
    assert not scalp_time_aligned(sequential.metadata)
    np.testing.assert_equal(sequential.psd_uv2_hz, result.psd_uv2_hz)


def test_native_invalid_positions_and_incomplete_frames_are_not_guessed():
    channels = [SimpleNamespace(label=n, kind=k, x=x, y=y) for n,k,x,y in
                [('Fp1',0,72,108),('Fp2',0,72,72),('Cz',0,0,90),
                 ('A1',2,0,0),('Foto',1,137,230),('bad',0,91,30),
                 ('unknown',0,0,0),('F3-F4',0,48,135),('bad_angle',0,20,361)]]
    eeg = SimpleNamespace(header={'sample_rate_hz':250,'logical_frame_count':100},
                          physical_frame_count=100,trailing_sample_bytes=0,channels=channels)
    context = native_recording_context(eeg)
    assert set(context['positions_xy']) == {'Fp1','Fp2','Cz'}
    assert context['positions_xy']['Fp1'][0] < 0 and context['positions_xy']['Fp1'][1] > 0
    assert context['positions_xy']['Cz'] == [0.,0.]
    assert scalp_time_aligned({'recording_context':context})
    eeg.trailing_sample_bytes = 1
    assert not scalp_time_aligned({'recording_context':native_recording_context(eeg)})
    eeg.trailing_sample_bytes = 0; eeg.physical_frame_count = 101
    assert not scalp_time_aligned({'recording_context':native_recording_context(eeg)})


def test_embedded_fif_geometry_works_but_in_memory_arrays_keep_unknown_timing(tmp_path):
    import mne
    names = ['F3','F4','Cz','A1','A2']
    values = np.vstack([signals(),signals()[:2]])
    raw = mne.io.RawArray(values*1e-6,mne.create_info(names,256,'eeg'),verbose='ERROR')
    raw.set_montage(mne.channels.make_dig_montage(ch_pos={
        'F3':[-.04,.04,.06],'F4':[.04,.04,.06],'Cz':[0.,0.,.09]},coord_frame='head'),on_missing='ignore')
    direct = analyze_raw(raw)
    assert len(direct.review['montage']['positions_xy']) == 3
    assert not scalp_time_aligned(direct.metadata)
    assert raw_recording_context(raw,file_format='GDF')['timing']['aligned'] is None
    path = tmp_path/'known_raw.fif';raw.save(path,verbose='ERROR');raw.close()
    result = analyze_file(path)
    assert scalp_time_aligned(result.metadata)
    assert result.metadata['simultaneous'] is None
    assert result.review['montage']['positions_xy']['F3'][0] < 0
    assert 'measurement status unconfirmed' in result.review['montage']['source']
    np.testing.assert_allclose(result.psd_uv2_hz,direct.psd_uv2_hz,rtol=1e-5,atol=1e-8)
    from EEG_Paradox_Spectrum_Studio.reports import generate_reports
    summary = generate_reports(result,tmp_path/'reports',pdf=False)
    assert summary['scalp_map_available']
    assert summary['unpositioned_channels'] == ['A1','A2']
    assert 'Shared file clock' in summary['timing_context']


def test_edf_labels_alone_do_not_create_positions_and_mixed_rates_stay_unknown(tmp_path):
    from pyedflib import highlevel
    names = ['F3','F4','Cz']
    path = tmp_path/'uniform.edf'
    headers = highlevel.make_signal_headers(names,dimension='uV',sample_frequency=256,physical_min=-100,physical_max=100)
    highlevel.write_edf(str(path),signals(),headers)
    result = analyze_file(path)
    assert scalp_time_aligned(result.metadata)
    assert not result.metadata['recording_context']['positions_xy']
    assert not result.review.get('montage')
    headers[0]['sample_frequency'] = 128
    mixed = tmp_path/'mixed.edf'
    highlevel.write_edf(str(mixed),[np.ascontiguousarray(v) for v in [signals()[0,::2],*signals()[1:]]],headers)
    unequal = analyze_file(mixed)
    assert not scalp_time_aligned(unequal.metadata)
    assert 'resampling' in unequal.metadata['recording_context']['timing']['source']


def signal_object():
    return {'channels':['F3','F4','Cz'],'sfreq':256,'unit':'uV','data':signals().tolist(),
            'simultaneous':True,'positions_xy':{'F3':[-.4,.4],'F4':[.4,.4],'Cz':[0,0]},
            'position_source':'recording setup worksheet'}


def test_json_and_non_pickle_npz_can_declare_existing_metadata(tmp_path):
    obj = signal_object();path = tmp_path/'declared.json';path.write_text(json.dumps(obj))
    result = analyze_file(path)
    assert scalp_time_aligned(result.metadata)
    assert result.metadata['simultaneous'] is None
    assert result.review['montage']['positions_xy'] == obj['positions_xy']
    npz = tmp_path/'declared.npz'
    np.savez(npz, **{**obj, 'positions_xy':list(obj['positions_xy'].values())})
    packed = analyze_file(npz)
    assert packed.review['montage']['positions_xy'] == obj['positions_xy']
    np.testing.assert_equal(packed.psd_uv2_hz,result.psd_uv2_hz)
    assert not scalp_time_aligned(analyze_file(path,simultaneous=False).metadata)
    obj['simultaneous'] = False;path.write_text(json.dumps(obj))
    assert not scalp_time_aligned(analyze_file(path).metadata)
    with pytest.raises(ValueError,match='conflicts'):
        analyze_file(path,simultaneous=True)


@pytest.mark.parametrize('change',[
    {'simultaneous':'yes'}, {'position_source':None},
    {'positions_xy':{'wrong':[0,0]}}, {'positions_xy':{'F3':[float('nan'),0]}},
    {'positions_xy':{'F3':[5,0]}}, {'positions_xy':{'F3':[True,0]}},
])
def test_ambiguous_or_invalid_embedded_metadata_is_refused(tmp_path,change):
    obj = {**signal_object(),**change};path = tmp_path/'bad.json';path.write_text(json.dumps(obj))
    with pytest.raises(ValueError):analyze_file(path)


def test_file_evidence_changes_workspace_binding_and_operator_no_always_blocks():
    from EEG_Paradox_Spectrum_Studio import analyze_array
    result = analyze_array(signals(),256,['F3','F4','Cz'],unit='uV')
    old = fingerprint(result)
    result.metadata['recording_context'] = {'timing':{'aligned':True}}
    assert fingerprint(result) != old
    assert scalp_time_aligned(result.metadata)
    assert 'hardware simultaneity unconfirmed' in timing_description(result.metadata)
    result.metadata['simultaneous'] = False
    assert not scalp_time_aligned(result.metadata)
