from helpers import subagents
from helpers.extension import Extension


class EndPathScope(Extension):
    def execute(self, data: dict | None = None, **kwargs):
        subagents.end_path_scope()
