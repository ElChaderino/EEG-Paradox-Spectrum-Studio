import copy
import hashlib
import json

import numpy as np
import pytest

from EEG_Paradox_Spectrum_Studio import (QualityConfig, analyze_array, attach_review,
    artifact_review_events, event_table_events, export_project_review, export_studio, stage_events)
from EEG_Paradox_Spectrum_Studio.review import fingerprint, normalize_events


def fixture(seconds=12, reference="AFz", simultaneous=True):
    t = np.arange(seconds * 256) / 256
    data = np.vstack([20 * np.sin(2 * np.pi * 10 * t), np.zeros_like(t), 100 * np.sin(2 * np.pi * 50 * t)])
    return analyze_array(data, 256, ["F3", "F4", "Cz"], unit="uV", reference=reference, simultaneous=simultaneous)


def test_quality_flags_keep_flat_high_amplitude_and_line_noise_valid():
    result = fixture()
    assert np.all(result.quality["flags"][1] & 4)
    assert np.all(result.quality["flags"][2] & 8)
    assert np.all(result.quality["flags"][2] & 32)
    assert result.quality["line_50_ratio"][2, 0] == pytest.approx(1)
    assert result.valid.all()
    assert result.band_power_uv2["alpha"][0, 0] == pytest.approx(200, rel=1e-6)
    assert result.metadata["quality_contract"]["rail_check_available"] is False


def test_only_declared_rails_produce_clipping_flag_and_bad_nonfinite_flags_survive():
    data = np.zeros((2, 3072)); data[0, 4] = 10; data[1, 2500] = np.nan
    a = analyze_array(data, 256, ["F3", "F4"], unit="uV", bad_intervals=[(2, 3)],
                      quality_config=QualityConfig(physical_rails_uv=(-10, 10)))
    assert a.quality["flags"][0, 0] & 16
    assert a.quality["flags"][0, 0] & 2
    assert not a.valid[0, 0]
    assert np.any(a.quality["flags"][1] & 1)
    b = analyze_array(np.zeros((1, 2048)), 128, ["Cz"], unit="uV")
    assert not np.any(b.quality["flags"] & 16)
    with pytest.raises(ValueError):
        fixture_config = QualityConfig(physical_rails_uv=(10, -10)).validate()


def test_quality_uses_declared_calibrated_units():
    a = fixture()
    t = np.arange(12 * 256) / 256
    b = analyze_array((20e-6 * np.sin(2*np.pi*10*t))[None], 256, ["F3"], unit="V")
    np.testing.assert_allclose(a.quality["peak_to_peak_uv"][0], b.quality["peak_to_peak_uv"][0])


def test_events_require_valid_times_channels_status_and_unique_ids():
    r = fixture()
    event = {"id": "a", "onset_seconds": 2, "duration_seconds": 1, "label": "EO", "status": "recorded", "channels": []}
    for change in ({"onset_seconds": float("nan")}, {"duration_seconds": -1}, {"status": "automatic accepted"}, {"channels": ["unknown"]}):
        with pytest.raises(ValueError):normalize_events([{**event, **change}], r.metadata)
    with pytest.raises(ValueError):normalize_events([event, event], r.metadata)
    before = r.psd_uv2_hz.copy()
    attach_review(r, events=[event])
    np.testing.assert_equal(r.psd_uv2_hz, before)
    assert r.review["events"][0]["label"] == "EO"


def test_template_is_explicit_and_sequential_remains_declared(monkeypatch, tmp_path):
    monkeypatch.setenv("_MNE_FAKE_HOME_DIR", str(tmp_path))
    r = fixture(simultaneous=False)
    attach_review(r, montage="standard_1020")
    assert len(r.review["montage"]["positions_xy"]) == 3
    assert r.review["montage"]["simultaneous"] is False
    assert "not measured" in r.review["montage"]["source"]
    with pytest.raises(ValueError):attach_review(r, positions={"F3": [0, 0]})
    with pytest.raises(ValueError):attach_review(r, positions={"bipolar": [0, 0]}, position_source="measured")


def test_artifact_sidecar_matches_source_and_preserves_status():
    from cracker.review.artifact_annotations import ArtifactReviewSidecar, ArtifactAnnotation
    r = fixture();r.metadata["source_sha256"] = "a"*64
    sidecar = ArtifactReviewSidecar("input.edf", "a"*64, 12, 256, ["F3", "F4", "Cz"],
      [ArtifactAnnotation(2, 1, "flatline", ("F4",), "accepted"), ArtifactAnnotation(5, 1, "high amplitude", ("F3",))])
    events = artifact_review_events(sidecar, r)
    assert [e["status"] for e in events] == ["accepted", "proposed"]
    assert r.valid.all()
    tampered=sidecar.to_dict();tampered["annotations"][0]["status"]="rejected"
    with pytest.raises(ValueError,match="hash"):artifact_review_events(tampered,r)
    sidecar.input_sha256 = "b"*64
    with pytest.raises(ValueError):artifact_review_events(sidecar, r)


def test_event_clock_and_sleep_stage_decisions_are_not_guessed():
    r = fixture();r.metadata["source_sha256"] = "a"*64
    table = {"schema": "eeg-paradox.event-table.v1", "input_sha256": "a"*64, "sfreq": 256,
      "events": [{"event_id": "a", "onset_s": 1, "sample": 256, "code": "stimulus", "clock": "sample_clock"}]}
    with pytest.raises(ValueError):event_table_events(table, r)
    assert event_table_events(table, r, sample_clock_origin_seconds=8)[0]["onset_seconds"] == 9
    stages = stage_events([{"onset_seconds": 0, "duration_seconds": 10, "label": "N2", "reviewed": False}], r, input_sha256="a"*64, origin="Sleep Studio")
    assert stages[0]["status"] == "proposed"


def test_cross_recording_comparison_requires_known_identical_contract(tmp_path):
    a, b = fixture(), fixture()
    export_studio(a, tmp_path / "valid", comparison=b)
    b.metadata["reference"] = "unknown"
    with pytest.raises(ValueError, match="reference"):export_studio(a, tmp_path / "invalid", comparison=b)
    assert not (tmp_path / "invalid").exists()
    a.metadata["reference"] = b.metadata["reference"] = " Unknown "
    with pytest.raises(ValueError):export_studio(a, tmp_path / "unknown", comparison=b)


def test_handoff_is_hash_bound_and_other_project_data_not_mutated(tmp_path):
    r = fixture()
    path = export_project_review(r, tmp_path / "handoff", project="Decoder", positions={"F3": [-.4,.5], "F4": [.4,.5], "Cz": [0,0]}, position_source="test fixture")
    handoff = json.loads(path.read_text());assert handoff["project"] == "Decoder"
    manifest = json.loads((path.parent / "spectrum_manifest.json").read_text())
    for name, digest in manifest["files_sha256"].items():assert hashlib.sha256((path.parent/name).read_bytes()).hexdigest() == digest
    before = fingerprint(r);r.psd_uv2_hz[0, 0, 0] += 1
    assert fingerprint(r) != before


def test_review_quality_and_comparison_survive_viewer_refresh(tmp_path):
    from EEG_Paradox_Spectrum_Studio import refresh_studio
    a, b = fixture(), fixture()
    attach_review(a, positions={"F3": [-.4,.5], "F4": [.4,.5], "Cz": [0,0]}, position_source="fixture")
    path = export_studio(a, tmp_path / "refresh_review", comparison=b)
    files = ["spectrum_arrays.npz", "method.json", "review_context.json", "comparison_recording.json", "band_power.csv"]
    before = {name: (path.parent/name).read_bytes() for name in files}
    refresh_studio(path.parent)
    for name, data in before.items():assert (path.parent/name).read_bytes() == data
    arrays = np.load(path.parent / "spectrum_arrays.npz", allow_pickle=False)
    np.testing.assert_array_equal(arrays["quality_flags"], a.quality["flags"])


def test_export_rebinds_fingerprint_after_result_changes(tmp_path):
    r = fixture();attach_review(r);old = r.review["fingerprint"]
    r.psd_uv2_hz[0, 0, 0] += 1
    path=export_studio(r, tmp_path/"changed")
    assert json.loads((path.parent/"review_context.json").read_text())["fingerprint"] != old


def test_line_checks_are_unavailable_beyond_nyquist():
    from EEG_Paradox_Spectrum_Studio import SpectrumConfig
    r=analyze_array(np.zeros((1, 2048)),64,["Cz"],unit="uV",config=SpectrumConfig(fmax_hz=30))
    assert np.isnan(r.quality["line_50_ratio"]).all()
    assert np.isnan(r.quality["line_60_ratio"]).all()


def test_template_does_not_invent_bipolar_or_unknown_sites(monkeypatch,tmp_path):
    monkeypatch.setenv("_MNE_FAKE_HOME_DIR",str(tmp_path))
    r=analyze_array(np.zeros((2,2048)),256,["F3-F4","custom-site"],unit="uV",simultaneous=True)
    attach_review(r,montage="standard_1020")
    assert not r.review["montage"]["positions_xy"]


def test_python_workspace_loader_checks_layout_and_imported_baseline(tmp_path):
    from EEG_Paradox_Spectrum_Studio import load_workspace
    r=fixture();attach_review(r)
    workspace={"schema":"eeg-paradox.spectrum-workspace.v1","fingerprint":fingerprint(r),
      "view":{"ch":0,"row":0,"low":.5,"high":45,"floor":-20,"range":50,"perspective":.58,"lift":.65,
              "palette":"sdr","quality":"balanced","depth":96,"dock":"summary","scopeCentered":True,"focus":False,"speed":1,"compare":[0,1]},
      "review":{"panels":[0,1],"band":[8,13],"linked":True,"fps":30,"mapMax":200,"mapBand":"custom","bSource":"primary","a":[0,6],"b":[6,12],"events":[]}}
    path=tmp_path/"workspace.json";path.write_text(json.dumps(workspace))
    assert load_workspace(path,r)["view"]["row"] == 0
    workspace['view']['mapExpanded']=True;path.write_text(json.dumps(workspace))
    assert load_workspace(path,r)['view']['mapExpanded'] is True
    for invalid in [None,1,'yes']:
        workspace['view']['mapExpanded']=invalid;path.write_text(json.dumps(workspace))
        with pytest.raises(ValueError,match='display/layout'):load_workspace(path,r)
    workspace['view']['mapExpanded']=True;workspace['view']['focus']=True;path.write_text(json.dumps(workspace))
    with pytest.raises(ValueError,match='display/layout'):load_workspace(path,r)
    del workspace['view']['mapExpanded'];workspace['view']['focus']=False
    workspace['view']['dock']='trend';workspace['review']['trend']=[1,10];path.write_text(json.dumps(workspace))
    assert load_workspace(path,r)['review']['trend']==[1,10]
    workspace['review']['markerSeconds']=10;path.write_text(json.dumps(workspace))
    assert load_workspace(path,r)['review']['markerSeconds']==10
    for invalid in [False,None,'10',7]:
        workspace['review']['markerSeconds']=invalid;path.write_text(json.dumps(workspace))
        with pytest.raises(ValueError,match='clock interval'):load_workspace(path,r)
    del workspace['review']['markerSeconds']
    for invalid in [[True,10],['1',10],[-1,10],[0,13]]:
        workspace['review']['trend']=invalid;path.write_text(json.dumps(workspace))
        with pytest.raises(ValueError,match='trend'):load_workspace(path,r)
    del workspace['review']['trend'];workspace['view']['dock']='summary'
    workspace["review"]["panels"]=[999];path.write_text(json.dumps(workspace))
    with pytest.raises(ValueError):load_workspace(path,r)
    workspace["review"]["panels"]=[0];workspace["review"]["bSource"]="imported";path.write_text(json.dumps(workspace))
    with pytest.raises(ValueError,match="comparison"):load_workspace(path,r)
    workspace["review"]["bSource"]="primary"
    workspace["review"]["events"]=[{"id":f"large:{i}","onset_seconds":3,"duration_seconds":1,
        "label":"E"*500,"status":"proposed","kind":"task","channels":[],"origin":"review"} for i in range(7000)]
    path.write_text(json.dumps(workspace,indent=2),encoding="utf-8")
    assert path.stat().st_size>2_000_000
    assert len(load_workspace(path,r)["review"]["events"])==7000


def test_topo_adapter_preserves_original_names_and_rejects_maps_only():
    from EEG_Paradox_Spectrum_Studio.adapters import analyze_topo_data
    class Topo:
        channels = ["EEG Cz-REF"]
        data = np.zeros((1, 2048));sfreq=256;unit="uV"
        def validate(self):self.channels=["Cz"]
    dataset=Topo();result=analyze_topo_data(dataset)
    assert dataset.channels == ["EEG Cz-REF"]
    assert result.metadata["channel_names"] == ["Cz"]
    dataset.data=None
    with pytest.raises(ValueError, match="continuous"):analyze_topo_data(dataset)


def test_project_handoff_preserves_previously_attached_review_events(tmp_path):
    r=fixture()
    event={"id":"reviewed-task", "onset_seconds":3, "duration_seconds":2,
           "label":"Reviewed task", "status":"accepted", "kind":"task", "channels":[], "origin":"Detection Studio"}
    attach_review(r,events=[event],project="Detection Studio")
    path=export_project_review(r,tmp_path/"preserved_events",project="Decoder")
    context=json.loads((path.parent/"review_context.json").read_text())
    assert context["events"][0]["id"] == "reviewed-task"
    assert context["events"][0]["status"] == "accepted"


def test_invalid_review_update_keeps_existing_context_unchanged():
    r=fixture();attach_review(r,project="Decoder")
    before=copy.deepcopy(r.review)
    with pytest.raises(ValueError):
        attach_review(r,project="Other",events=[{"label":"replacement","onset_seconds":2}],
                      positions={"unknown":[0,0]},position_source="test")
    assert r.review == before
