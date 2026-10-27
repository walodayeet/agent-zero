from __future__ import annotations

import asyncio
import sys
import time
import warnings
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from helpers import extension
from helpers.extension import Extension


class _Slow(Extension):
    cancelled = False

    async def execute(self, **_kwargs):
        try:
            await asyncio.sleep(5)
        finally:
            type(self).cancelled = True


def test_slow_hook_is_cancelled_and_reported(monkeypatch):
    monkeypatch.setenv("A0_EXTENSION_TIMEOUT", "0.05")
    monkeypatch.setattr(extension, "_get_extension_classes", lambda *a, **k: [_Slow])
    warnings_seen: list[str] = []
    monkeypatch.setattr(
        extension.PrintStyle, "warning", lambda message: warnings_seen.append(str(message))
    )
    _Slow.cancelled = False

    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        started = time.perf_counter()
        asyncio.run(extension.call_extensions_async("test_timeout"))
        elapsed = time.perf_counter() - started

    assert elapsed < 0.5
    assert _Slow.cancelled is True
    assert warnings_seen and "exceeded" in warnings_seen[0]
    assert not [item for item in caught if issubclass(item.category, RuntimeWarning)]
