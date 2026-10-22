"""Compact the static parts of the system prompt for lower time-to-first-token.

Tool docs are markdown guides: a description, argument notes, workflows and
examples. The model needs the name, the description and the argument contract;
the long examples mostly cost prefill tokens on every call. With
``A0_COMPACT_PROMPTS`` enabled (default), tool docs keep the heading, the first
paragraph, the argument section and any embedded JSON schema, and skill entries
keep a shorter description.
"""

from __future__ import annotations

import json
import os
import re
from typing import Any

_DEFAULT_TOOL_CHARS = 350
_SKILL_DESCRIPTION_CHARS = 60


def enabled() -> bool:
    """Opt-in: the launchers set A0_COMPACT_PROMPTS=1; unset keeps full docs."""
    raw = str(os.environ.get("A0_COMPACT_PROMPTS", "")).strip().lower()
    return raw in ("1", "true", "yes", "on")


def _balanced_json(text: str, start: int) -> str:
    depth = 0
    in_string = False
    escape = False
    for index in range(start, len(text)):
        char = text[index]
        if in_string:
            if escape:
                escape = False
            elif char == "\\":
                escape = True
            elif char == '"':
                in_string = False
            continue
        if char == '"':
            in_string = True
        elif char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth == 0:
                return text[start : index + 1]
    return ""


def _first_paragraph(lines: list[str]) -> list[str]:
    paragraph: list[str] = []
    for line in lines:
        if not line.strip():
            if paragraph:
                break
            continue
        if line.lstrip().startswith("#"):
            break
        paragraph.append(line.rstrip())
    return paragraph


def _argument_section(lines: list[str]) -> list[str]:
    collected: list[str] = []
    inside = False
    for line in lines:
        heading = line.lstrip().startswith("#")
        if heading:
            inside = bool(re.match(r"#{1,3}\s*(arguments?|args)\b", line.strip(), re.I))
            if inside:
                collected.append(line.rstrip())
            continue
        if inside:
            collected.append(line.rstrip())
    return collected


def compact_tool_prompt(text: str, max_chars: int = _DEFAULT_TOOL_CHARS) -> str:
    """Keep the tool contract, drop the long examples/workflow prose."""
    stripped = text.strip()
    if not stripped:
        return ""
    # Schemas may live in a fence: capture them before examples are dropped.
    marker = "Input schema for tool_args:"
    marker_index = stripped.find(marker)
    schema = ""
    if marker_index != -1:
        brace = stripped.find("{", marker_index)
        schema = _balanced_json(stripped, brace) if brace != -1 else ""

    # Examples are fenced blocks; dropping them keeps the doc contract and never
    # leaves a dangling fence when the text is truncated.
    unfenced = re.sub(r"(?ms)^\s*(```|~~~).*?^\s*\1\s*$", "", stripped)
    lines = unfenced.splitlines()
    title = lines[0].rstrip() if lines else ""
    body = lines[1:]

    parts: list[str] = [title]
    description = _first_paragraph(body)
    if description:
        parts.append("\n".join(description))
    arguments = _argument_section(body)
    if arguments:
        parts.append("\n".join(arguments))
    if schema:
        parts.append(f"{marker}\n{schema}")

    compact = "\n".join(part for part in parts if part.strip())
    if len(compact) <= max_chars:
        return compact
    cut = compact.rfind("\n", 0, max_chars)
    return compact[: cut if cut > 0 else max_chars].rstrip() + "\n…"


def skill_line(name: str, description: str) -> str:
    name = name.strip().replace("\n", " ")[:100]
    if enabled():
        # Names only: skills_tool search/list still exposes the descriptions on
        # demand, and this saves ~4.5k prefill tokens with a large skill library.
        return f"- {name}"
    description = description.replace("\n", " ").strip()
    if len(description) > 100:
        description = description[:100].rstrip() + "..."
    return f"- {name}: {description}" if description else f"- {name}"
