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

  if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
    throw "Node.js/npm is required for Steam client authorization."
  }
  Write-Host "Installing Steam client bridge dependencies..."
  npm install
} elseif (-not (Test-Path (Join-Path $repoRoot "node_modules\steam-user"))) {
  Write-Warning "Node bridge dependencies are missing. Run npm install before using Finish booster sign-in."
}

Write-Host "Launching Steam Hour Booster..."
python -m steam_hour_booster
