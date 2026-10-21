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

$busy = Get-NetTCPConnection -LocalPort ([int]$port) -State Listen -ErrorAction SilentlyContinue
if ($busy) {
    $owner = ($busy | Select-Object -First 1).OwningProcess
    $proc = Get-Process -Id $owner -ErrorAction SilentlyContinue
    Write-Error "Port $port is already in use by PID $owner ($($proc.ProcessName)). Stop the other Agent Zero instance or set A0_PORT."
    exit 1
}

$python = Join-Path $PSScriptRoot ".venv\Scripts\python.exe"
if (-not (Test-Path $python)) {
    Write-Error "Missing .venv. Run: powershell -ExecutionPolicy Bypass -File .\install-baremetal.ps1"
    exit 1
}

# Data directory: env > saved .a0-data > ~/agent-zero/<instance>/usr (launcher layout).
$dataConf = Join-Path $PSScriptRoot ".a0-data"
if (-not $env:A0_DATA_DIR -and (Test-Path $dataConf)) {
    $saved = (Get-Content $dataConf -TotalCount 1 -ErrorAction SilentlyContinue)
    if ($saved) { $saved = $saved.Trim() }
    if ($saved -and $saved -ne "local") { $env:A0_DATA_DIR = $saved }
}
if (-not $env:A0_DATA_DIR -and -not (Test-Path $dataConf)) {
    $usrPath = Join-Path $PSScriptRoot "usr"
    $hasData = $false
    if (Test-Path $usrPath) {
        $hasData = [bool](Get-ChildItem -LiteralPath $usrPath -Recurse -File -Force -ErrorAction SilentlyContinue |
            Where-Object { $_.Name -ne ".gitkeep" } | Select-Object -First 1)
    }
    $instance = if ($env:A0_INSTANCE) { $env:A0_INSTANCE } else { "agent-zero" }
    $fallback = Join-Path $env:USERPROFILE "agent-zero\$instance\usr"
    if (-not $hasData) {
        $answer = ""
        if ($Host.Name -eq "Console" -and -not $env:A0_NO_ONBOARDING) {
            $answer = Read-Host "Agent Zero data directory [$fallback] (enter 'local' to keep $usrPath)"
        }
        if (-not $answer) { $env:A0_DATA_DIR = $fallback }
        elseif ($answer -notin @("local", "./usr", "usr")) { $env:A0_DATA_DIR = $answer }
        if ($env:A0_DATA_DIR) { Set-Content -Path $dataConf -Value $env:A0_DATA_DIR } else { Set-Content -Path $dataConf -Value "local" }
    }
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
