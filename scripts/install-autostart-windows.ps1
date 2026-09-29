# Register a logon Scheduled Task that runs the Nexus Node web hub.
# Run from PowerShell:  .\scripts\install-autostart-windows.ps1

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Main = Join-Path $Root "main.py"
$TaskName = "NexusNodeWeb"

if (-not (Test-Path $Python)) {
    Write-Error "Missing venv python at $Python. Create .venv and pip install -r requirements.txt first."
}

$Dist = Join-Path $Root "web\dist"
if (-not (Test-Path $Dist)) {
    Write-Error "Build the UI first:  cd web; npm install; npm run build"
}

$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Main`" --mode web --host 0.0.0.0 --port 8743" -WorkingDirectory "$Root"
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Scheduled task '$TaskName' registered and started."
Write-Host "WorkingDirectory: $Root"
Write-Host "Open http://<this-pc>:8743 on the phone (same Wi-Fi) or Tailscale 100.x away from home."
Write-Host "Uninstall: Unregister-ScheduledTask -TaskName $TaskName -Confirm:`$false"
