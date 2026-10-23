# Ensure the bare-metal Agent Zero server is listening.
# Intended for a scheduled task (every few minutes): starts the server only
# when the port is free, so a manual stop is not fought with while it runs.
$ErrorActionPreference = "SilentlyContinue"
$root = Split-Path -Parent $PSScriptRoot
$port = if ($env:A0_PORT) { [int]$env:A0_PORT } else { 5000 }

$busy = Get-NetTCPConnection -LocalPort $port -State Listen
if ($busy) { exit 0 }

$logDir = Join-Path $root "tmp"
New-Item -ItemType Directory -Force -Path $logDir | Out-Null

Start-Process -FilePath "powershell.exe" `
    -ArgumentList "-NoProfile", "-ExecutionPolicy", "Bypass", "-File", (Join-Path $root "run-baremetal.ps1") `
    -WorkingDirectory $root -WindowStyle Hidden `
    -RedirectStandardOutput (Join-Path $logDir "a0-stdout.log") `
    -RedirectStandardError (Join-Path $logDir "a0-stderr.log") | Out-Null
