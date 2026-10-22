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
  plugin unavailable. Smoke-tested on Windows 11 26200 (Python 3.12 venv via uv):
  install, startup, WebUI, settings modal, and a real chat turn through a local
  OpenAI-compatible gateway all work. Windows-specific fixes that this required:
  `helpers/files.fsync_directory` (no-op on Windows; a directory cannot be opened
  as a descriptor), `os.geteuid` guards in the Desktop/Office package hooks, and
  a tolerant `fcntl` import in `plugins/_desktop/helpers/desktop_session.py`
  (used only by the generated Linux bridge scripts). The full suite runs green on
  Windows as well: `pytest tests` -> 2274 passed, 22 skipped (POSIX-only
  Desktop/Xpra/apt/unix-socket cases), matching the Linux count once its
  POSIX-only skips are removed. `tests/conftest.py` points Playwright at the
  installer's `tmp/playwright` cache so the DOM regressions run without env setup.
  Windows latency notes (measured on that box): ConPTY input must end with CR —
  sending `\r\n` leaves PowerShell on the `>>` continuation prompt, so the shell
  never reports a prompt and every command waited out the 15 s idle timeout
  (`tty_session` now normalises to `\r`; commands return in ~2 s).
  `api/tunnel_proxy` probed `localhost:55520` with a blocking `requests.post` and
  no timeout; a closed loopback port costs ~2 s per address there, which stalled
  `/api/csrf_token` for 4 s and made the WebUI report "backend appears to be
  disconnected". Local runtime now calls the in-process manager directly and the
  split-service path uses async httpx with a 0.75 s timeout. UI polls no longer
  persist the browser timezone (`set_timezone(..., persist=False)`), which used to
  rewrite `.env` and trigger a settings reload on every poll.
  `run-baremetal.ps1` defaults `A0_PRELOAD=1` with `A0_PRELOAD_WAIT=0`, so the
  embedding model warms in the background after HTTP is up: first-turn memory
  recall 7.2 s -> 0.08 s, at the cost of ~1.7 GB RSS while unused. Remaining turn
  latency is model-bound: the system prompt is ~68 KB (~15-17 k tokens) with the
  installed plugins, and the gateway needs ~3 s for that prompt vs ~1.4 s for 82
  tokens; prompt assembly itself is ~75 ms.
- Core cross-platform bits: `/a0` mapping uses `os.path`; code execution uses
  `powershell.exe` + pywinpty on Windows; `find_soffice` checks app-bundle and
  Program Files paths.

## Data directory
Default: `~/agent-zero/<instance>/usr` (matches A0 Launcher/A0 Install:
`~/agent-zero/<name>/usr`, default instance `agent-zero`). The installer and the
launcher ask once on first run (enter `local` to keep `<repo>/usr`); the answer is
stored in `.a0-data`. Overrides: `--data-dir PATH` / `-DataDir`, `A0_DATA_DIR`,
`A0_INSTANCE`, `--non-interactive`, `A0_NO_ONBOARDING=1`. Repo `usr` becomes a
symlink to the chosen directory; migration is installer-only and copy-verified.

Startup cost: `A0_PRELOAD_WAIT` bounds how long startup blocks on the embedding
warm-up (default 10s; the bare-metal launchers export 0). `A0_PRELOAD=0` skips the
warm-up itself — the local embedding model then loads on first use (~6s once per
process, ~1.7GB RSS saved while it is unused). Both launchers default to 0/0.

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
- Model search honors a custom `api_base` for providers whose metadata ships an
  absolute endpoint (e.g. OpenAI) and bypasses proxy env for loopback hosts.
- `runtime.initialize()` appends loopback hosts to `NO_PROXY` on bare metal, so
  manually started instances behave like the launcher.
- Launchers fail fast when the port is taken and print the owning process.
- Diagnose gateways with `scripts/check-local-gateway.sh URL KEY [MODEL]`
  (uses `curl --noproxy '*'`, so it tests the gateway, not the proxy).
- `A0_KEEP_LOCALHOST=1` disables the localhost→127.0.0.1 rewrite for IPv6-only
  gateways.
- Docker is upstream and untouched in this fork; native A0 has no Docker network
  layer at all. Launcher/A0 Install containers (external projects) use bridged
  networks — there `localhost` is the container, so use `host.docker.internal`.
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
