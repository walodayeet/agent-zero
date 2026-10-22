"""One ready-to-use local terminal, so a chat's first command is instant.

PowerShell takes ~3 s to reach its prompt on Windows (bash ~0.3 s), and the code
execution tool paid that on the first command of every new chat. A spare shell is
connected in the background during the agent's model call; the tool adopts it and
a new spare is started for the next chat.
"""

from __future__ import annotations

import asyncio
import os
import time

from helpers import runtime
from plugins._code_execution.helpers.shell_local import LocalInteractiveSession

_MAX_SPARE_AGE_SECONDS = 1800.0

_spare: LocalInteractiveSession | None = None
_spare_created: float = 0.0
_spare_loop: asyncio.AbstractEventLoop | None = None
_task: asyncio.Task | None = None


def enabled() -> bool:
    if not runtime.is_local_runtime():
        return False
    from helpers import dotenv

    raw = str(dotenv.get_dotenv_value("A0_TERMINAL_PREWARM", "")).strip().lower()
    return raw not in ("0", "false", "no", "off")


def default_cwd() -> str:
    from helpers import files

    return files.get_abs_path("usr", "workdir")


def _same_path(left: str | None, right: str | None) -> bool:
    if not left or not right:
        return True
    # Resolve junctions/symlinks: the checkout's `usr` is a junction to the data
    # dir, so the same workdir has two textual paths.
    return os.path.normcase(os.path.realpath(left)) == os.path.normcase(os.path.realpath(right))


async def _discard_spare() -> None:
    global _spare
    session, _spare = _spare, None
    if session:
        await session.close()


async def _prewarm(cwd: str | None) -> None:
    global _spare, _spare_created, _spare_loop
    session = LocalInteractiveSession(cwd=cwd or default_cwd())
    try:
        await session.connect()
    except BaseException as exc:
        await session.close()
        return
    _spare = session
    _spare_created = time.monotonic()
    _spare_loop = asyncio.get_running_loop()


def ensure_prewarm(cwd: str | None = None) -> None:
    """Start (or keep) one spare shell on the current loop."""
    global _task, _spare_loop
    if not enabled():
        return
    loop = asyncio.get_running_loop()
    if _task is not None and not _task.done():
        return
    if _spare is not None:
        if _spare_loop is loop and not _spare.is_terminated():
            return
        # A spare from another loop (or a dead one) cannot be reused.
        loop.create_task(_discard_spare())
    if _spare_loop is not None and _spare_loop is not loop:
        _spare_loop = None
    _task = loop.create_task(_prewarm(cwd or default_cwd()))


async def prewarm_on_current_loop() -> None:
    """Entry point for callers that schedule onto the agent event loop."""
    ensure_prewarm()


async def take_prewarmed(cwd: str | None = None, timeout: float = 8.0) -> LocalInteractiveSession | None:
    """Adopt the spare shell, waiting for an in-flight prewarm if needed."""
    global _spare, _spare_created
    if not enabled():
        return None
    loop = asyncio.get_running_loop()
    task = _task
    if task is not None and not task.done():
        try:
            await asyncio.wait_for(asyncio.shield(task), timeout)
        except Exception:
            pass
    session = _spare
    created = _spare_created
    if session is None or _spare_loop is not loop:
        return None
    _spare = None
    _spare_created = 0.0
    if (
        session.is_terminated()
        or not _same_path(session.cwd, cwd)
        or (time.monotonic() - created) > _MAX_SPARE_AGE_SECONDS
    ):
        await session.close()
        return None
    return session
