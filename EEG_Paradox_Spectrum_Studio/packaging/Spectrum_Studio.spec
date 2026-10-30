# Build with the maintained setup environment; no recordings are bundled.
from pathlib import Path
import importlib.util
from PyInstaller.utils.hooks import collect_data_files,collect_submodules,copy_metadata

module=Path(SPECPATH).resolve().parent
workspace=module.parent
datas=[]
for folder in ['assets','third_party']:
    for file in (module/folder).rglob('*'):
        if file.is_file():datas.append((str(file),'EEG_Paradox_Spectrum_Studio/'+str(file.relative_to(module).parent)))
for name in ['LICENSE','NOTICE.md','README.md','VALIDATION.md']:
    datas.append((str(module/name),'EEG_Paradox_Spectrum_Studio'))
# The audited native adapter checks the real source path and hashes core.py.
# Keep this small maintained converter as loose embedded source, not a second
# import of the same package in PYZ with a different provenance path.
native=workspace/'vendor_sources/wineeg_converter'
for name in ['__init__.py','core.py','service.py']:
    datas.append((str(native/'wineeg_converter'/name),'vendor_sources/wineeg_converter/wineeg_converter'))
datas.append((str(native/'README.md'),'vendor_sources/wineeg_converter'))

def mne_module(name):
    return not any(part in {'tests','datasets','commands','gui'} for part in name.split('.')) and not name.startswith('mne.viz.backends')
hidden=collect_submodules('mne',filter=mne_module,on_error='warn once')
datas+=collect_data_files('mne')
for name in ['numpy','scipy','mne','pyedflib','matplotlib','reportlab','lazy_loader']:
    datas+=copy_metadata(name)
hidden+=['tkinter','tkinter.ttk','tkinter.filedialog','tkinter.messagebox',
         'EEG_Paradox_Auto_Pipeline.eeg_auto_pipeline.native_intake',
         'EEG_Paradox_Auto_Pipeline.eeg_auto_pipeline.reference_evidence',
         'cracker.formats.bxs_converter','bxs_processor.direct_bxs_converter',
         'converters.converter_a_bxs_edf_to_ng.converter','pyedflib._extensions._pyedflib',
         'matplotlib.backends.backend_agg','scipy.io.matlab']

a=Analysis([str(module/'packaging/launch_studio.py')],pathex=[str(workspace)],
    binaries=[],datas=datas,hiddenimports=hidden,
    hookspath=[],runtime_hooks=[],
    excludes=['wineeg_converter','PySide6','PyQt5','PyQt6','PySide2','qtpy',
              'pyvista','vtk','vtkmodules','torch','tensorflow','numba','sklearn',
              'IPython','jupyter','pytest','mne.gui','mne.viz.backends'],
    hooksconfig={'matplotlib':{'backends':['Agg']}},
    module_collection_mode={'EEG_Paradox_Spectrum_Studio':'pyz+py',
        'EEG_Paradox_Auto_Pipeline.eeg_auto_pipeline.native_intake':'pyz+py',
        'EEG_Paradox_Auto_Pipeline.eeg_auto_pipeline.reference_evidence':'pyz+py'},
    noarchive=False)
pyz=PYZ(a.pure)
exe=EXE(pyz,a.scripts,a.binaries,a.datas,[],name='Spectrum Studio',
    debug=False,bootloader_ignore_signals=False,strip=False,upx=False,
    console=False,disable_windowed_traceback=False,uac_admin=False,
    icon=str(module/'assets/spectrum_studio.ico'),
    version=str(module/'packaging/version_info.txt'))
