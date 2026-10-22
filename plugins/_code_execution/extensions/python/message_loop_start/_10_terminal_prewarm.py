from helpers.extension import Extension
from plugins._code_execution.helpers import shell_pool


class TerminalPrewarm(Extension):
    """Connect a spare local terminal while the model is thinking."""

    def execute(self, **kwargs):
        if not self.agent:
            return
        shell_pool.ensure_prewarm()
