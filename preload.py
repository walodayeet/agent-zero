import asyncio
from helpers import runtime
from helpers.print_style import PrintStyle
import models


async def preload():
    try:
        from plugins._model_config.helpers.model_config import get_embedding_model_config_object

        emb_cfg = get_embedding_model_config_object()
        if emb_cfg.provider.lower() == "huggingface":
            # Model imports and construction must not block the shared background loop.
            emb_mod = await asyncio.to_thread(
                models.get_embedding_model,
                emb_cfg.provider,
                emb_cfg.name,
                model_config=emb_cfg,
                **emb_cfg.build_kwargs(),
            )
            await emb_mod.aembed_query("test")
        # Warm the default vector store too: the first recall otherwise pays for
        # the FAISS index load plus the knowledge checksum scan (~2.6 s on Windows).
        # A0_PRELOAD_MEMORY=0 keeps that work on first use.
        if runtime.preload_memory_enabled():
            try:
                from plugins._memory.helpers.memory import Memory

                await Memory.get_by_subdir("default")
            except Exception:
                pass
        PrintStyle().print("Preload completed.")
    except Exception as e:
        PrintStyle().error(f"Error in preload: {e}")


# preload the configured embedding model
if __name__ == "__main__":
    PrintStyle().print("Running preload...")
    runtime.initialize()
    asyncio.run(preload())
