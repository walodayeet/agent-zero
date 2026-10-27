from __future__ import annotations

import json
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers.turn_metrics import TurnMetrics


def test_turn_metrics_writes_one_record(tmp_path, monkeypatch):
    target = tmp_path / "turns.jsonl"
    monkeypatch.setenv("A0_TURN_METRICS_FILE", str(target))
    metrics = TurnMetrics("ctx", "model")
    metrics.api(0.5, iteration=1)
    metrics.tool("terminal", 0.2, iteration=1)
    metrics.end("ok")
    records = [json.loads(line) for line in target.read_text().splitlines()]
    assert len(records) == 1
    assert records[0]["context"] == "ctx"
    assert records[0]["status"] == "ok"
    assert [event["kind"] for event in records[0]["events"]] == ["api", "tool"]
    assert all(event["iteration"] == 1 for event in records[0]["events"])


def test_turn_metrics_can_be_disabled(tmp_path, monkeypatch):
    target = tmp_path / "turns.jsonl"
    monkeypatch.setenv("A0_TURN_METRICS_FILE", str(target))
    monkeypatch.setenv("A0_TURN_METRICS", "0")
    metrics = TurnMetrics("ctx")
    metrics.end()
    metrics.end()
    assert not target.exists()


def test_turn_metrics_end_is_idempotent(tmp_path, monkeypatch):
    target = tmp_path / "turns.jsonl"
    monkeypatch.setenv("A0_TURN_METRICS_FILE", str(target))
    metrics = TurnMetrics("ctx")
    metrics.end("ok")
    metrics.end("error")
    assert len(target.read_text().splitlines()) == 1
