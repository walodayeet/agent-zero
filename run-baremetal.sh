#!/usr/bin/env bash
# Agent Zero on bare metal: Python server + host terminal (no Docker, no RFC).
#
# Works for direct CLI use and as the systemd ExecStart target. Environment:
#   A0_HOST       bind address (default 127.0.0.1; use 0.0.0.0 for LAN/remote access)
#   A0_PORT       web UI port (default 5000)
#   A0_DATA_DIR   user data directory (default ~/agent-zero/<instance>/usr)
#   A0_INSTANCE   instance name for the default path (default agent-zero)
#   A0_NO_ONBOARDING=1  skip the first-run data-directory prompt
#   A0_PRELOAD_WAIT      seconds startup may block on the embedding warm-up (default 0 here)
#   A0_PRELOAD=0         skip the embedding warm-up entirely (loads on first use)
#   SEARXNG_URL   SearXNG JSON endpoint (default http://127.0.0.1:8888/search when present)
#   ALLOWED_ORIGINS  extra CORS origins, appended to the derived local/host origins
set -euo pipefail
cd "$(dirname "$0")"

# Bare metal is auto-detected (non-dockerized, no RFC_PASSWORD); keep this explicit for clarity.
export A0_LOCAL_RUNTIME=${A0_LOCAL_RUNTIME:-true}
export PATH="$PWD/.venv/bin:$PATH"
export PLAYWRIGHT_BROWSERS_PATH="$PWD/tmp/playwright"
export A0_PORT="${A0_PORT:-5000}"
export A0_HOST="${A0_HOST:-127.0.0.1}"
# Fast start: never block HTTP on the embedding warm-up, and skip the warm-up
# itself unless A0_PRELOAD=1. The local embedding model then loads on first use
# (~6s once per process, ~1.7GB RSS saved while it is unused).
export A0_PRELOAD_WAIT="${A0_PRELOAD_WAIT:-0}"
export A0_PRELOAD="${A0_PRELOAD:-0}"
# Local embeddings through onnxruntime instead of torch (~-400 MB RSS, ~-9 s load;
# vectors are identical). Set A0_EMBEDDING_BACKEND=st to use sentence-transformers.
export A0_EMBEDDING_BACKEND="${A0_EMBEDDING_BACKEND:-onnx}"
# onnxruntime and faiss/sklearn each ship an OpenMP runtime; Intel's guard aborts
# the process on the duplicate. Standard workaround for this stack.
export KMP_DUPLICATE_LIB_OK="${KMP_DUPLICATE_LIB_OK:-TRUE}"

seed_usr() {
  mkdir -p "$1/agents" "$1/plugins" "$1/skills" "$1/knowledge" "$1/workdir" \
    "$1/uploads" "$1/chats" "$1/scheduler" "$1/memory" "$1/projects"
}

default_data_dir() {
  # Same layout the A0 Launcher / A0 Install use: ~/agent-zero/<instance>/usr
  printf '%s' "$HOME/agent-zero/${A0_INSTANCE:-agent-zero}/usr"
}

# True when usr/ holds real user data (not just the tracked skeleton).
usr_has_data() {
  [ -d usr ] || return 1
  [ -n "$(find usr -mindepth 1 -type f ! -name '.gitkeep' -print -quit 2>/dev/null)" ]
}

DATA_CONF="$PWD/.a0-data"
DATA_DIR_RESOLVED="${A0_DATA_DIR:-}"
if [ -z "$DATA_DIR_RESOLVED" ]; then
  if usr_has_data; then
    : # existing project-local data wins; migration is the installer's job
  elif [ -f "$DATA_CONF" ]; then
    saved="$(sed -n '1p' "$DATA_CONF" | sed 's/^[[:space:]]*//;s/[[:space:]]*$//')"
    case "$saved" in
      ""|local) : ;;
      *) DATA_DIR_RESOLVED="$saved" ;;
    esac
  else
    fallback="$(default_data_dir)"
    if [ -t 0 ] && [ -t 1 ] && [ "${A0_NO_ONBOARDING:-0}" != "1" ]; then
      printf 'Agent Zero data directory [%s] (enter "local" to keep %s): ' "$fallback" "$PWD/usr"
      read -r answer || answer=""
      case "$answer" in
        "") DATA_DIR_RESOLVED="$fallback" ;;
        local|./usr|usr) DATA_DIR_RESOLVED="" ;;
        *) DATA_DIR_RESOLVED="$answer" ;;
      esac
    else
      DATA_DIR_RESOLVED="$fallback"
    fi
    if [ -n "$DATA_DIR_RESOLVED" ]; then
      printf '%s\n' "$DATA_DIR_RESOLVED" > "$DATA_CONF"
    else
      printf 'local\n' > "$DATA_CONF"
    fi
  fi
fi

if [ -n "$DATA_DIR_RESOLVED" ]; then
  mkdir -p "$DATA_DIR_RESOLVED"
  if [ -L usr ]; then
    ln -sfn "$DATA_DIR_RESOLVED" usr
  elif usr_has_data; then
    echo "Data directory ignored: $PWD/usr already contains data." >&2
    echo "Use 'bash install-baremetal.sh --data-dir $DATA_DIR_RESOLVED' to migrate it." >&2
  else
    [ -d usr ] && rm -rf usr
    ln -sfn "$DATA_DIR_RESOLVED" usr
  fi
  export A0_DATA_DIR="$DATA_DIR_RESOLVED"
fi
if [ -L usr ]; then
  seed_usr "$(readlink usr)"
else
  seed_usr usr
fi

# Login is disabled unless AUTH_LOGIN/AUTH_PASSWORD are set, so the browser origin
# must be allowlisted or every API call answers 403 "Origin None not allowed".
if [ -z "${ALLOWED_ORIGINS:-}" ]; then
  origins="http://localhost:${A0_PORT},http://127.0.0.1:${A0_PORT}"
  case "$A0_HOST" in
    0.0.0.0|::|"[::]")
      host_name="$(hostname -f 2>/dev/null || hostname)"
      host_ip="$(hostname -I 2>/dev/null | awk '{print $1}')"
      if [ -z "$host_ip" ] && command -v ipconfig >/dev/null 2>&1; then
        host_ip="$(ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || true)"
      fi
      [ -n "$host_name" ] && origins="$origins,http://${host_name}:${A0_PORT}"
      [ -n "$host_ip" ] && origins="$origins,http://${host_ip}:${A0_PORT}"
      ;;
    *)
      origins="$origins,http://${A0_HOST}:${A0_PORT}"
      ;;
  esac
  export ALLOWED_ORIGINS="$origins"
fi

# Local gateways (cliproxyapi, SearXNG, Ollama, …) must bypass any proxy.
loopback_no_proxy="localhost,127.0.0.1,::1,host.docker.internal"
if [ -n "${NO_PROXY:-}" ]; then
  case ",$NO_PROXY," in
    *",127.0.0.1,"*) ;;
    *) export NO_PROXY="$NO_PROXY,$loopback_no_proxy" ;;
  esac
else
  export NO_PROXY="$loopback_no_proxy"
fi
export no_proxy="$NO_PROXY"

# Fail fast with an actionable message instead of a uvicorn Errno 98 trace.
port_busy() {
  if command -v ss >/dev/null 2>&1; then
    ss -ltn "sport = :${A0_PORT}" 2>/dev/null | grep -q LISTEN && return 0
    return 1
  fi
  python3 - "$A0_PORT" <<'PYCHECK' >/dev/null 2>&1
import socket, sys
s = socket.socket()
s.settimeout(0.5)
sys.exit(0 if s.connect_ex(("127.0.0.1", int(sys.argv[1]))) == 0 else 1)
PYCHECK
}
if port_busy; then
  echo "Port ${A0_PORT} is already in use:" >&2
  if command -v ss >/dev/null 2>&1; then
    ss -ltnp "sport = :${A0_PORT}" 2>/dev/null | sed -n '2p' >&2
  fi
  echo "Stop the other Agent Zero instance (pgrep -af run_ui.py) or start with A0_PORT=5080." >&2
  exit 1
fi

if [ -z "${SEARXNG_URL:-}" ]; then
  if curl -fsS -m 2 -o /dev/null -X POST http://127.0.0.1:8888/search -d 'q=ping&format=json' 2>/dev/null; then
    export SEARXNG_URL="http://127.0.0.1:8888/search"
  fi
fi

exec .venv/bin/python run_ui.py --host="$A0_HOST" --port="$A0_PORT" "$@"
