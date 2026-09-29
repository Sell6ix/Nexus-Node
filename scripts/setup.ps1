# First-run: venv, deps, web UI, .env, encryption key.
param(
    [switch]$Quiet
)
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "Creating Python venv…"
    python -m venv (Join-Path $Root ".venv")
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
}
Write-Host "Installing Python packages…"
& $Python -m pip install -q -r (Join-Path $Root "requirements.txt")

$Dist = Join-Path $Root "web\dist"
if (-not (Test-Path $Dist) -or $env:NEXUS_REBUILD_UI -eq "1") {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Error "npm is required to build the web UI."
    }
    Write-Host "Building web UI…"
    Push-Location (Join-Path $Root "web")
    npm install --no-fund --no-audit
    npm run build
    Pop-Location
}

$EnvFile = Join-Path $Root ".env"
$Example = Join-Path $Root ".env.example"
if (-not (Test-Path $EnvFile) -and (Test-Path $Example)) {
    Copy-Item $Example $EnvFile
    Write-Host "Wrote .env from .env.example — edit DEVICE_NAME if you want."
}

& $Python -c "from pathlib import Path; from core.secrets import default_box; b=default_box(Path('data')); print('Secrets key ready (%s)' % b.key_path)"

if (-not $Quiet) {
    Write-Host ""
    Write-Host "Setup complete."
    Write-Host "  Start now:     .\scripts\start.ps1"
    Write-Host "  Autostart:     .\scripts\install-autostart-windows.ps1"
    Write-Host "  Then open the URL printed by the hub and create the owner account."
}
