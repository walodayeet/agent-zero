"""Shared test fixtures."""

import os

import pytest

_PLAYWRIGHT_ENV_KEY = "PLAYWRIGHT_BROWSERS_PATH"


@pytest.fixture(autouse=True)
def _restore_playwright_browsers_path():
    """Production code may point the Playwright cache at the A0 runtime dir.

    That mutation is process-global, so a test that exercises the install path with
    patched directories would otherwise leave later browser tests looking at a
    temporary directory.
    """
    original = os.environ.get(_PLAYWRIGHT_ENV_KEY)
    yield
    if original is None:
        os.environ.pop(_PLAYWRIGHT_ENV_KEY, None)
    else:
        os.environ[_PLAYWRIGHT_ENV_KEY] = original
