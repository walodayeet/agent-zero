#!/usr/bin/env bash
# Run Agent Zero in Docker with HOST networking by default.
#
# Host networking makes local gateways (cliproxyapi, Ollama, SearXNG) reachable
# at 127.0.0.1 from inside A0, the same as bare metal.
#
#   ./scripts/run-docker.sh                     # host network, port 80
#   A0_PORT=5080 ./scripts/run-docker.sh        # host network, port 5080
#   ./scripts/run-docker.sh --bridge            # isolated network + published port
#   ./scripts/run-docker.sh --build-local       # build this fork (DockerfileLocal)
#   A0_IMAGE=agent0ai/agent-zero:latest ./scripts/run-docker.sh
#
# Docker mode still means the image runs its own /a0 copy unless the repo is
# mounted; this script mounts the checkout so bare-metal patches are active.
set -euo pipefail
REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$REPO_DIR"

IMAGE="${A0_IMAGE:-agent0ai/agent-zero:latest}"
PORT="${A0_PORT:-80}"
MODE="host"
BUILD=0
NAME="${A0_CONTAINER_NAME:-agent-zero}"

while [ $# -gt 0 ]; do
  case "$1" in
    --bridge) MODE="bridge"; shift ;;
    --build-local) BUILD=1; shift ;;
    --image) IMAGE="${2:?--image needs a value}"; shift 2 ;;
    --image=*) IMAGE="${1#*=}"; shift ;;
    --port) PORT="${2:?--port needs a value}"; shift 2 ;;
    --port=*) PORT="${1#*=}"; shift ;;
    --name) NAME="${2:?--name needs a value}"; shift 2 ;;
    --name=*) NAME="${1#*=}"; shift ;;
    -h|--help) sed -n '2,18p' "$0"; exit 0 ;;
    *) echo "Unknown option: $1" >&2; exit 1 ;;
  esac
done

command -v docker >/dev/null 2>&1 || { echo "docker is required" >&2; exit 1; }

if [ "$BUILD" -eq 1 ]; then
  IMAGE="agent-zero-local:${A0_LOCAL_TAG:-latest}"
  echo "Building $IMAGE from DockerfileLocal..."
  docker build -f DockerfileLocal -t "$IMAGE" .
fi

docker rm -f "$NAME" >/dev/null 2>&1 || true

args=(
  --rm
  --name "$NAME"
  -e "A0_WEB_UI_PORT=$PORT"
  -v "$REPO_DIR:/a0"
  -v "$REPO_DIR/docker/run/fs/exe/self_update_manager.py:/exe/self_update_manager.py:ro"
  -v "$REPO_DIR/docker/run/fs/exe/run_tunnel_api.sh:/exe/run_tunnel_api.sh:ro"
)

if [ "$MODE" = "host" ]; then
  # Omit container sshd/SearXNG: host ports 22 and 8080/55510 are usually taken.
  host_bind="${A0_WEB_UI_HOST:-127.0.0.1}"
  args+=(
    --network host
    -e "A0_WEB_UI_HOST=$host_bind"
    -v "$REPO_DIR/docker/run/fs/etc/supervisor/conf.d/supervisord.host-network.conf:/etc/supervisor/conf.d/supervisord.conf:ro"
  )
  [ -n "${SEARXNG_URL:-}" ] && args+=(-e "SEARXNG_URL=$SEARXNG_URL")
else
  args+=(-p "${PORT}:${PORT}" --add-host "host.docker.internal:host-gateway")
fi

echo "Starting $IMAGE ($MODE network) on port $PORT..."
echo "Host gateways: http://127.0.0.1:<port>/v1 (not container localhost)."
exec docker run "${args[@]}" "$IMAGE"
