import os

import pytest

from helpers import prompt_compact


@pytest.fixture(autouse=True)
def _clean_env(monkeypatch):
    monkeypatch.delenv("A0_COMPACT_PROMPTS", raising=False)


def test_disabled_by_default_and_enabled_explicitly(monkeypatch):
    assert prompt_compact.enabled() is False
    for value in ("1", "true", "YES", "on"):
        monkeypatch.setenv("A0_COMPACT_PROMPTS", value)
        assert prompt_compact.enabled() is True
    for value in ("0", "false", "no", "off", ""):
        monkeypatch.setenv("A0_COMPACT_PROMPTS", value)
        assert prompt_compact.enabled() is False


def test_tool_prompt_keeps_contract_and_drops_fenced_examples():
    text = """# sample_tool
does a useful thing
with a second description line

## Arguments
- `path`: where to look

## Example
~~~json
{"tool_name": "sample_tool", "tool_args": {"path": "/tmp"}}
~~~

Input schema for tool_args:
{"type": "object", "properties": {"path": {"type": "string"}}}
"""
    compact = prompt_compact.compact_tool_prompt(text)

    assert compact.startswith("# sample_tool")
    assert "does a useful thing" in compact
    assert "- `path`: where to look" in compact
    assert '"type": "object"' in compact
    assert "~~~" not in compact
    assert "tool_name" not in compact


def test_truncation_stays_within_budget_without_open_fence():
    text = "# tool\n" + "\n".join(f"line {index} " + "x" * 40 for index in range(40))

    compact = prompt_compact.compact_tool_prompt(text, max_chars=200)

    assert len(compact) <= 201
    assert compact.endswith("…")
    assert "~~~" not in compact


def test_skill_line_is_name_only_when_compact(monkeypatch):
    description = "A long description " * 10
    monkeypatch.setenv("A0_COMPACT_PROMPTS", "1")
    assert prompt_compact.skill_line("my-skill", description) == "- my-skill"

    monkeypatch.setenv("A0_COMPACT_PROMPTS", "0")
    line = prompt_compact.skill_line("my-skill", description)
    assert line.startswith("- my-skill: A long description")
    assert line.endswith("...")
