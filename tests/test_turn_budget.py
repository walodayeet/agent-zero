from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import agent as agent_module
from agent import Agent
from helpers import extension
from helpers.llm_result import LLMResult

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import settings


def test_turn_budget_defaults_are_unlimited():
    defaults = settings.get_default_settings()
    assert defaults["agent_max_turns"] == 0
    assert defaults["agent_turn_timeout_seconds"] == 0


@pytest.mark.asyncio
async def test_monologue_stops_at_max_turns(monkeypatch, tmp_path):
    monkeypatch.setenv("A0_TURN_METRICS_FILE", str(tmp_path / "turns.jsonl"))
    monkeypatch.setattr(
        settings,
        "get_settings",
        lambda: {"agent_max_turns": 1, "agent_turn_timeout_seconds": 0},
    )
    monkeypatch.setattr(extension, "call_extensions_async", lambda *a, **k: _noop())

    agent = object.__new__(Agent)
    agent.context = SimpleNamespace(id="budget-ctx", streaming_agent=None, task=None)
    agent.last_user_message = None
    agent.get_chat_model = lambda: SimpleNamespace(model_name="fake")
    agent.prepare_prompt = lambda **_kwargs: _empty()
    agent.handle_intervention = lambda *_args, **_kwargs: _noop()
    agent._remember_llm_result_state = lambda *_args: None
    agent.hist_add_ai_response = lambda *_args, **_kwargs: SimpleNamespace(id="")
    agent.handle_response_stream = lambda *_args: _noop()
    agent.process_llm_result_tools = lambda _result: _none()
    agent._execute_tool_request = lambda **kwargs: _budget(kwargs)

    calls = 0

    async def call_model(**_kwargs):
        nonlocal calls
        calls += 1
        return LLMResult(response="tool request")

    agent.call_chat_model_turn = call_model
    result = await Agent.monologue.__wrapped__(agent)

    assert calls == 1
    assert result == "Stopped after 1 model turns (agent_max_turns)."


async def _noop():
    return None


async def _empty():
    return []


async def _none():
    return None


async def _budget(kwargs):
    return kwargs["tool_args"]["text"]
