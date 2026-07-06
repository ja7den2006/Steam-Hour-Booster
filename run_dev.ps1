param(
  [switch]$SkipInstall
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $repoRoot

if (-not (Get-Command python -ErrorAction SilentlyContinue)) {
  throw "Python 3.8 or newer is required on PATH."
}

if (-not $SkipInstall) {
  Write-Host "Installing editable desktop dependencies..."
  python -m pip install -e '.[dev]'
}

Write-Host "Launching Steam Hour Booster..."
python -m steam_hour_booster
