# Windows handoff — core Agent Zero only

This is for a new user. Do **not** copy `usr/plugins` or install the Jev, Mnemosyne,
Vault, Bitwarden, Caveman, or other custom plugins from this development machine.

The Windows port covers only changes in the Agent Zero code tree:

- `agent.py`
- `helpers/` (lazy chats, lazy UI, metrics, journal, tool runtime, settings, extension timeout)
- `models.py`
- `run_ui.py`, `initialize.py`
- `extensions/python/...`
- `prompts/`, `webui/`
- bundled `plugins/` source (including `_model_config`)
- `run-baremetal.ps1`, `install-baremetal.ps1`

## What to copy
Copy the repository **without** the data directory:

- Keep: `agent.py`, `helpers/`, `models.py`, `webui/`, `prompts/`, `plugins/`,
  `agents/`, `run_ui.py`, `initialize.py`, `requirements.txt`,
  `pyproject.toml`/lockfiles if present, installer/launcher files.
- Exclude: `usr/` (chats, plugins, tokens, databases, settings, logs),
  `.venv/`, `tmp/`, `plans/`, local benchmark scripts.
- The Windows checkout should start with an empty `usr/`; the launcher creates
  its folders.

## Windows setup
1. Install Python 3.12 and `uv`.
2. In PowerShell:
   ```powershell
   powershell -ExecutionPolicy Bypass -File .\install-baremetal.ps1
   .\run-baremetal.ps1
   ```
3. Open `http://127.0.0.1:5000`.

`run-baremetal.ps1` enables lazy WebUI loading (`A0_LAZY_WEBUI=1`) and otherwise
uses stock Agent Zero settings. No custom plugin dependencies are installed.

## Intentionally not included
- Jev toolkit/routers, Mnemosyne provider, Vault/Bitwarden plugins, caveman,
  Evey verification, output limiter, time hooks, Herdr, curator, Nerve.
- Hermes databases, Vault tokens, Bitwarden sessions, TypeSafe keys.
- Any `usr/plugins` directory from the development machine.

## Status
Windows code-level portability was reviewed but **not executed on Windows**.
The Linux full suite and live instance were used for verification of the shared
core code.
