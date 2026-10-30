"""Persistent writable locations for the portable Windows application."""
from __future__ import annotations

import os
from pathlib import Path
import sys


def frozen():
    return bool(getattr(sys,'frozen',False))


def set_windows_app_id():
    """Give the standalone app its own Windows taskbar identity."""
    if sys.platform=='win32':
        import ctypes
        setter=ctypes.windll.shell32.SetCurrentProcessExplicitAppUserModelID
        setter.argtypes=[ctypes.c_wchar_p];setter.restype=ctypes.c_long
        setter('EEGParadox.SpectrumStudio')


def set_window_icon(root):
    assets=Path(__file__).resolve().parent/'assets'
    if sys.platform=='win32':root.iconbitmap(default=str(assets/'spectrum_studio.ico'))
    else:
        import tkinter as tk
        root._spectrum_icon=tk.PhotoImage(master=root,file=str(assets/'spectrum_studio.png'))
        root.iconphoto(True,root._spectrum_icon)


def data_directory():
    """Source runs keep their existing workspace; EXEs use per-user storage."""
    if not frozen():return Path.cwd()/'cache/spectrum_studio'
    override=os.environ.get('SPECTRUM_STUDIO_DATA_DIR')
    if override:
        directory=Path(override).expanduser()
        if not directory.is_absolute():raise ValueError('SPECTRUM_STUDIO_DATA_DIR must be an absolute folder')
    else:
        local=os.environ.get('LOCALAPPDATA')
        directory=(Path(local) if local else Path.home()/'.local/share')/'EEG Paradox'/'Spectrum Studio'
    directory=directory.resolve()
    bundle=getattr(sys,'_MEIPASS',None)
    if bundle and directory.is_relative_to(Path(bundle).resolve()):
        raise ValueError('App data must be outside the temporary EXE extraction folder')
    return directory


def output_directory():
    return data_directory()/'outputs' if frozen() else Path.cwd()/'studio_outputs/spectrum_studio'


def initialize_standalone():
    """Configure libraries before their first use; never write into the bundle."""
    directory=data_directory();directory.mkdir(parents=True,exist_ok=True)
    for variable,folder in [('_MNE_FAKE_HOME_DIR','mne_home'),('MPLCONFIGDIR','matplotlib')]:
        target=directory/folder;target.mkdir(parents=True,exist_ok=True);os.environ.setdefault(variable,str(target))
    os.environ.setdefault('MNE_LOGGING_LEVEL','ERROR')
    os.environ.setdefault('MPLBACKEND','Agg')
    return directory
