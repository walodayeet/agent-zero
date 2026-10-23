# Ensure the bare-metal Agent Zero server is listening.
# Intended for a scheduled task (every few minutes): starts the server only
# when the port is free, so a manual stop is not fought with while it runs.
# The server itself runs under the AgentZeroServer task so Task Scheduler keeps
# its process tree alive after this watchdog action exits.
$ErrorActionPreference = "SilentlyContinue"
$port = if ($env:A0_PORT) { [int]$env:A0_PORT } else { 5000 }

$busy = Get-NetTCPConnection -LocalPort $port -State Listen
if ($busy) { exit 0 }

& schtasks /run /tn "AgentZeroServer"
