# Single-file Windows build

`Spectrum Studio.exe` opens the recording loader when double-clicked. It includes
Python, the scientific readers/libraries, maintained WinEEG conversion code, local
viewer resources and PNG/PDF reporting. It does not include recordings, generated
reports, a browser installation or a second EEG Paradox application.

Only the EXE is needed to run. Source archives and build manifests are developer
artifacts and are kept outside the finished app folder. The icon is embedded in
the EXE, with sizes from 16 to 256 pixels; the same icon is used for the native
window and its Windows taskbar identity. `Build_Icon.py` renders the original
vector source in `assets/spectrum_studio.svg` before a repeat build.

The local interactive viewer uses the user's installed Windows browser. Settings,
diagnostics, library caches and default outputs live in
`%LOCALAPPDATA%\EEG Paradox\Spectrum Studio`. The output folder can be changed in
the loader. Moving the EXE does not reset that profile. An absolute
`SPECTRUM_STUDIO_DATA_DIR` environment variable selects an isolated profile for
qualification or a portable deployment. It must be outside the extraction folder.
Ordinary source launches retain the existing workspace locations.

## Build

From the standalone repository root, install the project in a Python environment
and run the build script with that environment's Python:

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e .
& .\EEG_Paradox_Spectrum_Studio\packaging\Build_Single_EXE.ps1 -Python .\.venv\Scripts\python.exe
```

Use 64-bit Windows and a 64-bit Python runtime. PyInstaller is installed in an
isolated build-tools folder; the scientific environment is not changed. The spec
bundles the required maintained intake modules, rather than the other EEG Paradox
applications. Build outputs go to `studio_outputs/spectrum_studio_standalone`.
`-OutputDirectory` can select another folder. In the larger Decoder checkout,
the existing `.venv_live` interpreter remains the default when `-Python` is omitted.

The build includes the public `VALIDATION.md` summary. Internal real-recording
acceptance logs and their identifying run details are deliberately excluded.

The WinEEG core/service are embedded as source at their original relative paths:
the audited intake still verifies the converter path and hashes its actual
`core.py`. Matplotlib uses the noninteractive Agg renderer. Tk is included for the
loader; Qt and unrelated graphics/ML runtimes are excluded. MNE's lazy import
stubs and montage data are retained. The application modules are embedded with
their corresponding source and GPL attribution.

Qualification must copy **only the EXE** to a separate folder, clear Python and
virtual-environment variables, remove Python from PATH, and run real EDF/native
EEG inputs. Check the run inventory, source hashes and numerical arrays against
the source build. Also check a fresh GUI launch, file picker, report/viewer/folder
opening, restart persistence and normal close. Never include qualification inputs
or outputs in the executable.

PyInstaller documents the temporary extraction and resource paths in its
[runtime information](https://pyinstaller.org/en/stable/runtime-information.html),
and the one-file layout in its
[spec-file documentation](https://pyinstaller.org/en/stable/spec-files.html).
The extraction folder is not used for persistent recordings or settings.
