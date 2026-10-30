param([string]$Python,[string]$OutputDirectory)
$ErrorActionPreference='Stop'
$module=Split-Path -Parent $PSScriptRoot
$workspace=Split-Path -Parent $module
if(-not $Python){$Python=Join-Path $workspace '.venv_live/Scripts/python.exe'}
$Python=(Resolve-Path -LiteralPath $Python).Path
$buildRoot=Join-Path $workspace 'work/spectrum_studio_build'
$toolsFolder=Join-Path $buildRoot 'tools'
$distribution=if($OutputDirectory){[System.IO.Path]::GetFullPath($OutputDirectory)}else{Join-Path $workspace 'studio_outputs/spectrum_studio_standalone'}
New-Item -ItemType Directory -Force $buildRoot | Out-Null
& $Python (Join-Path $PSScriptRoot 'Build_Icon.py')
if($LASTEXITCODE -ne 0){throw 'The application icon could not be built.'}
if(-not (Test-Path -LiteralPath (Join-Path $toolsFolder 'PyInstaller/__init__.py'))){
    & $Python -m pip install --target $toolsFolder 'pyinstaller==6.22.3'
    if($LASTEXITCODE -ne 0){throw 'The isolated packaging tools could not be installed.'}
}
$savedPath=$env:PYTHONPATH
$savedMne=$env:_MNE_FAKE_HOME_DIR
$savedMpl=$env:MPLCONFIGDIR
try{
    $env:PYTHONPATH=$toolsFolder
    $env:_MNE_FAKE_HOME_DIR=Join-Path $buildRoot 'mne'
    $env:MPLCONFIGDIR=Join-Path $buildRoot 'matplotlib'
    & $Python -m PyInstaller (Join-Path $PSScriptRoot 'Spectrum_Studio.spec') --noconfirm --distpath $distribution --workpath (Join-Path $buildRoot 'pyinstaller')
    if($LASTEXITCODE -ne 0){throw 'Spectrum Studio EXE build failed.'}
    Get-FileHash -Algorithm SHA256 -LiteralPath (Join-Path $distribution 'Spectrum Studio.exe')
}finally{
    $env:PYTHONPATH=$savedPath
    $env:_MNE_FAKE_HOME_DIR=$savedMne
    $env:MPLCONFIGDIR=$savedMpl
}
