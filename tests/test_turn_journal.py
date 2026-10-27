from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import turn_journal


def test_turn_journal_resume_detection(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    monkeypatch.setattr(turn_journal, "_path", lambda _ctxid: path)

    turn_journal.append("ctx", "turn_start")
    turn_journal.append("ctx", "tool_start", tool="terminal")
    assert turn_journal.incomplete("ctx") is True

    turn_journal.append("ctx", "turn_end", status="ok")
    assert turn_journal.incomplete("ctx") is False
    assert turn_journal.last_event("ctx")["event"] == "turn_end"


def test_turn_journal_compaction_keeps_recent_turns(tmp_path, monkeypatch):
    path = tmp_path / "journal.jsonl"
    monkeypatch.setattr(turn_journal, "_path", lambda _ctxid: path)
    for i in range(5):
        turn_journal.append("ctx", "turn_start")
        turn_journal.append("ctx", "turn_end", status=str(i))
    turn_journal.compact("ctx", keep=2)
    lines = path.read_text().splitlines()
    assert len(lines) == 4
    assert '"status":"4"' in lines[-1]


def test_load_chat_reports_incomplete_journal(tmp_path, monkeypatch):
    from types import SimpleNamespace
    from helpers import persist_chat, settings

    journal = tmp_path / "turn-journal.jsonl"
    monkeypatch.setattr(turn_journal, "_path", lambda _ctxid: journal)
    chat_file = tmp_path / "chat.json"
    chat_file.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(persist_chat, "_get_chat_file_path", lambda _ctxid: str(chat_file))
    monkeypatch.setattr(persist_chat.AgentContext, "get", staticmethod(lambda _ctxid: None))
    monkeypatch.setattr(persist_chat, "update_chat_index", lambda _ctx: None)
    monkeypatch.setattr(persist_chat, "mark_chat_saved", lambda _ctx: None)
    monkeypatch.setattr(
        settings,
        "get_settings",
        lambda: {"turn_journal_resume": "report"},
    )
    fake = SimpleNamespace(id="ctx", data={})
    monkeypatch.setattr(persist_chat, "_deserialize_context", lambda _data: fake)
    turn_journal.append("ctx", "turn_start")
    turn_journal.append("ctx", "tool_start", tool="terminal")

    context = persist_chat.load_chat("ctx")

    assert context is fake
    assert context.data["turn_journal_incomplete"] is True
    assert turn_journal.last_event("ctx")["event"] == "turn_resumed"
