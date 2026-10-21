# Agent Zero bare metal launcher (Windows PowerShell).
# Start:  .\run-baremetal.ps1
# Env:    A0_HOST, A0_PORT, A0_DATA_DIR, SEARXNG_URL, ALLOWED_ORIGINS
#requires -Version 5.1
$ErrorActionPreference = "Stop"
Set-Location -LiteralPath $PSScriptRoot

$port = if ($env:A0_PORT) { $env:A0_PORT } else { "5000" }
$hostAddr = if ($env:A0_HOST) { $env:A0_HOST } else { "127.0.0.1" }
$env:A0_LOCAL_RUNTIME = if ($env:A0_LOCAL_RUNTIME) { $env:A0_LOCAL_RUNTIME } else { "true" }
$env:PATH = "$PSScriptRoot\.venv\Scripts;$env:PATH"
$env:PLAYWRIGHT_BROWSERS_PATH = if ($env:PLAYWRIGHT_BROWSERS_PATH) { $env:PLAYWRIGHT_BROWSERS_PATH } else { "$PSScriptRoot\tmp\playwright" }

# Local gateways must bypass any proxy.
$loopbackNoProxy = "localhost,127.0.0.1,::1,host.docker.internal"
if ($env:NO_PROXY) {
    if ($env:NO_PROXY -notmatch "127\.0\.0\.1") { $env:NO_PROXY = "$env:NO_PROXY,$loopbackNoProxy" }
} else {
    $env:NO_PROXY = $loopbackNoProxy
}
$env:no_proxy = $env:NO_PROXY

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Missing .venv. Run: powershell -ExecutionPolicy Bypass -File .\install-baremetal.ps1"
    exit 1
}

# Optional external data directory via an NTFS junction.
if ($env:A0_DATA_DIR) {
    $usr = Join-Path $PSScriptRoot "usr"
    if (-not (Test-Path $usr)) {
        cmd /c mklink /J "$usr" "$env:A0_DATA_DIR" | Out-Null
    } elseif ((Get-Item $usr -Force).LinkType -ne "Junction") {
        $entries = @(Get-ChildItem -Force $usr -ErrorAction SilentlyContinue)
        if ($entries.Count -gt 0) {
            Write-Warning "A0_DATA_DIR ignored: $usr already contains data. Use install-baremetal.ps1 -DataDir to migrate."
        }
    }
}

$dirs = "agents", "plugins", "skills", "knowledge", "workdir", "uploads", "chats", "scheduler", "memory", "projects"
foreach ($dir in $dirs) {
    New-Item -ItemType Directory -Force -Path (Join-Path $PSScriptRoot "usr\$dir") | Out-Null
}

if (-not $env:ALLOWED_ORIGINS) {
    $origins = @("http://localhost:$port", "http://127.0.0.1:$port")
    if ($hostAddr -in @("0.0.0.0", "::")) {
        $hostName = $env:COMPUTERNAME
        if ($hostName) { $origins += "http://${hostName}:$port" }
        try {
            $ip = (Get-NetIPAddress -AddressFamily IPv4 -ErrorAction SilentlyContinue |
                Where-Object { $_.IPAddress -notlike "127.*" -and $_.IPAddress -notlike "169.254.*" } |
                Select-Object -First 1 -ExpandProperty IPAddress)
            if ($ip) { $origins += "http://${ip}:$port" }
        } catch { }
    } else {
        $origins += "http://${hostAddr}:$port"
    }
    $env:ALLOWED_ORIGINS = ($origins | Select-Object -Unique) -join ","
}

& $python run_ui.py "--host=$hostAddr" "--port=$port" @args
