# Start the Nexus Node web hub (builds the UI once if needed).
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root

$Python = Join-Path $Root ".venv\Scripts\python.exe"
if (-not (Test-Path $Python)) {
    Write-Host "Creating venv…"
    python -m venv (Join-Path $Root ".venv")
    $Python = Join-Path $Root ".venv\Scripts\python.exe"
    & $Python -m pip install -r (Join-Path $Root "requirements.txt")
}

$Dist = Join-Path $Root "web\dist"
if (-not (Test-Path $Dist)) {
    if (-not (Get-Command npm -ErrorAction SilentlyContinue)) {
        Write-Error "npm is required to build the web UI (web/dist is missing)."
    }
    Write-Host "Building web UI…"
    Push-Location (Join-Path $Root "web")
    npm install
    npm run build
    Pop-Location
}

$HostBind = if ($env:NEXUS_WEB_HOST) { $env:NEXUS_WEB_HOST } else { "0.0.0.0" }
$Port = if ($env:NEXUS_WEB_PORT) { $env:NEXUS_WEB_PORT } else { "8743" }
& $Python (Join-Path $Root "main.py") --mode web --host $HostBind --port $Port
