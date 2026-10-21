#!/usr/bin/env bash
# Agent Zero on bare metal: Python server + host terminal (no Docker, no RFC).
#
# Works for direct CLI use and as the systemd ExecStart target. Environment:
#   A0_HOST       bind address (default 127.0.0.1; use 0.0.0.0 for LAN/remote access)
#   A0_PORT       web UI port (default 5000)
#   A0_DATA_DIR   user data directory (default <repo>/usr)
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

seed_usr() {
  mkdir -p usr/agents usr/plugins usr/skills usr/knowledge usr/workdir \
    usr/uploads usr/chats usr/scheduler usr/memory usr/projects
}

if [ -n "${A0_DATA_DIR:-}" ]; then
  mkdir -p "$A0_DATA_DIR"
  if [ -L usr ]; then
    ln -sfn "$A0_DATA_DIR" usr
  elif [ -d usr ] && [ -n "$(ls -A usr 2>/dev/null)" ]; then
    # Never move live data at runtime; the installer handles one-time migration.
    echo "A0_DATA_DIR ignored: $PWD/usr already contains data." >&2
    echo "Use 'bash install-baremetal.sh --data-dir $A0_DATA_DIR' to migrate." >&2
  else
    [ -d usr ] && rmdir usr
    ln -sfn "$A0_DATA_DIR" usr
  fi
fi
seed_usr

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

if [ -z "${SEARXNG_URL:-}" ]; then
  if curl -fsS -m 2 -o /dev/null -X POST http://127.0.0.1:8888/search -d 'q=ping&format=json' 2>/dev/null; then
    export SEARXNG_URL="http://127.0.0.1:8888/search"
  fi
fi

exec .venv/bin/python run_ui.py --host="$A0_HOST" --port="$A0_PORT" "$@"
