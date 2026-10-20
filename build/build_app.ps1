# Packages Dropshift into dist\Dropshift\Dropshift.exe together with FFmpeg.
#
#   powershell -ExecutionPolicy Bypass -File build\build_app.ps1
#
# Needs bootstrap.ps1 (venv + ffmpeg).

$ErrorActionPreference = 'Continue'
$ProgressPreference = 'SilentlyContinue'

$Root  = Split-Path -Parent $PSScriptRoot
$Cache = Join-Path $env:LOCALAPPDATA 'DropshiftBuild'
$Venv  = Join-Path $Cache 'venv'
$Comp  = Join-Path $Cache 'components'
$Py    = Join-Path $Venv 'Scripts\python.exe'
$Dist  = Join-Path $Root 'dist\Dropshift'
$Tools = Join-Path $Dist 'tools'

function Fail($msg) { Write-Host "ERROR: $msg" -ForegroundColor Red; exit 1 }

if (-not (Test-Path $Py)) { Fail "venv missing - run build\bootstrap.ps1 first" }
# PyInstaller empties dist\Dropshift first and stops at the first file in use,
# which leaves a running copy with half its files gone.
$Running = Get-Process -Name Dropshift -ErrorAction SilentlyContinue |
    Where-Object { $_.Path -and $_.Path -like (Join-Path $Dist '*') }
if ($Running) { Fail "Dropshift is running from $Dist - quit it from the tray first" }

# Every module must at least import before it is packaged: a mistake caught
# here prints a traceback, where the windowed exe would put up an error box.
Write-Host '>> import check'
& $Py -c "import sys; sys.path.insert(0, r'$Root\app'); import dropshift.app"
if ($LASTEXITCODE -ne 0) { Fail 'Dropshift does not import - fix the error above' }

Write-Host '>> icon'
& $Py (Join-Path $PSScriptRoot 'make_icon.py')
if ($LASTEXITCODE -ne 0) { Fail 'icon generation failed' }

Write-Host '>> PyInstaller'
Push-Location $Root
& $Py -m PyInstaller --noconfirm --clean --distpath (Join-Path $Root 'dist') `
    --workpath (Join-Path $Cache 'pyinstaller') (Join-Path $PSScriptRoot 'dropshift.spec')
$code = $LASTEXITCODE
Pop-Location
if ($code -ne 0) { Fail "PyInstaller failed ($code)" }
if (-not (Test-Path (Join-Path $Dist 'Dropshift.exe'))) { Fail 'Dropshift.exe was not produced' }

Write-Host '>> bundling FFmpeg'
New-Item -ItemType Directory -Force (Join-Path $Tools 'ffmpeg') | Out-Null
if (Test-Path (Join-Path $Comp 'ffmpeg\ffmpeg.exe')) {
    Copy-Item (Join-Path $Comp 'ffmpeg\*') (Join-Path $Tools 'ffmpeg') -Force -Recurse
} else { Write-Host 'WARNING: ffmpeg not found; video and audio will be unavailable' -ForegroundColor Yellow }

Copy-Item (Join-Path $Root 'README.md') $Dist -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $Root 'THIRD-PARTY-NOTICES.md') $Dist -Force -ErrorAction SilentlyContinue
Copy-Item (Join-Path $Root 'LICENSE') $Dist -Force -ErrorAction SilentlyContinue

# Does it start? A build can come out of PyInstaller without complaint and
# still be unable to open a window. --self-test loads what a real start loads
# and answers with the version.
$Answer = Join-Path $env:TEMP "dropshift-build-self-test-$PID.txt"
Remove-Item $Answer -ErrorAction SilentlyContinue
$Probe = Start-Process -FilePath (Join-Path $Dist 'Dropshift.exe') -ArgumentList '--self-test', "`"$Answer`"" -PassThru
if (-not $Probe.WaitForExit(120000)) { $Probe.Kill(); Fail 'the built Dropshift.exe hung when started (--self-test)' }
$Said = if (Test-Path $Answer) { (Get-Content -Raw $Answer).Trim() } else { '' }
Remove-Item $Answer -ErrorAction SilentlyContinue
if (-not $Said) { Fail 'the built Dropshift.exe does not start (--self-test gave no answer)' }
Write-Host ">> it starts: Dropshift $Said"

$size = (Get-ChildItem $Dist -Recurse -File | Measure-Object Length -Sum).Sum
Write-Host ('>> done: {0}  ({1:N0} MB)' -f (Join-Path $Dist 'Dropshift.exe'), ($size / 1MB))
