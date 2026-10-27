from __future__ import annotations

import asyncio
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import tool_runtime


class _Agent:
    async def _execute_tool_request(self, **kwargs):
        return kwargs["tool_name"]


def test_host_runtime_delegates(monkeypatch):
    monkeypatch.setattr(tool_runtime, "runtime", tool_runtime.HostToolRuntime())
    result = asyncio.run(
        tool_runtime.runtime.execute(_Agent(), tool_name="terminal", tool_args={}, message="")
    )
    assert result == "terminal"


def test_capabilities_are_explicit():

    class ReadTool:
        name = "read_file"
        concurrency_safe = False

        def concurrency_safe_for(self, **kwargs):
            return kwargs.get("action") == "read"

    class WriteTool:
        name = "terminal"

    assert tool_runtime.capabilities(ReadTool(), action="read")["concurrency_safe"] is True
    assert tool_runtime.capabilities(ReadTool(), action="write")["concurrency_safe"] is False
    assert tool_runtime.capabilities(WriteTool())["concurrency_safe"] is False


def test_class_metadata_enables_concurrency():
    class SafeTool:
        name = "safe"
        concurrency_safe = True

    class UnknownTool:
        name = "unknown"

    assert tool_runtime.can_run_concurrently(SafeTool()) is True
    assert tool_runtime.can_run_concurrently(UnknownTool()) is False


def test_skills_tool_parallel_only_for_read_actions():
    from tools.skills_tool import SkillsTool

    tool = object.__new__(SkillsTool)
    tool.name = "skills_tool"
    tool.args = {}
    assert tool.concurrency_safe_for(action="list") is True
    assert tool.concurrency_safe_for(action="search", query="agents") is True
    assert tool.concurrency_safe_for(action="read_file", file_path="SKILL.md") is False
    assert tool.concurrency_safe_for(action="load", skill_name="agents") is False


def test_search_engine_stays_serialized():
    from tools.search_engine import SearchEngine

    tool = object.__new__(SearchEngine)
    tool.name = "search_engine"
    assert tool_runtime.can_run_concurrently(tool, query="independent") is False
