"""Tool execution seam.

The default adapter keeps A0's existing host execution. The seam exists so a
process/Docker adapter can be added without changing tool names or plugin tool
implementations.
"""

from __future__ import annotations

from typing import Any


class ToolRuntime:
    async def execute(self, agent: Any, **kwargs: Any) -> Any:
        return await agent._execute_tool_request(**kwargs)


class HostToolRuntime(ToolRuntime):
    pass


def capabilities(tool: Any, **args: Any) -> dict:
    """Read explicit per-invocation concurrency metadata; unknown tools serialize."""
    checker = getattr(tool, "concurrency_safe_for", None)
    safe = bool(checker(**args)) if callable(checker) else bool(
        getattr(tool, "concurrency_safe", False)
    )
    return {"concurrency_safe": safe, "isolation": "host"}


def can_run_concurrently(tool: Any, **args: Any) -> bool:
    return capabilities(tool, **args)["concurrency_safe"]





runtime: ToolRuntime = HostToolRuntime()
