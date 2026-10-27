from __future__ import annotations

import asyncio
import sys
import time
from pathlib import Path
from types import MethodType, SimpleNamespace

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from agent import Agent
from helpers.llm_result import LLMResult, ResponseItem
from helpers.tool import Response


def _result(*names: str, arguments: dict | None = None) -> LLMResult:
    import json

    return LLMResult(
        output_items=[
            ResponseItem(
                type="function_call",
                data={
                    "name": name,
                    "arguments": json.dumps(arguments or {}),
                    "call_id": name,
                },
            )
            for name in names
        ]
    )


class _Tool:
    def __init__(self, name: str, commits: list[str], delay: float = 0.2) -> None:
        self.name = name
        self.concurrency_safe = True
        self.commits = commits
        self.delay = delay
        self.log = SimpleNamespace(id=name, update=lambda **_kwargs: None)

    async def before_execution(self, **_kwargs):
        return None

    async def execute(self, **_kwargs):
        await asyncio.sleep(self.delay)
        return Response(message=self.name, break_loop=False, additional={})

    async def after_execution(self, response, **_kwargs):
        self.commits.append(response.message)


def _agent(tools: dict[str, _Tool]):
    agent = object.__new__(Agent)
    agent.context = SimpleNamespace(id="parallel-test")
    agent.loop_data = SimpleNamespace(current_tool=None, iteration=0)
    agent.get_data = lambda _key: {}
    agent.handle_intervention = lambda *_a, **_k: _noop()
    agent._clear_responses_pending_state = lambda: None
    agent._resolve_tool_for_call = lambda name, *_a, **_k: tools.get(name)
    for name in (
        "_begin_tool_span",
        "_end_tool_span",
        "_run_tool_before",
        "_run_tool_after",
        "_execute_tool_request_batch",
        "_execute_tool_request",
    ):
        setattr(agent, name, MethodType(getattr(Agent, name), agent))
    return agent


async def _noop():
    return None


def _calls(*names: str, arguments: dict | None = None):
    return [
        {
            "tool_name": name,
            "tool_args": arguments or {},
            "message": "",
            "responses_item_factory": None,
        }
        for name in names
    ]


def test_parallel_native_calls_overlap_and_commit_in_model_order(monkeypatch):
    commits: list[str] = []
    slow = _Tool("slow", commits, delay=0.3)
    fast = _Tool("fast", commits, delay=0.05)
    agent = _agent({"slow": slow, "fast": fast})
    monkeypatch.setattr("agent.extension.call_extensions_async", lambda *a, **k: _noop())

    from helpers import settings

    settings.get_settings()
    started = time.perf_counter()
    asyncio.run(
        Agent._execute_tool_request_batch(agent, _calls("slow", "fast"))
    )
    assert time.perf_counter() - started < 0.45
    assert commits == ["slow", "fast"]


def test_parallel_batch_returns_first_break_loop_result(monkeypatch):
    commits: list[str] = []
    first = _Tool("first", commits, delay=0.2)
    second = _Tool("second", commits, delay=0.01)
    second.execute = lambda **_kwargs: _break("final")
    agent = _agent({"first": first, "second": second})
    monkeypatch.setattr("agent.extension.call_extensions_async", lambda *a, **k: _noop())

    result = asyncio.run(
        Agent._execute_tool_request_batch(agent, _calls("first", "second"))
    )
    assert result == "final"
    assert commits == ["first", "final"]


async def _break(message: str):
    return Response(message=message, break_loop=True, additional={})


def test_native_calls_use_batch_path_by_default(monkeypatch):
    class _AgentForDispatch:
        def __init__(self):
            self.batch_calls = 0

        async def _log_response_builtin_items(self, _result):
            return None

        def get_data(self, _key):
            return {}

        async def _execute_tool_request_batch(self, calls, **_kwargs):
            self.batch_calls = len(calls)
            return "batch-result"

        def _resolve_tool_for_call(self, name, *_args):
            return type("Safe", (), {"name": name, "concurrency_safe": True})()

        async def _execute_tool_request(self, **_kwargs):
            raise AssertionError("sequential path should not run")

    agent = _AgentForDispatch()
    monkeypatch.delenv("A0_PARALLEL_TOOLS", raising=False)
    result = asyncio.run(
        Agent.process_llm_result_tools(agent, _result("first", "second"))
    )
    assert result == "batch-result"
    assert agent.batch_calls == 2


def test_parallel_hooks_keep_model_order(monkeypatch):
    events: list[tuple[str, str]] = []
    commits: list[str] = []
    first = _Tool("first", commits, delay=0.2)
    second = _Tool("second", commits, delay=0.01)
    agent = _agent({"first": first, "second": second})

    async def record(name, _agent=None, **kwargs):
        if name in {"tool_execute_before", "tool_execute_after"}:
            events.append((name, kwargs["tool_name"]))

    monkeypatch.setattr("agent.extension.call_extensions_async", record)
    asyncio.run(Agent._execute_tool_request_batch(agent, _calls("first", "second")))

    assert events == [
        ("tool_execute_before", "first"),
        ("tool_execute_before", "second"),
        ("tool_execute_after", "first"),
        ("tool_execute_after", "second"),
    ]
    assert commits == ["first", "second"]


def test_break_loop_tools_keep_sequential_order(monkeypatch):
    class _AgentForDispatch:
        def __init__(self):
            self.seq = []

        async def _log_response_builtin_items(self, _result):
            return None

        def get_data(self, _key):
            return {}

        async def _execute_tool_request_batch(self, _calls, **_kwargs):
            raise AssertionError("break-loop batch must stay sequential")

        def _resolve_tool_for_call(self, tool_name, *_args):
            return type("Unsafe", (), {"concurrency_safe": False, "name": tool_name})()

        async def _execute_tool_request(self, tool_name, **_kwargs):
            self.seq.append(tool_name)
            return "stop" if tool_name == "response" else None

    agent = _AgentForDispatch()
    result = asyncio.run(
        Agent.process_llm_result_tools(agent, _result("response", "other"))
    )
    assert result == "stop"
    assert agent.seq == ["response"]


def test_parallel_intervention_cancels_siblings(monkeypatch):
    from helpers.errors import InterventionException

    commits: list[str] = []
    slow = _Tool("slow", commits, delay=0.5)
    interrupter = _Tool("interrupter", commits, delay=0.01)

    async def interrupt(**_kwargs):
        raise InterventionException()

    interrupter.execute = interrupt
    agent = _agent({"slow": slow, "interrupter": interrupter})
    monkeypatch.setattr("agent.extension.call_extensions_async", lambda *a, **k: _noop())

    started = time.perf_counter()
    try:
        asyncio.run(
            Agent._execute_tool_request_batch(agent, _calls("slow", "interrupter"))
        )
    except InterventionException:
        pass
    else:
        raise AssertionError("intervention should propagate")
    assert time.perf_counter() - started < 0.35
    assert commits == []


def test_read_only_skill_calls_can_parallelize_but_load_cannot(monkeypatch):
    from tools.skills_tool import SkillsTool

    class _AgentForSkills:
        def __init__(self):
            self.batch_calls = 0
            self.seq = []

        async def _log_response_builtin_items(self, _result):
            return None

        def get_data(self, _key):
            return {}

        def _resolve_tool_for_call(self, name, args, _message):
            tool = object.__new__(SkillsTool)
            tool.name = name
            tool.args = args
            return tool

        async def _execute_tool_request_batch(self, calls, **_kwargs):
            self.batch_calls = len(calls)
            return None

        async def _execute_tool_request(self, tool_name, **_kwargs):
            self.seq.append(tool_name)
            return None

    agent = _AgentForSkills()
    monkeypatch.delenv("A0_PARALLEL_TOOLS", raising=False)
    asyncio.run(
        Agent.process_llm_result_tools(
            agent,
            _result("skills_tool", "skills_tool", arguments={"action": "list"}),
        )
    )
    assert agent.batch_calls == 2

    agent.batch_calls = 0
    agent.seq.clear()
    asyncio.run(
        Agent.process_llm_result_tools(
            agent,
            _result("skills_tool", "skills_tool", arguments={"action": "load"}),
        )
    )
    assert agent.batch_calls == 0
    assert agent.seq == ["skills_tool", "skills_tool"]
