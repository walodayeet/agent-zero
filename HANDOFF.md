# Handoff: bare-metal Agent Zero

Paste this file to the next agent maintaining this host/checkout.

## What this checkout is
- Fork of `agent0ai/agent-zero` (v2.14 base) at `walodayeet/agent-zero`, branch `main`.
- Local checkout: this directory. Remotes: `origin` = upstream, `fork` = user fork.
- Two runtimes in one codebase:
  - **Docker/launcher/CLI-image mode** (`--dockerized=true`): unchanged upstream behavior.
  - **Bare metal** (`A0_LOCAL_RUNTIME=true`, or auto when non-dockerized with no `RFC_PASSWORD`): Python server + host shell, no RFC/SSH.
- Runtime switch: `helpers/runtime.py::is_local_runtime()`.
  Docker/launcher flows must stay untouched; never make local-only behavior unconditional.

## Platform matrix
- Linux (Debian/Ubuntu, Arch/CachyOS): `install-baremetal.sh` + `run-baremetal.sh`.
  apt/pacman, systemd optional. Arch uses per-package `pacman -S --needed` only.
- macOS: `install-baremetal.sh` uses Homebrew; `--systemd` is skipped; Desktop
  plugin (Xpra/XFCE) unavailable; LibreOffice is found in the app bundle.
- Windows 10/11: `install-baremetal.ps1` + `run-baremetal.ps1`; winget packages;
  junctions `C:\a0`, `C:\exe`, `C:\opt\venv[-a0]` when elevated. Desktop
  plugin unavailable. The PowerShell path is implemented but not yet smoke-tested
  on real Windows hardware; verify it first there.
- Core cross-platform bits: `/a0` mapping uses `os.path`; code execution uses
  `powershell.exe` + pywinpty on Windows; `find_soffice` checks app-bundle and
  Program Files paths.

## Starting and installing
```bash
./run-baremetal.sh                       # manual start (A0_HOST/A0_PORT/A0_DATA_DIR/SEARXNG_URL)
bash install-baremetal.sh --help         # full install (venv, browser, optional CLI/systemd)
sudo bash install-baremetal.sh --packages-only --with-office --with-desktop
sudo bash install-baremetal.sh --compat-links-only   # /a0, /exe, /opt/venv[-a0], hosts entry
```
Installer is idempotent and non-interactive: `uv venv --allow-existing`, per-package
installs, no full-system upgrade (`pacman -S --needed`, `apt-get install`).

## Architecture invariants (do not regress)
1. `/a0` is display syntax; on bare metal it maps to the checkout.
   - Core mapping: `helpers/files.py::_resolve_path`, `display_path()`.
   - Compatibility symlinks: `/a0`, `/exe`, `/opt/venv-a0`, `/opt/venv` (installer).
   - File browser: local root + `_map()`, directory listing via `os.scandir`
     (no `ls` parsing — this broke on hosts with non-default `ls` output and on
     Windows); download/zip/extract map `/a0` too.
2. Code execution: `ssh_enabled: auto` resolves to a local TTY off Docker;
   workdir is a real host path; node uses `docker/run/fs/exe/node_eval.js`.
3. Job loop must not self-pause on bare metal (scheduler + job extensions).
4. Search: `SEARXNG_URL` (image default `:55510`), DDG fallback; launcher
   auto-detects a host SearXNG at `127.0.0.1:8888`.
5. Office: never force `SAL_USE_VCLPLUGIN=gen`; each conversion gets an isolated
   LibreOffice profile. Override only via `A0_OFFICE_VCLPLUGIN`.
6. Prompt/skill paths: real host paths on bare metal (`files.display_path`).
7. Fresh installs need `usr/plugins` early; installer seeds `usr/`; runtime never
   moves a non-empty `usr` (data-dir migration is installer-only, copy-verified).
8. Permissions: checkout and `usr` are group/world-write-free (strict plugins
   such as `sam_mesh` reject writable ancestors).

## Catalog plugins
212 Plugin Hub plugins verified loadable (3 upstream repos are gone). Systemic
requirements: pip in the venv (uv venvs lack it), container-path shims,
`host.docker.internal` host entry, host packages (`ripgrep jq node/npm x11vnc
novnc ffmpeg tesseract libreoffice xpra …`). Remaining per-plugin needs are
environmental (Docker socket, credentials, external services, root installers).

## Networking
- Bare metal has no Docker network layer: model/browser/search calls are direct
  host sockets. Only `runtime.get_local_url()` mentions `host.docker.internal`
  (dockerized mode); the compatibility `/etc/hosts` entry maps that name to
  127.0.0.1 so plugin defaults work on the host too.
- `runtime.normalize_local_url()` rewrites `localhost` → `127.0.0.1` for model
  `api_base` values in bare-metal mode (IPv4-only local gateways). Docker mode is
  unchanged. `run-baremetal.sh`/`.ps1` append loopback hosts to `NO_PROXY`.
- Diagnose gateways with `scripts/check-local-gateway.sh URL KEY [MODEL]`.
- Docker-mode reminder: inside the container `localhost` is the container; use
  `host.docker.internal` (Linux: `--add-host=host.docker.internal:host-gateway`)
  or `--network host`.

## Verification checklist after any change
- `curl -s localhost:5000/api/health` → 200.
- Chat prompt runs `echo` via terminal; cwd is `<checkout>/usr/workdir`.
- Files panel lists `/a0`, upload/edit/rename/download/zip/7z extract work.
- Browser tool opens a page and screenshots.
- Office `docx → pdf` succeeds twice back-to-back.
- Desktop status `healthy`; a session starts and serves Xpra HTML5.
- Scheduler task gets `last_run`/`last_result` in `usr/scheduler/tasks.json`.
- `a0 headless --host http://127.0.0.1:5000 --new-chat -p "..." --print --output jsonl`.
- Docker path: stock image with the fork mounted at `/a0` stays healthy and
  reports `is_dockerized=True`, `is_local_runtime=False`.

## Updating
```bash
git pull fork main        # or rebase on origin/main and re-apply the bare-metal commits
bash install-baremetal.sh --no-system-packages   # refresh venv/browser
```
Patches live in these files: `helpers/{runtime,files,file_browser,file_archives,
job_loop,searxng,skills,plugins}.py`, `api/{download_work_dir_file,skills}.py`,
`plugins/_code_execution/tools/code_execution_tool.py`,
`plugins/_office/helpers/libreoffice.py`,
`plugins/_orchestrator/helpers/adapters/a0.py`,
`plugins/_promptinclude/.../_16_promptinclude.py`,
`extensions/python/message_loop_prompts_after/_75_include_workdir_extras.py`
plus `install-baremetal.sh` / `run-baremetal.sh`.

## Known limits
- No isolation: the agent has a real host shell. Keep `workdir_path` on scratch.
- In-app self-update is Docker-only (`supported: false`); update via git.
- `--data-dir` uses a symlink; a few hardened plugins reject symlinked roots.
- Arch/CachyOS: distro has xpra; Ubuntu needs the xpra.org repo (installer).
- Catalog plugins may mutate the venv at startup; test them in a separate
  `--data-dir` instance, not the live one.
