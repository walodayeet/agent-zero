import asyncio

import pytest

from plugins._code_execution.helpers import shell_pool


class FakeSession:
    instances: list["FakeSession"] = []

    def __init__(self, cwd=None):
        self.cwd = cwd
        self.closed = False
        self.terminated = False
        self.connect_delay = 0.01
        FakeSession.instances.append(self)

    async def connect(self):
        await asyncio.sleep(self.connect_delay)

    async def close(self):
        self.closed = True

    def is_terminated(self):
        return self.terminated


@pytest.fixture(autouse=True)
def _reset_pool(monkeypatch):
    FakeSession.instances = []
    monkeypatch.setattr(shell_pool, "enabled", lambda: True)
    monkeypatch.setattr(shell_pool, "LocalInteractiveSession", FakeSession)
    shell_pool._spare = None
    shell_pool._spare_created = 0.0
    shell_pool._spare_loop = None
    shell_pool._task = None
    yield
    shell_pool._spare = None
    shell_pool._spare_loop = None
    shell_pool._task = None


def test_spare_is_adopted_without_connecting_again():
    async def run():
        shell_pool.ensure_prewarm("C:/work")
        adopted = await shell_pool.take_prewarmed("C:/work")
        assert isinstance(adopted, FakeSession)
        assert not adopted.closed
        assert len(FakeSession.instances) == 1

    asyncio.run(run())


def test_take_waits_for_an_inflight_boot(monkeypatch):
    class SlowSession(FakeSession):
        def __init__(self, cwd=None):
            super().__init__(cwd)
            self.connect_delay = 0.15

    monkeypatch.setattr(shell_pool, "LocalInteractiveSession", SlowSession)

    async def run():
        loop = asyncio.get_running_loop()
        started = loop.time()
        shell_pool.ensure_prewarm("C:/work")
        adopted = await shell_pool.take_prewarmed("C:/work")
        assert isinstance(adopted, SlowSession)
        assert loop.time() - started >= 0.12

    asyncio.run(run())


def test_path_mismatch_discards_the_spare():
    async def run():
        shell_pool.ensure_prewarm("C:/one")
        assert await shell_pool.take_prewarmed("C:/two") is None
        assert FakeSession.instances[0].closed

    asyncio.run(run())


def test_terminated_spare_is_discarded():
    async def run():
        shell_pool.ensure_prewarm("C:/work")
        await asyncio.sleep(0.05)
        shell_pool._spare.terminated = True
        assert await shell_pool.take_prewarmed("C:/work") is None
        assert FakeSession.instances[0].closed

    asyncio.run(run())


def test_disabled_pool_does_nothing(monkeypatch):
    monkeypatch.setattr(shell_pool, "enabled", lambda: False)

    async def run():
        shell_pool.ensure_prewarm("C:/work")
        assert await shell_pool.take_prewarmed("C:/work") is None
        assert shell_pool._task is None

    asyncio.run(run())
