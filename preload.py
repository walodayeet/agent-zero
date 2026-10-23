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
        # Vector-store initialization remains first-use lazy: its current API does
        # synchronous disk/model work and is not single-flight across event loops.
        await warm_skills()
        PrintStyle().print("Preload completed.")
    except Exception as e:
        PrintStyle().error(f"Error in preload: {e}")


async def warm_skills():
    """Parse the skill catalog off the event loop.

    The first system-prompt build otherwise parses every SKILL.md frontmatter
    (~0.3-0.8 s). Runs even when A0_PRELOAD=0, where it is the only startup warm.
    """
    try:
        from helpers import skills as skills_helper

        await asyncio.to_thread(skills_helper.list_skills)
    except Exception:
        pass


async def warmup_llm():
    """Import LiteLLM off the event loop, in its own deferred task.

    Kept separate from ``preload()`` so the embedding/vector-store task still
    completes (and reports readiness) without waiting for the LLM stack.
    """
    try:
        from helpers import litellm_transport

        await asyncio.to_thread(litellm_transport.warmup)
    except Exception:
        pass


# preload the configured embedding model
if __name__ == "__main__":
    PrintStyle().print("Running preload...")
    runtime.initialize()
    asyncio.run(preload())
