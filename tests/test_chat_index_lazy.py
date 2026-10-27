from __future__ import annotations

import json
import sys
import types
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import persist_chat


def _write_chat(path: Path, ctxid: str, name: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            {
                "id": ctxid,
                "name": name,
                "created_at": "2026-01-01T00:00:00+00:00",
                "last_message": "2026-01-02T00:00:00+00:00",
                "type": "user",
                "agent_profile": "agent0",
                "output_data": {"parent_context_id": None},
                "log": {"guid": "g", "logs": []},
                "agents": [],
            }
        ),
        encoding="utf-8",
    )


def test_index_refresh_and_cache_roundtrip(tmp_path, monkeypatch) -> None:
    index_path = tmp_path / "usr" / "index.json"
    chat_root = tmp_path / "chats"
    monkeypatch.setattr(persist_chat, "_chat_index_path", lambda: str(index_path))
    monkeypatch.setattr(
        persist_chat,
        "_get_chat_file_path",
        lambda ctxid: str(chat_root / ctxid / persist_chat.CHAT_FILE_NAME),
    )
    monkeypatch.setattr(persist_chat, "_INDEX_META_CACHE", None)
    monkeypatch.setattr(persist_chat, "CHATS_FOLDER", str(chat_root))

    _write_chat(chat_root / "abc" / persist_chat.CHAT_FILE_NAME, "abc", "Alpha")

    index = persist_chat.refresh_chat_index()
    assert list(index["entries"]) == ["abc"]
    rows = persist_chat.indexed_chat_outputs()
    assert rows[0]["id"] == "abc"
    assert rows[0]["name"] == "Alpha"

    persist_chat.remove_chat_index_entry("abc")
    assert persist_chat.indexed_chat_outputs() == []
    (chat_root / "abc" / persist_chat.CHAT_FILE_NAME).unlink()
    assert persist_chat.refresh_chat_index()["entries"] == {}


def test_index_meta_tolerates_partial_context() -> None:
    context = types.SimpleNamespace(id="ctx", name="Name")
    meta = persist_chat._chat_meta_from_context(context)
    assert meta["id"] == "ctx"
    assert meta["name"] == "Name"
    assert meta["no"] == 0
    assert meta["type"] == "user"
