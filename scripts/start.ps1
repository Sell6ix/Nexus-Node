# Start the Nexus Node web hub (runs first-run setup if needed).
$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
Set-Location $Root
& (Join-Path $PSScriptRoot "setup.ps1") -Quiet
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$HostBind = if ($env:NEXUS_WEB_HOST) { $env:NEXUS_WEB_HOST } else { "0.0.0.0" }
$Port = if ($env:NEXUS_WEB_PORT) { $env:NEXUS_WEB_PORT } else { "8743" }
$extra = @()
if ($env:NEXUS_WEB_TLS -match '^(1|true|yes|on)$') { $extra += '--tls' }
& $Python (Join-Path $Root "main.py") --mode web --host $HostBind --port $Port @extra
