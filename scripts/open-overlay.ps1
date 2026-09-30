# Always-on-top PC stats HUD (hub must be running).
param(
  [string]$Corner = "top-right",
  [string]$Url = $(if ($env:NEXUS_PUBLIC_URL) { $env:NEXUS_PUBLIC_URL } else { "http://127.0.0.1:8743" })
)
$Root = Split-Path -Parent $PSScriptRoot
Set-Location $Root
python -m core.overlay_window --url $Url --corner $Corner
