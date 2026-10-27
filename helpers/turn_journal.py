"""Append-only per-turn journal for crash diagnostics and resume reporting.

The journal is additive: ``chat.json`` remains the compatibility surface. It
stores only event metadata (never prompts/tool payloads) so it is safe to read
and cheap to append.
"""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

from helpers import files


def _path(context_id: str) -> Path:
    return Path(files.get_abs_path("usr", "chats", context_id, "turn-journal.jsonl"))


def append(context_id: str, event: str, **fields) -> None:
    if not context_id:
        return
    try:
        path = _path(context_id)
        path.parent.mkdir(parents=True, exist_ok=True)
        record = {"ts": round(time.time(), 6), "event": event, **fields}
        with path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, separators=(",", ":")) + "\n")
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        pass


def last_event(context_id: str) -> dict | None:
    try:
        path = _path(context_id)
        if not path.exists():
            return None
        last = None
        with path.open("r", encoding="utf-8") as handle:
            for line in handle:
                line = line.strip()
                if not line:
                    continue
                try:
                    last = json.loads(line)
                except Exception:
                    continue
        return last
    except Exception:
        return None


def incomplete(context_id: str) -> bool:
    last = last_event(context_id)
    return bool(last and last.get("event") not in ("turn_end", "turn_resumed"))


def compact(context_id: str, keep: int | None = None) -> None:
    """Keep the most recent complete turns; never touches chat.json."""
    if not context_id:
        return
    try:
        limit = int(keep if keep is not None else os.environ.get("A0_TURN_JOURNAL_KEEP", "20"))
    except Exception:
        limit = 20
    if limit <= 0:
        return
    try:
        path = _path(context_id)
        if not path.exists():
            return
        lines = [line for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]
        starts = [i for i, line in enumerate(lines) if '"event":"turn_start"' in line]
        if len(starts) <= limit:
            return
        cut = starts[-limit]
        tmp = path.with_suffix(".tmp")
        tmp.write_text("\n".join(lines[cut:]) + "\n", encoding="utf-8")
        os.replace(tmp, path)
    except Exception:
        pass
