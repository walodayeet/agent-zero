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
  Memory footprint per bare-metal instance (Windows 11, measured working set):
  server idle with the embedding model unloaded (`A0_PRELOAD=0`) ~515 MB; after
  the embedding model is warm (background preload or first chat message)
  ~0.8-1.0 GB, with commit charge 2-5 GB because torch/faiss reserve arenas.
  The `run-baremetal.ps1` wrapper adds ~140 MB, each open terminal session adds a
  PowerShell process (~75 MB), and the first Browser-tool use leaves a Chromium
  plus its node driver alive (~0.4 GB) until it is closed. The terminal prefers
  `pwsh` when installed (the installer adds Microsoft.PowerShell): Windows
  PowerShell 5.1 rejects the bash-style `&&` the agent writes, and each retry
  costs a whole model turn. pwsh starts in ~3.7 s versus ~3.3 s for 5.1; the
  readiness barrier in `shell_local.connect()` hides that behind the first model
  A spare shell is connected on the shared AgentContext loop when the server
  becomes healthy and after each adoption (`plugins/_code_execution/helpers/
  shell_pool.py`), so a new chat's first command normally runs in ~0.1-0.3 s
  instead of waiting ~3 s for PowerShell. `A0_TERMINAL_PREWARM=0` disables it.
  Workdir settings that still point at a POSIX path (copied from a Linux
  checkout) fall back to `usr/workdir` on Windows instead of creating `C:\home\`.
  WebUI boot no longer blocks first paint on the ~3 MB service-worker bundle
  cache write: the app document is written immediately and the cache warms in the
  background (cold FCP 896-1408 ms -> 332-708 ms, DCL ~50 ms, same 250 assets).
## Linux parity
- `run-baremetal.sh` already exports the same knobs the Windows launcher sets:
  `KMP_DUPLICATE_LIB_OK=TRUE` (same duplicate-OpenMP abort) and the ONNX
  embedding backend; `install-baremetal.sh --systemd` generates a
  systemd --user unit that runs the launcher with `Restart=on-failure`, which is
  the Linux equivalent of the Windows scheduled tasks (no cron watchdog needed).
- `initialize_preload()` now starts `preload.warm_skills` even when
  `A0_PRELOAD=0` (the Linux default), so the first prompt build does not parse
  the 233 SKILL.md files. Embeddings/vector store still load lazily there.
- The terminal poll/echo fix and the UI cache headers are platform-neutral
  Python and apply on Linux unchanged.

## Transport and remote
- uvicorn ran on the pure-Python h11 parser because `httptools` was not
  installed. `requirements.txt` now pins `httptools>=0.6` (auto-selected by
  uvicorn) and `uvloop>=0.21; sys_platform != "win32"`; `install-baremetal.sh`
  installs from that file, so Linux gets libuv too. Measured on this host: 200
  keep-alive `/api/health` requests in 0.94 s (~213 req/s) through the full
  Flask/WSGI stack.
- WebSocket permessage-deflate is already active (uvicorn default
  `ws_per_message_deflate=True`); the 1.1 MB state snapshot for a large chat is
  compressed on the wire. python-socketio's `http_compression` covers the
  polling fallback (default on).
- Remote terminals use Paramiko; `SSHClient.connect(compress=True)` now
  compresses the SSH transport, which matters for text-heavy command output on
  WAN links. Transport keep-alive was already set (5 s).
- Verified in a real browser via CDP: the socket.io handshake answers
  `101` with `Sec-WebSocket-Extensions: permessage-deflate;
  client_max_window_bits=15`, six state frames received in the first seconds.
- Measured on this host after the transport changes: cold start to health `5.7 s`,
  first turn `6.3 s` (cold prompt/model caches), warm turn `3.4 s`, restore of
  67 chats `0.5 s`, WebUI boot to chat list `~2.8 s`, 200 keep-alive health
  requests `0.94 s`.
- Tried and reverted: warming the system prompt in a startup task. It cut the
  first prompt build 0.59 s -> 0.08 s but raised cold start 5.7 s -> 8.4 s
  because `initialize_agent()` runs a second time. Startup wins.
- Tried and reverted: serving component HTML from `/ui/asset-bundle` on first
  load. The bundle is 259 files / 3.1 MB and resolves after most components have
  already started loading, so it added bytes without cutting the 60-64 component
  requests. The service-worker preload path upstream is unchanged.

## UI and process health
- Static UI files were served with `Cache-Control: no-cache`, so every page load
  revalidated ~200 JS/CSS/component files; Flask now sets
  `SEND_FILE_MAX_AGE_DEFAULT=3600` (reload: 23 network requests, 201 cache hits).
- The server process was dying mid-turn with `OMP: Error #15: ... libomp140 ...
  already initialized` (onnxruntime and the faiss/sklearn stack ship different
  OpenMP runtimes). Both launchers export `KMP_DUPLICATE_LIB_OK=TRUE`, which
  stops Intel's duplicate-runtime abort. This was the "UI stuck / Not connected"
  symptom: the backend aborted while the browser waited for the response.
- The server runs under the `AgentZeroServer` scheduled task (onlogon);
  `AgentZeroPerf` runs `scripts/ensure-a0.ps1` every 5 minutes and restarts it
  only when port 5000 is free. Task Scheduler owns the process tree, so closing
  a terminal no longer kills it.
- Removed 125 benchmark-only chats (`bench*`, `PONG*`, `Ping*` titles) that made
  the sidebar list huge; real chats untouched.
- Known cosmetic issue: the background memorize util items open a "Processing..."
  group after the response and never close it, so the transcript keeps one
  spinner card at the bottom until the next message.

## Prompt policy
- The system prompt is the upstream full prompt: tool docs with examples,
  the complete skill catalog with descriptions, and the workdir tree. Compaction
  was reverted; prompts are never traded for tokens.
- Performance work must stay architectural: terminal I/O, startup/restore,
  caching of computed artifacts, process model, event-loop blocking.

## File discovery (workdir/project tree in the prompt)
- `_75_include_workdir_extras` used to scan synchronously inside the async
  prompt build. On the default workdir the tree is ~7.5 k chars and costs
  ~0.05 s warm; on a project that points at a large checkout the layout
  signature walk alone costs 0.25-0.6 s (9 k+ entries) and blocked the event
  loop for every chat. Both `file_tree` and `projects.get_file_structure` now
  run through `asyncio.to_thread`, so the scan no longer stalls other chats or
  the WebUI. The rendered prompt is byte-identical (76,834 chars, 68,468 of
  system text).
- The tree is still cached by layout signature upstream, so add/remove/rename
  and modified-time reordering invalidate it; that guarantee is unchanged.

## Non-LLM paths
- Terminal tool: `get_terminal_output` polled every 0.5 s, so even `echo` cost
  ~0.64 s; it now polls every 0.1 s (~0.15-0.2 s). ConPTY re-renders the typed
  line, and the reader saw the command concatenated with itself, which made the
  model spend its second call reasoning about the noise; `fix_full_output` now
  drops leading copies of the sent command. Measured tool step (LLM call + tool)
  2.65 s -> 1.72 s per call. The console stream still prints the raw redraw;
  only the tool result is cleaned, which is what the model reads.
- Preload warms the skill catalog (`skills.list_skills`): cold 0.33 s vs warm
  0.03 s, and the first system-prompt build parses 233 SKILL.md files otherwise.
- Per-turn non-LLM budget (warm): prompt build 0.15 s, memory recall 0.05-0.25 s
  (background, delayed), chat persistence ~0.07 s, context/welcome ~0.3 s on a
  new chat. Model call remains 1.4-2.5 s and dominates.

## Hermes head-to-head (same host, same gateway/model)
- Hermes uses `%LOCALAPPDATA%\hermes` on Windows (not `~/.hermes`). Its
  `config.yaml` has `model: {default: deepseek-v4.1-flash, provider: cliproxy,
  base_url: http://127.0.0.1:20301/v1}` and a `providers.cliproxy` entry with
  `key_env: CLIPROXY_API_KEY`; the key lives in that home's `.env`.
- Interactive protocol: Hermes runs in a Herdr pane (`herdr agent start
  hermes-bench --kind hermes --pane w2:p1 -- --yolo`, persistent TUI, YOLO). A
  turn is timed from `pane send-text` + `Enter` to the agent's next `done`
  completion. Agent Zero is timed over `api/message_async` (fresh context) to the
  turn's `response` log entry. Tasks run interleaved (A0 then Hermes per rep) so
  gateway drift hits both, five reps, one warm-up each.
- Medians with the full prompt restored (2026-10-23, fresh context per turn):
  simple reply 3.56 s (2.65-7.35), terminal tool 4.66 s, three-sentence answer
  4.64 s. The compact-prompt figures (1.94 / 2.61 / 1.94 s) are void: they were
  bought by shrinking the system prompt, which is not allowed.
- Hermes interactive (TUI, Herdr): pong 2.77 s, tool 5.17 s, explain 3.16 s.
  The model call is now the floor: 68k static system prompt + ~30-60 output
  tokens per turn on the shared gateway. Architecture cannot cut the generation
  time; only the ~0.2 s prompt assembly and ~0.1 s state work are left.
- The model is the floor on short turns: a direct gateway call with a cached
  prefix returns in 1.0-1.3 s TTFT regardless of whether the prompt is 1.5 k or
  13 k characters, and the endpoint always emits reasoning tokens. 2x on a
  trivial reply would need Agent Zero's whole turn under ~1.1 s, below the model
  call itself; tool and multi-step turns are where the 2x gap is reachable.
- One-shot `hermes -z` numbers (6.4-8.2 s) include its process and plugin boot
  and are not comparable with the interactive TUI.
- Responses API (`a0_api_mode: responses`) was tried and reverted- Responses API (`a0_api_mode: responses`) was tried and reverted: 8.9-13.3 s per
  simple turn on this gateway, slower than chat completions.
  With the ONNX embedding backend (`A0_EMBEDDING_BACKEND=onnx`, default in both
  launchers) and the vector-store warm-up in the same background preload, the
  server settles at ~500 MB idle and ~690 MB after a turn (commit ~3.5-3.7 GB):
  torch/transformers are never imported, the embedding model loads in ~1.7 s
  instead of ~10 s, vectors are identical to sentence-transformers (max abs diff
  < 2e-7), and the first recall no longer pays the FAISS load (~2.6 s -> 0.6 s).
  `A0_EMBEDDING_BACKEND=st` restores sentence-transformers; `A0_PRELOAD_MEMORY=0`
  keeps the vector-store warm-up on first use. Layer map (working set): app import
  ~99 MB, plugin/component init +78 MB, ONNX session + vector store +145 MB,
  HTTP stack (uvicorn/socketio/a2wsgi/Flask) ~+175 MB, first LLM call (litellm
  +138 MB, faiss +23 MB).
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
