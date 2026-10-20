# Dropshift build bootstrap - fetches everything needed to run and build Dropshift.
#
#   powershell -ExecutionPolicy Bypass -File build\bootstrap.ps1 [-Part all|python|components]
#
# Everything heavy lives in %LOCALAPPDATA%\DropshiftBuild so the project folder
# stays small. Delete that folder any time; this script recreates it.
#   venv\        Python venv with PySide6, Pillow, pypdf, pypdfium2, pywin32, PyInstaller
#   components\  ffmpeg\ - bundled into the app beside Dropshift.exe
# The script is idempotent: finished steps are skipped on re-run.

param(
    [ValidateSet('all', 'python', 'components')]
    [string]$Part = 'all'
)

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$Cache = Join-Path $env:LOCALAPPDATA 'DropshiftBuild'
$Dl    = Join-Path $Cache 'downloads'
$Venv  = Join-Path $Cache 'venv'
$Comp  = Join-Path $Cache 'components'
New-Item -ItemType Directory -Force $Dl, $Comp | Out-Null

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

function Get-File($url, $dest) {
    if (Test-Path $dest) { Write-Host "cached  $dest"; return }
    # Grabbit's build fetches the same FFmpeg; reuse its copy rather than download again.
    $shared = Join-Path $env:LOCALAPPDATA ('GrabbitBuild\downloads\' + (Split-Path -Leaf $dest))
    if (Test-Path $shared) { Write-Host "reuse   $shared"; Copy-Item $shared $dest; return }
    Write-Host "fetch   $url"
    & curl.exe -L --fail --retry 3 --silent --show-error -o "$dest.part" $url
    if ($LASTEXITCODE -ne 0) { Fail "download failed: $url" }
    Move-Item -Force "$dest.part" $dest
}

# --------------------------------------------------------------------------- Python
if ($Part -in 'all', 'python') {
    $py = Join-Path $Venv 'Scripts\python.exe'
    if (-not (Test-Path $py)) {
        Write-Host "venv    $Venv"
        & py -3.12 -m venv $Venv
        if ($LASTEXITCODE -ne 0) { Fail 'venv creation failed (needs Python 3.12 via the py launcher)' }
    }
    & $py -m pip install --disable-pip-version-check --upgrade pip | Out-Null
    & $py -m pip install --disable-pip-version-check -r (Join-Path $PSScriptRoot 'requirements.txt')
    if ($LASTEXITCODE -ne 0) { Fail 'pip install failed' }
    Write-Host 'Python venv ready'
}

# --------------------------------------------------------------------------- ffmpeg
if ($Part -in 'all', 'components') {
    $ffZip = Join-Path $Dl 'ffmpeg-master-latest-win64-gpl-shared.zip'
    Get-File 'https://github.com/yt-dlp/FFmpeg-Builds/releases/download/latest/ffmpeg-master-latest-win64-gpl-shared.zip' $ffZip
    $ffDir = Join-Path $Comp 'ffmpeg'
    if (-not (Test-Path (Join-Path $ffDir 'ffmpeg.exe'))) {
        $tmp = Join-Path $Dl 'ffmpeg-extract'
        Remove-Item -Recurse -Force $tmp -ErrorAction SilentlyContinue
        New-Item -ItemType Directory -Force $tmp, $ffDir | Out-Null
        & tar.exe -xf $ffZip -C $tmp
        $exe = Get-ChildItem $tmp -Recurse -Filter ffmpeg.exe | Select-Object -First 1
        if (-not $exe) { Fail 'ffmpeg.exe not found in archive' }
        Get-ChildItem $exe.DirectoryName | Where-Object { $_.Name -ne 'ffplay.exe' } | Copy-Item -Destination $ffDir
        $lic = Get-ChildItem $tmp -Recurse -Filter 'LICENSE*' | Select-Object -First 1
        if ($lic) { Copy-Item $lic.FullName (Join-Path $ffDir 'LICENSE.txt') }
        Remove-Item -Recurse -Force $tmp
    }
    Write-Host 'components ready'
}
