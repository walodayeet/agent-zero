import os
from typing import Any

from helpers.extension import Extension, extensible
from helpers import skills as skills_helper, tool_policy
from agent import Agent, LoopData


class SkillsPrompt(Extension):

    async def execute(
        self,
        system_prompt: list[str] = [],
        loop_data: LoopData = LoopData(),
        **kwargs: Any,
    ):
        if not self.agent:
            return
        prompt = await build_prompt(self.agent)
        if prompt:
            system_prompt.append(prompt)


@extensible
async def build_prompt(agent: Agent) -> str:
    if not tool_policy.resolve_tool(agent, "skills_tool").allowed:
        return ""

    available = skills_helper.list_skills(agent=agent)
    result: list[str] = []
    # Large catalogs dominate the prompt (100+ skills ≈ 5k tokens per model
    # call). Keep names only; descriptions stay discoverable through
    # skills_tool search/list.
    compact_at = int(os.environ.get("A0_COMPACT_SKILLS_AT", "30"))
    compact = len(available) > compact_at
    for skill in available:
        name = skill.name.strip().replace("\n", " ")[:100]
        if compact:
            result.append(f"- {name}")
            continue
        descr = skill.description.replace("\n", " ").strip()
        if len(descr) > 100:
            descr = descr[:100].rstrip() + "..."
        result.append(f"- {name}: {descr}" if descr else f"- {name}")

    if not result:
        return ""

    return agent.read_prompt("agent.system.skills.md", skills="\n".join(result))
