# prompt_compact.py DOX

## Purpose

- Own the `prompt_compact.py` helper module.
- Shrinks the static parts of the agent system prompt (tool docs, skill list, workdir tree) for lower time-to-first-token.
- Keep this file-level DOX profile synchronized with `prompt_compact.py` because this directory is intentionally flat.

## Ownership

- `prompt_compact.py` owns the runtime implementation.
- `prompt_compact.py.dox.md` owns durable notes about responsibilities, contracts, side effects, and verification for that implementation.
- Top-level functions:
- `enabled() -> bool`: opt-in via `A0_COMPACT_PROMPTS` (`1`/`true`/`yes`/`on`); unset keeps the full prompt.
- `compact_tool_prompt(text: str, max_chars: int=...) -> str`
- `skill_line(name: str, description: str) -> str`
- Notable constants/configuration names: `_DEFAULT_TOOL_CHARS` (350), `_SKILL_DESCRIPTION_CHARS`.

## Runtime Contracts

- `compact_tool_prompt` keeps the heading, the first paragraph, the `## Arguments`/`args` section and any `Input schema for tool_args:` JSON. Fenced example blocks are removed before truncation so the result never contains a dangling fence; the result is cut at a line boundary with a trailing ellipsis when over budget.
- `skill_line` returns `- name` only when compaction is enabled; otherwise it keeps the first 100 characters of the description. `skills_tool search/list` still exposes descriptions on demand.
- Consumers: `extensions/python/system_prompt/_11_tools_prompt.py`, `_13_skills_prompt.py`, `_14_compact_output.py` (adds a minimal envelope instruction) and `extensions/python/message_loop_prompts_after/_75_include_workdir_extras.py` (caps the workdir tree at depth 3 / 10 files / 10 folders / 60 lines).
- Observed side-effect areas: none beyond reading `A0_COMPACT_PROMPTS`; it only rewrites prompt text.
- Imported dependency areas include: `json`, `os`, `re`, `typing`.

## Work Guidance

- Keep the compactor conservative: tool names, argument contracts and embedded schemas must survive; drop only examples and verbose prose.
- Do not change the default: tests and non-launcher runs expect the full prompt unless `A0_COMPACT_PROMPTS` is set.

## Verification

- `tests/test_prompt_compact.py` covers env parsing, fence removal, truncation bounds and skill lines.
- `tests/test_default_prompt_budget.py` asserts the full-prompt contract with compaction unset.
- Measured on the Windows host: agent0 prompt 19,892 -> 6,546 tokens, simple reply 2.20 s vs 6.41 s for Hermes on the same gateway.

## Child DOX Index

No child DOX files.
