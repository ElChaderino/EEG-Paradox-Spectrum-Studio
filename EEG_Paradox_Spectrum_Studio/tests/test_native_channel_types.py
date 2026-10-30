"""Coded native auxiliary lanes must not become EEG when labels are generic."""
import hashlib
import struct

import numpy as np
import pytest

from EEG_Paradox_Spectrum_Studio import analyze_file
from EEG_Paradox_Spectrum_Studio.inspection import inspect_recording


@pytest.mark.parametrize('kind,expected_type',[(1,'misc'),(4,'stim'),(5,'stim')])
def test_native_auxiliary_codes_survive_conversion_with_unfamiliar_labels(tmp_path,monkeypatch,kind,expected_type):
    from EEG_Paradox_Auto_Pipeline.eeg_auto_pipeline.native_intake import CONVERTER_ROOT
    monkeypatch.syspath_prepend(str(CONVERTER_ROOT))
    from wineeg_converter import core
    t=np.arange(3072)/256;signal=np.rint(2000*np.sin(2*np.pi*10*t)).astype(int).tolist()
    names=['Cz','F3','F4','A1','A2','SignalX'];source=tmp_path/'typed.eeg'
    core.write_legacy_eeg(source,sample_rate_hz=256,channels=[(name,.01,0.) for name in names],
        digital_samples=[signal]*5+[[0]*len(signal)])
    native=core.read_legacy_eeg(source);data=bytearray(native.raw)
    struct.pack_into('<h',data,native.header['channel_table_offset']+5*core.LEGACY_CHANNEL_DESCRIPTOR_SIZE+0x1c,kind)
    for index in (3,4):struct.pack_into('<h',data,native.header['channel_table_offset']+index*core.LEGACY_CHANNEL_DESCRIPTOR_SIZE+0x1c,2)
    source.write_bytes(data);before=hashlib.sha256(data).hexdigest()
    info=inspect_recording(source)
    assert info['channels'][-1]['type']==expected_type
    assert not info['channels'][-1]['eligible']
    result=analyze_file(source,conversion_dir=tmp_path/'intake')
    assert result.metadata['channel_names']==names[:5]
    assert result.metadata['excluded_channels']==['SignalX']
    descriptors=result.metadata['input_provenance']['native_channel_descriptors']
    assert descriptors[-1]['kind']==kind and descriptors[-1]['effective_type']==expected_type
    assert all(item['effective_type']=='eeg' for item in descriptors[:5])
    np.testing.assert_allclose(result.band_power_uv2['alpha'][:,0],200,rtol=.001)
    assert hashlib.sha256(source.read_bytes()).hexdigest()==before
