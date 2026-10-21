# Agent Zero bare-metal installer (Windows PowerShell).
#
#   powershell -ExecutionPolicy Bypass -File .\install-baremetal.ps1
#   powershell -ExecutionPolicy Bypass -File .\install-baremetal.ps1 -WithOffice
#   powershell -ExecutionPolicy Bypass -File .\install-baremetal.ps1 -HostAddr 0.0.0.0 -Port 5080 -AuthLogin admin -AuthPassword "***"
#   powershell -ExecutionPolicy Bypass -File .\install-baremetal.ps1 -Instance my-a0 -DataDir D:\a0-data
#
# Docker, A0 Launcher image flows and the A0 Install CLI stay unchanged; this is
# an additional host runtime. A bare-metal instance can be added to A0 Launcher
# as a remote instance and used by the A0 CLI over HTTP/WebSocket.
#requires -Version 5.1
[CmdletBinding()]
param(
    [string]$HostAddr = "127.0.0.1",
    [int]$Port = 5000,
    [string]$DataDir = "",
    [string]$Instance = $(if ($env:A0_INSTANCE) { $env:A0_INSTANCE } else { "agent-zero" }),
    [switch]$NonInteractive,
    [string]$AuthLogin = "",
    [string]$AuthPassword = "",
    [switch]$WithOffice,
    [switch]$WithDesktop,
    [switch]$NoSystemPackages,
    [switch]$PackagesOnly,
    [switch]$NoCli,
    [switch]$NoCompatLinks,
    [string]$Python = "3.12"
)

$ErrorActionPreference = "Stop"
$repo = $PSScriptRoot
Set-Location -LiteralPath $repo

function Write-Step([string]$message) { Write-Host "[INFO] $message" -ForegroundColor Cyan }
function Write-Ok([string]$message) { Write-Host "[ OK ] $message" -ForegroundColor Green }
function Write-Warn([string]$message) { Write-Host "[WARN] $message" -ForegroundColor Yellow }
function Write-Err([string]$message) { Write-Host "[FAIL] $message" -ForegroundColor Red }

function Add-EnvLine([string]$key, [string]$value) {
    $envFile = Join-Path $repo "usr\.env"
    if (-not (Test-Path $envFile)) { New-Item -ItemType File -Force -Path $envFile | Out-Null }
    $exists = Select-String -Path $envFile -Pattern "^$([regex]::Escape($key))=" -Quiet -ErrorAction SilentlyContinue
    if (-not $exists) { Add-Content -Path $envFile -Value "$key=$value" }
}

function Seed-Usr([string]$root) {
    $dirs = "agents", "plugins", "skills", "knowledge", "workdir", "uploads", "chats", "scheduler", "memory", "projects"
    New-Item -ItemType Directory -Force -Path $root | Out-Null
    foreach ($dir in $dirs) { New-Item -ItemType Directory -Force -Path (Join-Path $root $dir) | Out-Null }
    $envFile = Join-Path $root ".env"
    if (-not (Test-Path $envFile)) { New-Item -ItemType File -Force -Path $envFile | Out-Null }
}

if (-not $IsWindows -and $PSVersionTable.PSVersion.Major -ge 6) {
    Write-Err "This installer targets Windows. On macOS/Linux use install-baremetal.sh."
    exit 1
}

# ------------------------------------------------------------------ packages --
function Install-WingetPackage([string]$id) {
    if (-not (Get-Command winget -ErrorAction SilentlyContinue)) { return $false }
    Write-Step "winget install $id"
    & winget install --id $id -e --silent --accept-package-agreements --accept-source-agreements | Out-Null
    return ($LASTEXITCODE -eq 0)
}

if (-not $NoSystemPackages) {
    if (Get-Command winget -ErrorAction SilentlyContinue) {
        $core = @(
            "Gyan.FFmpeg",
            "UB-Mannheim.TesseractOCR",
            "BurntSushi.ripgrep.MSVC",
            "jqlang.jq",
            "OpenJS.NodeJS.LTS",
            "7zip.7zip"
        )
        foreach ($package in $core) {
            if (-not (Install-WingetPackage $package)) { Write-Warn "winget could not install $package; continuing." }
        }
        if ($WithOffice) {
            if (-not (Install-WingetPackage "TheDocumentFoundation.LibreOffice")) {
                Write-Warn "LibreOffice install failed; the Office plugin will report it as missing."
            }
        }
    } else {
        Write-Warn "winget not found; skipping system packages (install ffmpeg/tesseract/ripgrep/jq/node/7zip manually)."
    }
}
if ($WithDesktop) {
    Write-Warn "The Desktop plugin needs Xpra + XFCE (Linux/X11). It stays unavailable on Windows."
}
if ($PackagesOnly) {
    Write-Ok "System packages processed."
    exit 0
}

# ------------------------------------------------------------------ python ----
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) {
    $uvPath = Join-Path $env:USERPROFILE ".local\bin"
    if (Test-Path (Join-Path $uvPath "uv.exe")) {
        $env:PATH = "$uvPath;$env:PATH"
    } else {
        Write-Step "Installing uv..."
        Invoke-RestMethod https://astral.sh/uv/install.ps1 | Invoke-Expression
        $env:PATH = "$uvPath;$env:PATH"
    }
}
if (-not (Get-Command uv -ErrorAction SilentlyContinue)) { Write-Err "uv installation failed."; exit 1 }

Write-Step "Creating Python $Python environment..."
& uv venv .venv --python $Python --allow-existing
if ($LASTEXITCODE -ne 0) {
    if (Test-Path ".venv\Scripts\python.exe") { Write-Warn "Reusing the existing .venv." }
    else { Write-Err "Could not create the Python environment."; exit 1 }
}

Write-Step "Installing Python dependencies..."
$env:VIRTUAL_ENV = Join-Path $repo ".venv"
& uv pip install -r requirements.txt ipython pip setuptools wheel
if ($LASTEXITCODE -ne 0) { Write-Err "Dependency installation failed."; exit 1 }

# 7-Zip is not added to PATH by its installer; expose it for archive extraction.
$sevenZip = Join-Path $env:ProgramFiles "7-Zip"
if (Test-Path (Join-Path $sevenZip "7z.exe")) {
    if (-not ($env:PATH -split ";" | Where-Object { $_ -eq $sevenZip })) {
        $env:PATH = "$sevenZip;$env:PATH"
        try {
            $userPath = [Environment]::GetEnvironmentVariable("PATH", "User")
            if (-not ($userPath -split ";" | Where-Object { $_ -eq $sevenZip })) {
                [Environment]::SetEnvironmentVariable("PATH", "$sevenZip;$userPath", "User")
            }
        } catch { Write-Warn "Could not persist 7-Zip on the user PATH." }
    }
}

# ------------------------------------------------------------------ browser ---
Write-Step "Installing the Patchright Chromium build..."
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path $repo "tmp\playwright"
& ".venv\Scripts\patchright.exe" install chromium --no-shell
if ($LASTEXITCODE -ne 0) { Write-Warn "Chromium download failed; it retries on first Browser use." }

# -------------------------------------------------------------------- data ----
$usr = Join-Path $repo "usr"
$dataConf = Join-Path $repo ".a0-data"
if (-not $DataDir -and (Test-Path $dataConf)) {
    $saved = (Get-Content $dataConf -TotalCount 1 -ErrorAction SilentlyContinue)
    if ($saved) { $saved = $saved.Trim() }
    if ($saved -and $saved -ne "local") { $DataDir = $saved }
}
function Test-UsrData([string]$path) {
    if (-not (Test-Path $path)) { return $false }
    return [bool](Get-ChildItem -LiteralPath $path -Recurse -File -Force -ErrorAction SilentlyContinue |
        Where-Object { $_.Name -ne ".gitkeep" } | Select-Object -First 1)
}
if (-not $DataDir -and -not (Test-UsrData $usr)) {
    $fallback = Join-Path $env:USERPROFILE "agent-zero\$Instance\usr"
    if (-not $NonInteractive -and $Host.Name -eq "Console") {
        $answer = Read-Host "Agent Zero data directory [$fallback] (enter 'local' to keep $usr)"
        if (-not $answer) { $DataDir = $fallback }
        elseif ($answer -in @("local", "./usr", "usr")) { $DataDir = "" }
        else { $DataDir = $answer }
    } else {
        $DataDir = $fallback
    }
    if ($DataDir) { Set-Content -Path $dataConf -Value $DataDir } else { Set-Content -Path $dataConf -Value "local" }
}

if ($DataDir) {
    New-Item -ItemType Directory -Force -Path $DataDir | Out-Null
    if (Test-Path $usr) {
        $item = Get-Item $usr -Force
        if ($item.LinkType -eq "Junction") {
            cmd /c rmdir "$usr" | Out-Null
            cmd /c mklink /J "$usr" "$DataDir" | Out-Null
        } else {
            $entries = @(Get-ChildItem -Force $usr -ErrorAction SilentlyContinue)
            if ($entries.Count -gt 0) {
                Write-Step "Copying existing usr data into $DataDir (no overwrite)..."
                & robocopy $usr $DataDir /E /XN /XO /NFL /NDL /NJH /NJS /NP | Out-Null
                if ($LASTEXITCODE -ge 8) { Write-Err "Could not copy usr data into $DataDir. Nothing was deleted."; exit 1 }
                Remove-Item $usr -Recurse -Force
            } else {
                Remove-Item -LiteralPath $usr -Force
            }
            cmd /c mklink /J "$usr" "$DataDir" | Out-Null
        }
    } else {
        cmd /c mklink /J "$usr" "$DataDir" | Out-Null
    }
}
Seed-Usr $usr
Add-EnvLine "A0_LOCAL_RUNTIME" "true"
if ($AuthLogin) {
    Add-EnvLine "AUTH_LOGIN" $AuthLogin
    if ($AuthPassword) { Add-EnvLine "AUTH_PASSWORD" $AuthPassword }
} elseif ($HostAddr -notin @("127.0.0.1", "localhost")) {
    Write-Warn "Binding $HostAddr without authentication. Add -AuthLogin USER -AuthPassword PASS before exposing this instance."
}

# ---------------------------------------------------------- compat junctions --
if (-not $NoCompatLinks) {
    $isAdmin = ([Security.Principal.WindowsPrincipal][Security.Principal.WindowsIdentity]::GetCurrent()).IsInRole(
        [Security.Principal.WindowsBuiltInRole]::Administrator)
    $venv = Join-Path $repo ".venv"
    $links = @{
        "C:\a0"            = $repo
        "C:\exe"           = (Join-Path $repo "docker\run\fs\exe")
        "C:\opt\venv-a0"   = $venv
        "C:\opt\venv"      = $venv
    }
    if ($isAdmin) {
        New-Item -ItemType Directory -Force -Path "C:\opt" | Out-Null
        foreach ($link in $links.Keys) {
            if (Test-Path $link) {
                $existing = Get-Item $link -Force
                if ($existing.LinkType -eq "Junction") { continue }
                Write-Warn "$link already exists; leaving it."
                continue
            }
            cmd /c mklink /J "$link" "$($links[$link])" | Out-Null
            if ($LASTEXITCODE -eq 0) { Write-Ok "Linked $link" } else { Write-Warn "Could not create $link" }
        }
        $hosts = Join-Path $env:SystemRoot "System32\drivers\etc\hosts"
        if (-not (Select-String -Path $hosts -Pattern "host\.docker\.internal" -Quiet -ErrorAction SilentlyContinue)) {
            Add-Content -Path $hosts -Value "127.0.0.1 host.docker.internal"
            Write-Ok "Added host.docker.internal to the hosts file."
        }
    } else {
        Write-Warn "Container-path junctions need an elevated PowerShell; run this script as Administrator to add them."
    }
}

# ------------------------------------------------------------------ A0 CLI ----
if (-not $NoCli) {
    $cliOk = $false
    if (Get-Command a0 -ErrorAction SilentlyContinue) {
        & a0 --version *> $null
        $cliOk = ($LASTEXITCODE -eq 0)
    }
    if (-not $cliOk) {
        Write-Step "Installing the A0 CLI (a0-connector)..."
        try {
            Invoke-RestMethod https://raw.githubusercontent.com/agent0ai/a0-connector/main/install.ps1 | Invoke-Expression
            $cliOk = $true
        } catch { Write-Warn "A0 CLI installation failed; install it later from a0-connector." }
    }
    if ($cliOk) { Write-Ok "A0 CLI available." }
}

# ------------------------------------------------------------------ summary ----
$url = "http://${HostAddr}:$Port"
Write-Ok "Agent Zero bare metal is installed."
Write-Host ""
Write-Host "  Start:        powershell -ExecutionPolicy Bypass -File .\run-baremetal.ps1"
Write-Host "  Web UI:       $url"
Write-Host "  Launcher:     Instances -> Add remote instance -> $url"
Write-Host "  A0 CLI:       a0 headless --host $url -p `"hello`""
Write-Host "  Docker mode:  unchanged (docker/, DockerfileLocal, launcher-managed images)"
Write-Host "  Catalog:      C:\a0, C:\exe, C:\opt\venv[-a0] junctions (Administrator) + hosts entry"
Write-Host ""
