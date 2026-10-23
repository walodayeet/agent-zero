from typing import Any

from helpers import prompt_compact
from helpers.extension import Extension
from agent import LoopData


class CompactOutput(Extension):
    """Ask for a minimal tool-call envelope when compact prompts are enabled.

    Only ``tool_name`` and ``tool_args`` are required by the parser; the verbose
    ``thoughts`` array and long headlines cost output tokens on every step.
    """

    async def execute(
        self,
        system_prompt: list[str] = [],
        loop_data: LoopData = LoopData(),
        **kwargs: Any,
    ):
        if not self.agent or not prompt_compact.enabled():
            return
        system_prompt.append(
            "### Compact response format\n"
            "- output only the JSON object\n"
            "- omit `thoughts` and `headline` for direct answers; add one short\n"
            "  `thoughts` line only when the step needs planning\n"
            "- `tool_args`: only the keys the tool needs"
        )
