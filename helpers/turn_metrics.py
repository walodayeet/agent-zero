"""Per-turn timing metrics with no effect on agent behaviour.

Records one JSON object per user turn in ``usr/metrics/turns.jsonl`` unless
``A0_TURN_METRICS=0``. Model/tool spans are collected in memory and flushed at
turn end so a busy loop never performs synchronous disk I/O per token.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path
from typing import Any

from helpers import files


class TurnMetrics:
    def __init__(self, context_id: str, model: str = "") -> None:
        self.context_id = context_id
        self.model = model
        self.started = time.time()
        self._t0 = time.perf_counter()
        self.events: list[dict[str, Any]] = []
        self._ended = False

    @property
    def enabled(self) -> bool:
        return os.environ.get("A0_TURN_METRICS", "1") != "0"

    def api(
        self, duration: float, *, error: bool = False, iteration: int | None = None
    ) -> None:
        if self.enabled:
            self.events.append(
                {
                    "kind": "api",
                    "duration_s": round(duration, 4),
                    "error": error,
                    **({"iteration": iteration} if iteration is not None else {}),
                }
            )

    def tool(
        self,
        name: str,
        duration: float,
        *,
        error: bool = False,
        iteration: int | None = None,
    ) -> None:
        if self.enabled:
            self.events.append(
                {
                    "kind": "tool",
                    "tool": name,
                    "duration_s": round(duration, 4),
                    "error": error,
                    **({"iteration": iteration} if iteration is not None else {}),
                }
            )

    def end(self, status: str = "ok") -> None:
        if not self.enabled or self._ended:
            return
        self._ended = True
        record = {
            "ts": round(self.started, 3),
            "context": self.context_id,
            "model": self.model,
            "status": status,
            "duration_s": round(time.perf_counter() - self._t0, 4),
            "events": self.events,
        }
        try:
            path = Path(files.get_abs_path("usr", "metrics", "turns.jsonl"))
            path.parent.mkdir(parents=True, exist_ok=True)
            override = os.environ.get("A0_TURN_METRICS_FILE", "").strip()
            if override:
                path = Path(override).expanduser()
            with path.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps(record, separators=(",", ":")) + "\n")
        except Exception:
            # Metrics must never break a turn.
            pass
