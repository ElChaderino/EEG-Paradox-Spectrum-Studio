from pathlib import Path
import sys

import pytest

from EEG_Paradox_Spectrum_Studio import runtime


def test_source_paths_preserve_workspace_locations(tmp_path,monkeypatch):
    monkeypatch.chdir(tmp_path);monkeypatch.delattr(sys,'frozen',raising=False)
    monkeypatch.setenv('SPECTRUM_STUDIO_DATA_DIR',str(tmp_path/'ignored'))
    assert runtime.data_directory()==tmp_path/'cache/spectrum_studio'
    assert runtime.output_directory()==tmp_path/'studio_outputs/spectrum_studio'


def test_exe_paths_persist_across_working_directories(tmp_path,monkeypatch):
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'_MEIPASS',str(tmp_path/'temporary_bundle'),raising=False)
    monkeypatch.delenv('SPECTRUM_STUDIO_DATA_DIR',raising=False)
    monkeypatch.setenv('LOCALAPPDATA',str(tmp_path/'profile'))
    expected=tmp_path/'profile/EEG Paradox/Spectrum Studio'
    monkeypatch.chdir(tmp_path)
    assert runtime.data_directory()==expected
    other=tmp_path/'elsewhere';other.mkdir();monkeypatch.chdir(other)
    assert runtime.data_directory()==expected and runtime.output_directory()==expected/'outputs'
    for key in ['_MNE_FAKE_HOME_DIR','MPLCONFIGDIR']:monkeypatch.delenv(key,raising=False)
    runtime.initialize_standalone()
    assert (expected/'mne_home').is_dir() and (expected/'matplotlib').is_dir()


def test_exe_explicit_storage_cannot_use_deleted_extraction(tmp_path,monkeypatch):
    monkeypatch.setattr(sys,'frozen',True,raising=False)
    monkeypatch.setattr(sys,'_MEIPASS',str(tmp_path/'bundle'),raising=False)
    monkeypatch.setenv('SPECTRUM_STUDIO_DATA_DIR',str(tmp_path/'bundle/data'))
    with pytest.raises(ValueError,match='temporary'):runtime.data_directory()
    monkeypatch.setenv('SPECTRUM_STUDIO_DATA_DIR','relative')
    with pytest.raises(ValueError,match='absolute'):runtime.data_directory()
    monkeypatch.setenv('SPECTRUM_STUDIO_DATA_DIR',str(tmp_path/'persistent'))
    assert runtime.data_directory()==tmp_path/'persistent'
