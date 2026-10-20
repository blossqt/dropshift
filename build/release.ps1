# Publishes a release of this suite app, which Hanami then installs and updates.
#
#   powershell -ExecutionPolicy Bypass -File build\release.ps1 --notes "What's new"
#   powershell -ExecutionPolicy Bypass -File build\release.ps1 --dry-run
#   powershell -ExecutionPolicy Bypass -File build\release.ps1 --make-key      (once per app)
#
# All the work is in release.py - see the top of it for each step. This only
# runs it with the app's build venv, which build\release.json names.

$App = (Get-Content -Raw (Join-Path $PSScriptRoot 'release.json') | ConvertFrom-Json).app
$Py = Join-Path $env:LOCALAPPDATA "$($App)Build\venv\Scripts\python.exe"
if (-not (Test-Path $Py)) {
    Write-Host 'ERROR: the build venv is missing - run build\bootstrap.ps1 first' -ForegroundColor Red
    exit 1
}
& $Py (Join-Path $PSScriptRoot 'release.py') @args
exit $LASTEXITCODE
