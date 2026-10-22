import os
import platform
import re
import select
import subprocess
import time
import sys
from typing import Optional, Tuple
from helpers import runtime
from plugins._code_execution.helpers import tty_session
from plugins._code_execution.helpers.shell_ssh import clean_string

_PROMPT_RE = re.compile(r"(?:PS\s+[^>]*>|[$#])\s*$")


def disable_pagers_in_env(env: dict | None = None) -> dict:
    """Return a copy of ``env`` with terminal pagers disabled.

    Commands such as ``git diff``/``git log`` detect a TTY and pipe their output
    through a pager (``more``/``less``). The non-interactive shells created by
    the code execution tool never receive any user input, so the pager blocks
    forever and spins at 100% CPU per process. Pointing the pager variables at
    ``cat`` lets the output stream through instead. See issue #1697.
    """
    env = dict(env if env is not None else os.environ)
    env["PAGER"] = "cat"
    env["GIT_PAGER"] = "cat"
    return env


class LocalInteractiveSession:
    def __init__(self, cwd: str|None = None):
        self.session: tty_session.TTYSession|None = None
        self.full_output = ''
        self.cwd = cwd

    def __del__(self):
        try:
            if self.session:
                self.session.kill()
        except Exception:
            pass

    async def connect(self):
        self.session = tty_session.TTYSession(
            runtime.get_terminal_executable(),
            cwd=self.cwd,
            env=disable_pagers_in_env(),
        )
        await self.session.start()
        await self._wait_for_prompt()

    async def _wait_for_prompt(self, timeout: float = 8.0) -> None:
        """Drain the shell banner until its prompt is ready.

        PowerShell needs ~1.5 s to initialise PSReadLine on Windows; a command
        sent before that waits for the shell to catch up (measured ~2.3 s for a
        first ``echo``). Return as soon as a prompt line appears, or fall back to
        the old idle drain.
        """
        buffer = ""
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            chunk = await self.session.read(timeout=0.25)
            if chunk is None:
                continue
            buffer = (buffer + chunk)[-4000:]
            lines = [line.strip() for line in buffer.splitlines()[-3:]]
            if any(_PROMPT_RE.search(line) for line in lines):
                return

    async def close(self):
        if self.session:
            session = self.session
            self.session = None
            try:
                await session.close()
            except Exception:
                try:
                    session.kill()
                except Exception:
                    pass

    async def send_command(self, command: str):
        if not self.session:
            raise Exception("Shell not connected")
        self.full_output = ""
        await self.session.sendline(command)

    def is_terminated(self) -> bool:
        return self.session is None or self.session.is_terminated()

    def get_exit_code(self) -> int | None:
        if not self.session:
            return None
        return self.session.get_exit_code()
 
    async def read_output(self, timeout: float = 0, reset_full_output: bool = False) -> Tuple[str, Optional[str]]:
        if not self.session:
            raise Exception("Shell not connected")

        if reset_full_output:
            self.full_output = ""

        # get output from terminal
        partial_output = await self.session.read_full_until_idle(idle_timeout=0.01, total_timeout=timeout)
        self.full_output += partial_output

        # clean output
        partial_output = clean_string(partial_output)
        clean_full_output = clean_string(self.full_output)

        if not partial_output:
            return clean_full_output, None
        return clean_full_output, partial_output
