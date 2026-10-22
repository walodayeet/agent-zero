from helpers import subagents
from helpers.extension import Extension


class BeginPathScope(Extension):
    """Memoize subagent path resolution for the duration of one prompt build."""

    def execute(self, data: dict | None = None, **kwargs):
        subagents.begin_path_scope()
