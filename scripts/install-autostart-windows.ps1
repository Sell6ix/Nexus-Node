# Register a logon Scheduled Task that runs Nexus Node in Telegram mode.
# Run from PowerShell in the repo root (or any cwd; script locates itself).

$ErrorActionPreference = "Stop"
$Root = Resolve-Path (Join-Path $PSScriptRoot "..")
$Python = Join-Path $Root ".venv\Scripts\python.exe"
$Main = Join-Path $Root "main.py"
$TaskName = "NexusNodeTelegram"

if (-not (Test-Path $Python)) {
    Write-Error "Missing venv python at $Python. Create .venv and pip install -r requirements.txt first."
}

$Action = New-ScheduledTaskAction -Execute $Python -Argument "`"$Main`" --mode telegram" -WorkingDirectory "$Root"
$Trigger = New-ScheduledTaskTrigger -AtLogOn
$Settings = New-ScheduledTaskSettingsSet -RestartCount 3 -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries

Register-ScheduledTask -TaskName $TaskName -Action $Action -Trigger $Trigger -Settings $Settings -Force | Out-Null
Start-ScheduledTask -TaskName $TaskName

Write-Host "Scheduled task '$TaskName' registered and started."
Write-Host "WorkingDirectory: $Root"
Write-Host "Check Task Scheduler for output / errors. Pair code is in the console window if you run interactively,"
Write-Host "or inspect data\pair_code.txt after the first start."
