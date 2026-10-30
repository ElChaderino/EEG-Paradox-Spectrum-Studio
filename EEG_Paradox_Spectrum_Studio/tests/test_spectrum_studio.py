import hashlib
import base64
import json
from pathlib import Path

import numpy as np
import pytest

from EEG_Paradox_Spectrum_Studio import SpectrumConfig, analyze_array, analyze_raw, export_studio
from EEG_Paradox_Spectrum_Studio.demo import make_demo


def sine(rate=256., seconds=12., frequency=10., amplitude=20.):
    t = np.arange(int(rate * seconds)) / rate
    return (amplitude * np.sin(2 * np.pi * frequency * t))[None, :]


def test_calibrated_psd_and_integrated_sinusoid_power():
    result = analyze_array(sine(), 256, ["Cz"], unit="uV")
    power = result.psd_uv2_hz[0, 0]
    assert result.frequencies_hz[np.argmax(power)] == 10.
    assert np.sum(power) * result.metadata["bin_spacing_hz"] == pytest.approx(200., rel=1e-6)
    assert result.band_power_uv2["alpha"][0, 0] == pytest.approx(200., rel=1e-6)
    assert result.band_power_uv2["beta"][0, 0] < 1e-20
    assert result.metadata["hann_enbw_hz"] == .375


@pytest.mark.parametrize("unit,scale", [("V", 1e-6), ("mV", 1e-3), ("uV", 1.), ("nV", 1000.)])
def test_explicit_units_are_equivalent(unit, scale):
    a = analyze_array(sine() * scale, 256, ["Cz"], unit=unit)
    b = analyze_array(sine(), 256, ["Cz"], unit="uV")
    np.testing.assert_allclose(a.psd_uv2_hz, b.psd_uv2_hz, atol=1e-18)


def test_bad_intervals_and_nonfinite_windows_keep_original_clock():
    data = np.vstack([sine(seconds=14), sine(seconds=14)])
    data[0, 256 * 10] = np.nan
    r = analyze_array(data, 256, ["F3", "F4"], unit="uV", start_seconds=30., bad_intervals=[(35., 36.)])
    np.testing.assert_allclose(np.diff(r.times_seconds), .5)
    bad = (r.times_seconds - 2 < 36) & (r.times_seconds + 2 > 35)
    assert not r.valid[:, bad].any()
    assert np.isnan(r.psd_uv2_hz[:, bad]).all()
    assert np.any(~r.valid[0] & r.valid[1])
    assert r.times_seconds[0] == 32.
    assert r.times_seconds[-1] == 42.
    assert len(r.times_seconds) == 21


def test_missing_nyquist_bands_are_not_partial_or_zero():
    r = analyze_array(sine(rate=64), 64, ["Cz"], unit="uV", config=SpectrumConfig(fmax_hz=30.))
    assert "gamma_30_45" in r.metadata["unavailable_bands"]
    assert np.isnan(r.band_power_uv2["gamma_30_45"]).all()


@pytest.mark.parametrize("config", [SpectrumConfig(fmax_hz=200.), SpectrumConfig(hop_seconds=0.),
    SpectrumConfig(window_seconds=float("nan")), SpectrumConfig(max_cells=10),
    SpectrumConfig(fmin_hz=9.9, fmax_hz=10.1)])
def test_invalid_settings_fail(config):
    with pytest.raises(ValueError):
        analyze_array(sine(), 256, ["Cz"], unit="uV", config=config)


def test_unknown_units_and_duplicate_channels_fail():
    with pytest.raises(ValueError, match="unit"):
        analyze_array(sine(), 256, ["Cz"], unit="arbitrary")
    with pytest.raises(ValueError, match="unique"):
        analyze_array(np.vstack([sine(), sine()]), 256, ["Cz", "Cz"], unit="uV")


def test_short_recording_never_pads_a_window():
    with pytest.raises(ValueError, match="full window"):
        analyze_array(sine(seconds=1.), 256, ["Cz"], unit="uV")


def test_flat_signal_has_zero_power_and_stays_valid():
    r = analyze_array(np.zeros((1, 2048)), 256, ["Cz"], unit="V")
    assert r.valid.all()
    assert not r.psd_uv2_hz.any()
    assert not r.band_power_uv2["alpha"].any()


def test_export_is_self_contained_escaped_and_hash_bound(tmp_path):
    label = '</script><script>alert(1)</script>__SCRIPT__'
    r = analyze_array(sine(), 256, [label], unit="uV", bad_intervals=[(5., 6.)])
    page = export_studio(r, tmp_path / "new")
    html = page.read_text(encoding="utf-8")
    assert label not in html
    assert "\\u003c/script>" in html
    assert '<script src=' not in html and 'fetch(' not in html
    assert '__PAYLOAD__' not in html
    manifest = json.loads((page.parent / "spectrum_manifest.json").read_text())
    for name, digest in manifest["files_sha256"].items():
        assert hashlib.sha256((page.parent / name).read_bytes()).hexdigest() == digest
    arrays = np.load(page.parent / "spectrum_arrays.npz", allow_pickle=False)
    np.testing.assert_equal(arrays["psd_uv2_hz"], r.psd_uv2_hz)
    assert arrays["valid"].dtype == bool
    with pytest.raises(FileExistsError):
        export_studio(r, page.parent)


def test_binary_viewer_payload_preserves_values_and_masks(tmp_path):
    import re
    r = analyze_array(sine(), 256, ["Cz"], unit="uV", bad_intervals=[(5., 6.)])
    page = export_studio(r, tmp_path / "packed")
    html = page.read_text(encoding="utf-8")
    payload = json.loads(re.search(r'<script id="payload" type="application/json">(.*?)</script>', html, re.S).group(1))
    def unpack(field):
        return np.frombuffer(base64.b64decode(field["base64"]), dtype=field["dtype"]).reshape(field["shape"])
    np.testing.assert_equal(unpack(payload["trace_packed"]), r.trace_uv)
    np.testing.assert_equal(unpack(payload["bands_packed"]["alpha"]), r.band_power_uv2["alpha"])
    np.testing.assert_equal(unpack(payload["psd_packed"]), r.psd_uv2_hz)
    assert np.isnan(unpack(payload["psd_packed"])[~r.valid]).all()
    assert "__GPU_SCRIPT__" not in html and "__WATER_FRAGMENT__" not in html


def test_refresh_changes_viewer_without_rewriting_scientific_files(tmp_path):
    from EEG_Paradox_Spectrum_Studio.export import refresh_studio
    r = analyze_array(sine(), 256, ["Cz"], unit="uV")
    page = export_studio(r, tmp_path / "refresh")
    names = ["spectrum_arrays.npz", "method.json", "band_power.csv"]
    before = {name: (page.parent / name).read_bytes() for name in names}
    assert refresh_studio(page.parent) == page
    for name, data in before.items():
        assert (page.parent / name).read_bytes() == data
    manifest = json.loads((page.parent / "spectrum_manifest.json").read_text())
    from EEG_Paradox_Spectrum_Studio import __version__
    assert manifest["viewer_version"] == __version__
    for name, digest in manifest["files_sha256"].items():
        assert hashlib.sha256((page.parent / name).read_bytes()).hexdigest() == digest
    page.write_text("custom user edit", encoding="utf-8")
    with pytest.raises(ValueError, match="modified"):
        refresh_studio(page.parent)
    assert page.read_text() == "custom user edit"


def test_source_subset_matches_pinned_git_blobs():
    root = Path(__file__).parents[1] / "third_party/aethersdr"
    manifest = json.loads((root / "SOURCE_MANIFEST.json").read_text())
    assert len(manifest["commit"]) == 40
    for file in manifest["files"]:
        data = (root / file["path"]).read_bytes().replace(b"\r\n", b"\n")
        digest = hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest()
        assert file["git_blob_sha"] == digest, file["path"]


def test_demo_clearly_identifies_synthetic_data():
    r = make_demo()
    assert r.metadata["synthetic"] is True
    assert "SYNTHETIC" in r.metadata["condition"]
    assert r.metadata["invalid_windows_per_channel"][0] > 0


def test_reference_suffix_is_eeg_but_sync_and_label_channels_are_auxiliary():
    from EEG_Paradox_Spectrum_Studio.adapters import _eeg_picks
    class Raw:
        ch_names = ["Fp1-REF", "EEG Cz-REF", "ECG", "VSyn", "ASyn", "LABEL", "Reference", "Foto", "Photo", "EEG Photodiode"]
        info = {"bads": []}
        def get_channel_types(self):
            return ["eeg"] * len(self.ch_names)
    assert _eeg_picks(Raw(), None) == ["Fp1-REF", "EEG Cz-REF"]
    with pytest.raises(ValueError, match="auxiliary"):
        _eeg_picks(Raw(), ["LABEL"])
    with pytest.raises(ValueError, match="auxiliary"):
        _eeg_picks(Raw(), ["Foto"])


def test_edf_auxiliary_unknown_units_do_not_reject_calibrated_scalp(monkeypatch, tmp_path):
    monkeypatch.setenv("_MNE_FAKE_HOME_DIR", str(tmp_path))
    monkeypatch.setenv("MNE_LOGGING_LEVEL", "ERROR")
    from pyedflib import highlevel
    from EEG_Paradox_Spectrum_Studio import analyze_file
    path = tmp_path / "scalp_with_aux.edf"
    headers = highlevel.make_signal_headers(["Cz-REF", "VSyn", "LABEL"], sample_frequency=256,
                                          physical_min=-100., physical_max=100.)
    headers[2]["dimension"] = ""
    highlevel.write_edf(str(path), np.vstack([sine(), sine(), sine()]), headers)
    r = analyze_file(path)
    assert r.metadata["channel_names"] == ["Cz-REF"]
    assert set(r.metadata["excluded_channels"]) == {"VSyn", "LABEL"}
    assert r.band_power_uv2["alpha"][0, 0] == pytest.approx(200., rel=.001)


def test_mne_cropped_original_time_annotations_and_no_input_mutation(monkeypatch, tmp_path):
    monkeypatch.setenv("_MNE_FAKE_HOME_DIR", str(tmp_path))
    monkeypatch.setenv("MNE_LOGGING_LEVEL", "ERROR")
    import mne
    data = np.vstack([sine(seconds=30.) * 1e-6, sine(seconds=30.) * 1e-6])
    raw = mne.io.RawArray(data, mne.create_info(["Cz", "EOG"], 256., ["eeg", "eog"]), verbose="ERROR")
    raw.set_annotations(mne.Annotations([12.], [1.], ["BAD_artifact"]))
    raw.crop(tmin=8.)
    before = raw.get_data().copy()
    result = analyze_raw(raw, start=2., duration=12.)
    assert result.metadata["channel_names"] == ["Cz"]
    assert result.times_seconds[0] == 12.
    bad = (result.times_seconds - 2 < 13.) & (result.times_seconds + 2 > 12.)
    assert not result.valid[:, bad].any()
    assert result.metadata["reference"] == "unknown"
    np.testing.assert_array_equal(before, raw.get_data())


def test_calibrated_edf_round_trip_and_unknown_dimension_rejected(monkeypatch, tmp_path):
    monkeypatch.setenv("_MNE_FAKE_HOME_DIR", str(tmp_path))
    monkeypatch.setenv("MNE_LOGGING_LEVEL", "ERROR")
    from pyedflib import highlevel
    from EEG_Paradox_Spectrum_Studio import analyze_file
    for unit in ("uV", "unknown"):
        path = tmp_path / (unit + ".edf")
        headers = highlevel.make_signal_headers(["Cz"], dimension=unit, sample_frequency=256,
                                               physical_min=-100., physical_max=100.)
        highlevel.write_edf(str(path), sine(), headers)
        if unit == "unknown":
            with pytest.raises(ValueError, match="physical dimension"):
                analyze_file(path)
        else:
            r = analyze_file(path, reference="AFz", condition="EC", simultaneous=True)
            assert r.band_power_uv2["alpha"][0,0] == pytest.approx(200., rel=.001)
            assert r.metadata["source_sha256"] == hashlib.sha256(path.read_bytes()).hexdigest()
