"""Damaged saved runs must fail cleanly before changing retained artifacts."""
import json
from pathlib import Path
from types import SimpleNamespace

import numpy as np
import pytest

from EEG_Paradox_Spectrum_Studio import analyze_array, export_studio, run_recording
from EEG_Paradox_Spectrum_Studio.intake import sha256
from EEG_Paradox_Spectrum_Studio.runner import read_completed_run, refresh_run


def saved_run(folder):
    (folder/'review').mkdir()
    (folder/'OPEN_REPORT.html').write_text('report')
    (folder/'review/OPEN_SPECTRUM_STUDIO.html').write_text('viewer')
    manifest={'schema':'eeg-paradox.spectrum-run.v1','status':'complete',
              'entry':'OPEN_REPORT.html','viewer':'review/OPEN_SPECTRUM_STUDIO.html',
              'files_sha256':{name:sha256(folder/name) for name in
                              ['OPEN_REPORT.html','review/OPEN_SPECTRUM_STUDIO.html']}}
    (folder/'run_manifest.json').write_text(json.dumps(manifest))
    (folder/'run_status.json').write_text(json.dumps({'status':'complete'}))
    return manifest


@pytest.mark.parametrize('invalid',[None,[],17,'complete'])
@pytest.mark.parametrize('action',[read_completed_run,refresh_run])
def test_nonobject_run_manifest_is_a_clear_validation_error(tmp_path,invalid,action):
    saved_run(tmp_path)
    (tmp_path/'run_manifest.json').write_text(json.dumps(invalid))
    with pytest.raises(ValueError,match='manifest'):action(tmp_path)


@pytest.mark.parametrize('invalid',[None,[],{'status':'running'}])
@pytest.mark.parametrize('action',[read_completed_run,refresh_run])
def test_restore_and_refresh_require_a_completed_status(tmp_path,invalid,action):
    saved_run(tmp_path)
    (tmp_path/'run_status.json').write_text(json.dumps(invalid))
    with pytest.raises(ValueError,match='completed'):action(tmp_path)


@pytest.mark.parametrize('files',[{},[],{'OPEN_REPORT.html':False},
    {'../outside.txt':'0'*64},{'OPEN_REPORT.html':'not a digest'}])
def test_invalid_inventory_is_refused_before_refresh_writes(tmp_path,files):
    manifest=saved_run(tmp_path)
    manifest['files_sha256']={**manifest['files_sha256'],**files} if isinstance(files,dict) and files else files
    (tmp_path/'run_manifest.json').write_text(json.dumps(manifest))
    before={p:p.read_bytes() for p in tmp_path.rglob('*') if p.is_file()}
    for action in [read_completed_run,refresh_run]:
        with pytest.raises(ValueError):action(tmp_path)
    assert {p:p.read_bytes() for p in before}==before


@pytest.mark.parametrize('field',['entry','viewer'])
def test_completed_run_cannot_redirect_open_buttons_to_another_file(tmp_path,field):
    manifest=saved_run(tmp_path)
    other=tmp_path/'unexpected.txt';other.write_text('This file is not the report or viewer')
    manifest['files_sha256'][other.name]=sha256(other);manifest[field]=other.name
    (tmp_path/'run_manifest.json').write_text(json.dumps(manifest))
    for action in [read_completed_run,refresh_run]:
        with pytest.raises(ValueError,match='entrypoints'):action(tmp_path)
    assert other.read_text()=='This file is not the report or viewer'


@pytest.mark.parametrize('invalid',[None,[],17,{'schema':'eeg-paradox.spectrum-artifact.v1',
    'entry':'OPEN_SPECTRUM_STUDIO.html','status':'complete','files_sha256':{}}])
def test_invalid_viewer_manifest_cannot_change_export(tmp_path,invalid):
    from EEG_Paradox_Spectrum_Studio import refresh_studio
    result=analyze_array(np.zeros((1,1024)),256,['Cz'],unit='uV')
    page=export_studio(result,tmp_path/'review');before=page.read_bytes()
    (page.parent/'spectrum_manifest.json').write_text(json.dumps(invalid))
    with pytest.raises(ValueError,match='manifest|inventory'):refresh_studio(page.parent)
    assert page.read_bytes()==before


@pytest.mark.parametrize('name',['method.json','spectrum_arrays.npz','review_context.json'])
def test_refresh_cannot_read_scientific_context_omitted_from_inventory(tmp_path,name):
    from EEG_Paradox_Spectrum_Studio import refresh_studio
    result=analyze_array(np.zeros((1,1024)),256,['Cz'],unit='uV')
    page=export_studio(result,tmp_path/'review');before=page.read_bytes()
    manifest_path=page.parent/'spectrum_manifest.json';manifest=json.loads(manifest_path.read_text())
    del manifest['files_sha256'][name];manifest_path.write_text(json.dumps(manifest))
    with pytest.raises(ValueError,match='required'):refresh_studio(page.parent)
    assert page.read_bytes()==before


def test_run_hashing_never_loads_entire_artifact_into_memory(monkeypatch,tmp_path):
    source=tmp_path/'signal.json'
    source.write_text(json.dumps({'channels':['Cz'],'data':[[0.]*1024],'sfreq':256,'unit':'uV'}))
    source_hash=sha256(source)
    def forbidden(*args,**kwargs):
        raise AssertionError('Hashing must stream files rather than call Path.read_bytes')
    monkeypatch.setattr(Path,'read_bytes',forbidden)
    run=run_recording(source,tmp_path/'run',make_reports=False)
    assert read_completed_run(run.root)==run
    protected={p:sha256(p) for p in [source,run.root/'review/spectrum_arrays.npz',run.root/'review/band_power.csv']}
    refresh_run(run.root)
    assert read_completed_run(run.root)==run
    assert {p:sha256(p) for p in protected}==protected and sha256(source)==source_hash


@pytest.mark.parametrize('thread_alive,pending_completion',[(True,False),(False,True)])
def test_previous_run_cannot_join_an_active_batch(monkeypatch,tmp_path,thread_alive,pending_completion):
    import EEG_Paradox_Spectrum_Studio.desktop as desktop
    app=desktop.StudioDesktop.__new__(desktop.StudioDesktop)
    app.worker=SimpleNamespace(is_alive=lambda:thread_alive);app.run_active=pending_completion
    existing=object();app.results=[existing];app.selected_index=0
    def forbidden(*args):raise AssertionError('Do not read or alter a previous run during a batch')
    monkeypatch.setattr(desktop,'read_completed_run',forbidden)
    with pytest.raises(ValueError,match='finish'):app.select_completed(tmp_path)
    assert app.results==[existing] and app.selected_index==0
    statuses=[];app.status=SimpleNamespace(set=statuses.append)
    app.browse_recent()
    assert 'finish' in statuses[-1]
