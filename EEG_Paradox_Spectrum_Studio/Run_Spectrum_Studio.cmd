@echo off
setlocal
cd /d "%~dp0.."
set "SPECTRUM_PY=%CD%\.venv_live\Scripts\python.exe"
if not exist "%SPECTRUM_PY%" set "SPECTRUM_PY=python"
set "MNE_LOGGING_LEVEL=ERROR"
set "_MNE_FAKE_HOME_DIR=%CD%\cache\spectrum_studio\mne_home"
set "MPLCONFIGDIR=%CD%\cache\spectrum_studio\matplotlib"
"%SPECTRUM_PY%" -m EEG_Paradox_Spectrum_Studio --gui
if errorlevel 1 (
  echo Spectrum Studio could not start. See the error above.
  pause
  exit /b 1
)
endlocal
