from helpers.extension import Extension
from agent import LoopData

# Direct import - this extension lives inside the memory plugin
from plugins._memory.helpers import memory


class MemoryInit(Extension):

    async def execute(self, loop_data: LoopData = LoopData(), **kwargs):
        if not self.agent:
            return
        # Memory tools initialize lazily on first use. Skip the expensive
        # vector/embedding bootstrap when automatic recall/memorization is off.
        try:
            from helpers import plugins
            cfg = plugins.get_plugin_config("_memory", agent=self.agent) or {}
            if not cfg.get("memory_recall_enabled", True) and not cfg.get("memory_memorize_enabled", True):
                return
        except Exception:
            pass
        db = await memory.Memory.get(self.agent)
