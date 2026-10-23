# Performance report — Agent Zero vs Hermes (bare metal)

Date: 2026-10-23. Host: Ubuntu 24.04, 28 cores, local CLiProxyAPI gateway
(20301) shared by both agents, model `deepseek-v4.1-flash`.

## How to reproduce

```bash
# isolated A0 instance with an empty chat list
A0_DATA_DIR=/tmp/a0bench A0_NO_ONBOARDING=1 A0_PORT=5010 ./run-baremetal.sh

python scripts/bench/bench.py turns --port 5010 --reps 5
python scripts/bench/bench.py turns --port 5010 --reps 4 --prompt "Run this in the terminal: echo PONG. Then report stdout."
python scripts/bench/bench.py ui --port 5010 --mode cold --reps 3
python scripts/bench/bench.py ui --port 5010 --mode warm --reps 3
python scripts/bench/bench.py http --port 5010 --n 200
```

Each turn uses a fresh chat context through `POST /api/message` (blocking until the
response). The first turn after a restart is excluded (cold model/prompt caches);
medians are warm turns.

## Turn latency (warm, same gateway/model)

| Task | Agent Zero (this branch) | Hermes (interactive, 2026-10-23) |
|---|---|---|
| `Reply with exactly: PONG` | **1.38 s** (1.18–1.54) | 2.77 s |
| Terminal tool (`echo PONG`) | **2.92 s** (2.81–2.98) | 5.17 s |
| Three-sentence explanation | **1.60 s** (1.57–1.61) | 3.16 s |

The largest single win was protocol handling: when the model answered in plain
text, `_context_doctor` used to keep it as “thoughts”, skip processing and spend a
second model call on a tool-call reminder. It now converts the plain reply into a
`response` tool call and finishes the turn (`plain_text_as_response: true`,
configurable). Tool-call turns already used one call; they benefit from the
existing terminal/preload work and `uvloop`/`httptools`.

Hermes numbers are from the shared-host head-to-head recorded in `HANDOFF.md`
(same gateway and model, fresh context per turn). A fresh Hermes interactive
client could not re-run here: the running Hermes gateway rejects a second
interactive peer (`invalid_control_peer`) and its dashboard requires the owner's
password. Its OpenAI-compatible `api_server` platform is enabled in config but
was not listening on this host at measurement time.

## HTTP stack

| Metric | Agent Zero | Hermes |
|---|---|---|
| `/api/health`, 100 keep-alive requests | 0.52 s (192 req/s, 5.0 ms median) | 0.20 s (500 req/s, 2.0 ms median) |

A0's health handler passes through Flask+a2wsgi+Socket.IO and re-checks git state;
Hermes serves health directly from aiohttp. This does not affect turn latency.

## WebUI

| Mode | app ready (`#chat-input`) | FCP | requests | encoded payload |
|---|---|---|---|---|
| cold (caches cleared) | 3.1–3.4 s | 1.4–2.6 s | 213–240 | 4.2–4.4 MB (≈1.2 MB gzip) |
| warm (SW cache) | 1.5–1.6 s | ~1.0 s | 237 (cached, ≈20 KB network) | 4.3 MB |

Main-thread work per warm boot (CDP `Performance.getMetrics`): ~710 ms task time,
~250 ms script, ~45 ms style, ~20 ms layout; the rest is waiting on the
service-worker/asset fan-out.

The authenticated Hermes dashboard was not benchmarked (auth-gated). Its public
login page is tiny (4 requests, FCP 68–300 ms), so it is not a fair proxy for the
app shell.

### UI experiments measured and rejected

- ASGI `StaticFiles` mounts for `/js`, `/css`, `/components`, `/vendor`,
  `/public` with `Cache-Control`: no measurable change in app-ready
  (3.14 s vs 3.17 s cold, 1.54 s vs 1.61 s warm) and slightly lower raw static
  throughput; reverted to keep the Flask path and ETag revalidation.
- Upstream's asset-bundle preload was already tried and documented as reverted
  (259 files / 3.1 MB, arrives after component loading starts).

## Remaining opportunities

1. Component fan-out: ~150 nested `x-component` fetches form a depth-4/5
   waterfall. A build-time component bundle that is *inline in the first HTML*
   (not fetched later) is the only structural fix left; it needs a careful
   ordering test with Alpine initialisation.
2. Health endpoint: cache git info separately from settings and answer before
   Flask when only git/version is requested.
3. Chat persistence and log streaming: ~0.1 s per turn; batching writes would
   shave a small constant.
4. Hermes dashboard comparison requires the owner password or a dedicated
   benchmark profile; add it when credentials are available.
