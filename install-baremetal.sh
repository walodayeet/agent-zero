#!/usr/bin/env bash
# Agent Zero bare-metal installer (no Docker).
#
# Installs the Python runtime, host packages, browser engine, optional A0 CLI,
# and a systemd user service for this checkout.
#
#   bash install-baremetal.sh                     # core install on :5000
#   bash install-baremetal.sh --with-office --with-desktop --with-archives
#   bash install-baremetal.sh --host 0.0.0.0 --port 5080 --systemd
#   bash install-baremetal.sh --no-system-packages --no-cli
#
# Docker users are unaffected: this script never touches docker/, the image or
# the launcher-managed containers. A bare-metal instance can be added to A0
# Launcher as a remote instance (URL below) and used by the A0 CLI via
# `a0 headless --host <url>`.
set -euo pipefail

REPO_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$REPO_DIR"

HOST="127.0.0.1"
PORT="5000"
DATA_DIR=""
INSTALL_PACKAGES=1
WITH_OFFICE=0
WITH_DESKTOP=0
WITH_ARCHIVES=1
INSTALL_CLI=1
WITH_SYSTEMD=0
AUTH_LOGIN=""
AUTH_PASSWORD=""
PACKAGES_ONLY=0
COMPAT_LINKS=1
COMPAT_ONLY=0
PYTHON_SPEC="3.12"

RED='\033[0;31m'; GREEN='\033[0;32m'; YELLOW='\033[1;33m'; BLUE='\033[0;34m'; NC='\033[0m'
ok()   { printf "${GREEN}✔${NC} %s\n" "$1"; }
info() { printf "${BLUE}[INFO]${NC} %s\n" "$1"; }
warn() { printf "${YELLOW}[WARN]${NC} %s\n" "$1"; }
die()  { printf "${RED}[ERROR]${NC} %s\n" "$1" >&2; exit 1; }

usage() {
  cat <<'EOF'
Agent Zero bare-metal installer

Usage: bash install-baremetal.sh [options]

Options:
  --host HOST            Bind address (default 127.0.0.1; 0.0.0.0 for LAN/remote).
  --port PORT            Web UI port (default 5000).
  --auth-login USER      Enable Web UI basic auth (needed for remote access).
  --auth-password PASS   Password for --auth-login.
  --data-dir PATH        User data directory (default <repo>/usr).
  --with-office          Install LibreOffice for the Office plugin.
  --with-desktop         Install Xpra + XFCE for the Desktop plugin.
  --no-archives          Skip 7z support for rar/7z/gz archives.
  --no-system-packages   Skip apt/pacman packages (Python/browser only).
  --packages-only        Install system packages, then exit (run with sudo).
  --compat-links-only    Create container-path compatibility links, then exit (run with sudo).
  --no-compat-links      Skip /a0, /opt/venv-a0, /exe links and the hosts entry.
  --no-cli               Skip installing the A0 CLI (`a0`).
  --systemd              Install and enable a systemd --user service.
  --python SPEC          Python version for the venv (default 3.12).
  -h, --help             Show this help.
EOF
}

while [ $# -gt 0 ]; do
  case "$1" in
    --host) HOST="${2:?--host needs a value}"; shift 2 ;;
    --host=*) HOST="${1#*=}"; shift ;;
    --port) PORT="${2:?--port needs a value}"; shift 2 ;;
    --port=*) PORT="${1#*=}"; shift ;;
    --data-dir) DATA_DIR="${2:?--data-dir needs a value}"; shift 2 ;;
    --data-dir=*) DATA_DIR="${1#*=}"; shift ;;
    --auth-login) AUTH_LOGIN="${2:?--auth-login needs a value}"; shift 2 ;;
    --auth-login=*) AUTH_LOGIN="${1#*=}"; shift ;;
    --auth-password) AUTH_PASSWORD="${2:?--auth-password needs a value}"; shift 2 ;;
    --auth-password=*) AUTH_PASSWORD="${1#*=}"; shift ;;
    --with-office) WITH_OFFICE=1; shift ;;
    --with-desktop) WITH_DESKTOP=1; shift ;;
    --no-archives) WITH_ARCHIVES=0; shift ;;
    --no-system-packages) INSTALL_PACKAGES=0; shift ;;
    --no-cli) INSTALL_CLI=0; shift ;;
    --systemd) WITH_SYSTEMD=1; shift ;;
    --packages-only) PACKAGES_ONLY=1; shift ;;
    --compat-links-only) COMPAT_ONLY=1; shift ;;
    --no-compat-links) COMPAT_LINKS=0; shift ;;
    --python) PYTHON_SPEC="${2:?--python needs a value}"; shift 2 ;;
    --python=*) PYTHON_SPEC="${1#*=}"; shift ;;
    -h|--help) usage; exit 0 ;;
    *) die "Unknown option: $1 (see --help)" ;;
  esac
done

command -v python3 >/dev/null 2>&1 || die "python3 is required."

# ---------------------------------------------------------------- packages --
APT_CORE=(
  python3 python3-venv python3-pip curl git ca-certificates
  ffmpeg tesseract-ocr ripgrep jq nodejs npm fonts-dejavu fonts-liberation fonts-noto-color-emoji
  libnss3 libnspr4 libatk1.0-0t64 libatk-bridge2.0-0t64 libatspi2.0-0t64
  libcups2t64 libxcomposite1 libxdamage1 libxkbcommon0 libpango-1.0-0
  libcairo2 libgbm1 libasound2t64 xclip wl-clipboard
)
APT_OFFICE=(libreoffice python3-uno fonts-crosextra-carlito fonts-crosextra-caladea)
APT_ARCHIVES=(p7zip-full)
APT_DESKTOP=(
  xpra xpra-html5 xpra-x11 xvfb xfce4-session xfce4-panel xfwm4
  xfce4-settings thunar xfce4-terminal dbus-x11 xdotool x11-apps x11-utils x11-xserver-utils
  xauth xclip x11vnc novnc
)
PACMAN_CORE=(python python-pip base-devel curl git ffmpeg tesseract ripgrep jq nodejs npm
  ttf-dejavu ttf-liberation noto-fonts-emoji nss nspr atk at-spi2-atk at-spi2-core
  cups libxcomposite libxdamage libxkbcommon pango cairo mesa alsa-lib xclip wl-clipboard)
PACMAN_OFFICE=(libreoffice-fresh)
PACMAN_ARCHIVES=(7zip)
PACMAN_DESKTOP=(xpra xorg-server-xvfb xfce4-session xfce4-panel xfwm4
  xfce4-settings thunar xfce4-terminal xdotool xorg-xrandr xorg-xauth dbus)

as_root() {
  if [ "$(id -u)" -eq 0 ]; then "$@"; else sudo "$@"; fi
}

apt_known() {
  local known=() name
  for name in "$@"; do
    if apt-cache show "$name" >/dev/null 2>&1; then
      known+=("$name")
    else
      warn "Package not available on this release, skipping: $name"
    fi
  done
  printf '%s\n' "${known[@]}"
}

apt_install_group() {
  local label="$1"; shift
  [ "$#" -eq 0 ] && return 0
  info "Installing $label..."
  if ! as_root apt-get install -y --no-install-recommends "$@"; then
    warn "$label packages failed to install; continuing."
  fi
}

install_system_packages() {
  if command -v apt-get >/dev/null 2>&1; then
    as_root apt-get update -y

    mapfile -t core < <(apt_known "${APT_CORE[@]}")
    apt_install_group "core packages" "${core[@]}"

    if [ "$WITH_OFFICE" -eq 1 ]; then
      mapfile -t office < <(apt_known "${APT_OFFICE[@]}")
      apt_install_group "office packages" "${office[@]}"
    fi
    if [ "$WITH_ARCHIVES" -eq 1 ]; then
      mapfile -t archives < <(apt_known "${APT_ARCHIVES[@]}")
      apt_install_group "archive packages" "${archives[@]}"
    fi

    if [ "$WITH_DESKTOP" -eq 1 ]; then
      local codename
      codename="$(. /etc/os-release 2>/dev/null && printf '%s' "${VERSION_CODENAME:-${UBUNTU_CODENAME:-}}")"
      [ -n "$codename" ] || codename="noble"
      info "Installing Xpra 6 from xpra.org (distro xpra is too old for the desktop plugin)..."
      as_root install -d /usr/share/keyrings
      if curl -fsSL https://xpra.org/xpra.asc | as_root tee /usr/share/keyrings/xpra.asc >/dev/null; then
        echo "deb [signed-by=/usr/share/keyrings/xpra.asc] https://xpra.org ${codename} main" \
          | as_root tee /etc/apt/sources.list.d/xpra.list >/dev/null
        if ! as_root apt-get update -y; then
          warn "xpra.org has no repository for '${codename}'; falling back to distro packages."
        fi
      else
        warn "Could not fetch the xpra.org key; falling back to distro packages."
      fi
      mapfile -t desktop < <(apt_known "${APT_DESKTOP[@]}")
      apt_install_group "desktop packages" "${desktop[@]}"
    fi
  elif command -v pacman >/dev/null 2>&1; then
    local pkgs=("${PACMAN_CORE[@]}")
    [ "$WITH_OFFICE" -eq 1 ] && pkgs+=("${PACMAN_OFFICE[@]}")
    [ "$WITH_ARCHIVES" -eq 1 ] && pkgs+=("${PACMAN_ARCHIVES[@]}")
    info "Installing packages with pacman..."
    as_root pacman -Syu --needed --noconfirm "${pkgs[@]}" || warn "Some core packages failed."
    if [ "$WITH_DESKTOP" -eq 1 ]; then
      # xpra is AUR-only on Arch; try packages individually so one miss does not abort.
      local pkg
      for pkg in "${PACMAN_DESKTOP[@]}"; do
        as_root pacman -S --needed --noconfirm "$pkg" || warn "pacman: $pkg unavailable (desktop plugin will report it)."
      done
    fi
  else
    warn "No apt-get or pacman found; skipping system packages."
  fi
}

install_compat_links() {
  link_if_free() {
    local link="$1" target="$2"
    if [ -L "$link" ]; then
      if [ "$(readlink -f "$link" 2>/dev/null)" = "$(readlink -f "$target" 2>/dev/null)" ]; then
        return 0
      fi
      warn "$link already points elsewhere; leaving it."
      return 0
    fi
    if [ -e "$link" ]; then
      warn "$link already exists; leaving it."
      return 0
    fi
    if as_root ln -s "$target" "$link"; then
      ok "Linked $link -> $target"
    else
      warn "Could not create $link (run with sudo for catalog-plugin compatibility)."
    fi
  }

  # Container path literals used by many catalog plugins.
  link_if_free /a0 "$REPO_DIR"
  link_if_free /exe "$REPO_DIR/docker/run/fs/exe"
  link_if_free /opt/venv-a0 "$REPO_DIR/.venv"
  link_if_free /opt/venv "$REPO_DIR/.venv"

  # Container -> host gateway name used by gateway-backed plugins.
  if ! grep -q "host.docker.internal" /etc/hosts 2>/dev/null; then
    if echo "127.0.0.1 host.docker.internal" | as_root tee -a /etc/hosts >/dev/null; then
      ok "Added host.docker.internal -> 127.0.0.1 to /etc/hosts"
    else
      warn "Could not add host.docker.internal to /etc/hosts (needs root)."
    fi
  fi
}

if [ "$COMPAT_LINKS" -eq 1 ]; then
  install_compat_links
  if [ "$COMPAT_ONLY" -eq 1 ]; then
    exit 0
  fi
fi

if [ "$INSTALL_PACKAGES" -eq 1 ]; then
  install_system_packages
  if [ "$PACKAGES_ONLY" -eq 1 ]; then
    ok "System packages installed."
    exit 0
  fi
else
  warn "Skipping system packages (--no-system-packages)."
fi

# ------------------------------------------------------------------ python --
if ! command -v uv >/dev/null 2>&1; then
  info "Installing uv..."
  curl -LsSf https://astral.sh/uv/install.sh | sh
  export PATH="$HOME/.local/bin:$PATH"
fi
command -v uv >/dev/null 2>&1 || die "uv installation failed."

info "Creating Python $PYTHON_SPEC environment..."
uv venv .venv --python "$PYTHON_SPEC" || uv venv .venv
info "Installing Python dependencies..."
VIRTUAL_ENV="$REPO_DIR/.venv" uv pip install -r requirements.txt ipython pip setuptools wheel

# ---------------------------------------------------------------- browser --
info "Installing the Patchright Chromium build..."
PLAYWRIGHT_BROWSERS_PATH="$REPO_DIR/tmp/playwright" .venv/bin/patchright install chromium --no-shell || \
  warn "Chromium download failed; it will retry on first Browser use."
if [ "$INSTALL_PACKAGES" -eq 1 ]; then
  .venv/bin/patchright install-deps chromium >/dev/null 2>&1 || \
    warn "Browser system libraries were not fully installed; install them manually if Browser fails."
fi

# -------------------------------------------------------------------- data --
seed_usr() {
  local root="$1"
  mkdir -p "$root" \
    "$root/agents" "$root/plugins" "$root/skills" "$root/knowledge" \
    "$root/workdir" "$root/uploads" "$root/chats" "$root/scheduler" \
    "$root/memory" "$root/projects"
  [ -f "$root/.env" ] || : > "$root/.env"
  # Some hardening-minded plugins refuse group/world-writable runtime paths.
  chmod go-w "$root" "$root"/* 2>/dev/null || true
}

if [ -n "$DATA_DIR" ]; then
  mkdir -p "$DATA_DIR"
  if [ -e usr ] && [ ! -L usr ]; then
    # move the checkout's data (skeleton or real) into the requested location;
    # never delete the source unless the copy succeeded
    if ! python3 - "$REPO_DIR/usr" "$DATA_DIR" <<'PYMOVE'
import os, shutil, sys
src, dst = sys.argv[1], sys.argv[2]
for root, _dirs, files in os.walk(src):
    rel = os.path.relpath(root, src)
    target = dst if rel == "." else os.path.join(dst, rel)
    os.makedirs(target, exist_ok=True)
    for name in files:
        source = os.path.join(root, name)
        destination = os.path.join(target, name)
        if not os.path.exists(destination):
            shutil.copy2(source, destination)
PYMOVE
    then
      die "Could not copy existing usr data into $DATA_DIR. Nothing was deleted."
    fi
    rm -rf usr
  fi
  ln -sfn "$DATA_DIR" usr
fi
seed_usr usr
chmod go-w "$REPO_DIR" 2>/dev/null || true

add_env() {
  local key="$1" value="$2"
  grep -q "^${key}=" usr/.env 2>/dev/null || printf '%s=%s\n' "$key" "$value" >> usr/.env
}
add_env A0_LOCAL_RUNTIME true
if [ -n "$AUTH_LOGIN" ]; then
  add_env AUTH_LOGIN "$AUTH_LOGIN"
  if [ -n "$AUTH_PASSWORD" ]; then
    add_env AUTH_PASSWORD "$AUTH_PASSWORD"
  fi
fi
if [ -z "$AUTH_LOGIN" ] && [ "$HOST" != "127.0.0.1" ] && [ "$HOST" != "localhost" ]; then
  warn "Binding $HOST without authentication. Add --auth-login USER --auth-password PASS"
  warn "before exposing this instance to a LAN, Tailscale or the internet."
fi

# ----------------------------------------------------------------- systemd --
if [ "$WITH_SYSTEMD" -eq 1 ]; then
  unit_dir="$HOME/.config/systemd/user"
  mkdir -p "$unit_dir"
  cat > "$unit_dir/agent-zero.service" <<EOF
[Unit]
Description=Agent Zero (bare metal)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$REPO_DIR
Environment=A0_HOST=$HOST
Environment=A0_PORT=$PORT
Environment=A0_DATA_DIR=$DATA_DIR
Environment=SEARXNG_URL=\${SEARXNG_URL:-}
ExecStart=$REPO_DIR/run-baremetal.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
  systemctl --user daemon-reload
  systemctl --user enable --now agent-zero.service
  ok "systemd user service agent-zero.service enabled (journalctl --user -u agent-zero -f)."
fi

# ---------------------------------------------------------------- A0 CLI ----
if [ "$INSTALL_CLI" -eq 1 ]; then
  if command -v a0 >/dev/null 2>&1; then
    ok "A0 CLI already installed: $(command -v a0)"
  else
    info "Installing the A0 CLI (a0-connector)..."
    if curl -LsSf https://raw.githubusercontent.com/agent0ai/a0-connector/main/install.sh | sh; then
      ok "A0 CLI installed."
    else
      warn "A0 CLI installation failed; install it later from a0-connector."
    fi
  fi
fi

# ------------------------------------------------------------------ summary --
url="http://${HOST}:${PORT}"
if [ "$HOST" = "127.0.0.1" ] || [ "$HOST" = "localhost" ]; then
  display_url="http://127.0.0.1:${PORT}"
else
  display_url="http://${HOST}:${PORT}"
fi

ok "Agent Zero bare metal is installed."
cat <<EOF

  Start:        $REPO_DIR/run-baremetal.sh
  Web UI:       $display_url
  First run:    the UI downloads model presets and the embedding model

  A0 Launcher:  Instances -> Add remote instance -> $display_url
  A0 CLI:       a0 headless --host $display_url -p "hello"
  Docker mode:  unchanged (docker/, DockerfileLocal, launcher-managed images)
  Catalog:      /a0, /exe, /opt/venv-a0 links + host.docker.internal entry

  Optional extras:
    Office plugin   --with-office   (LibreOffice)
    Desktop plugin  --with-desktop  (Xpra + XFCE)
    Archive formats --no-archives   to skip 7z support

EOF
