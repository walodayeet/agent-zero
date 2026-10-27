import os
import re
from typing import Any

from .files import get_abs_path
from dotenv import load_dotenv as _load_dotenv

KEY_AUTH_LOGIN = "AUTH_LOGIN"
KEY_AUTH_PASSWORD = "AUTH_PASSWORD"
KEY_RFC_PASSWORD = "RFC_PASSWORD"
KEY_ROOT_PASSWORD = "ROOT_PASSWORD"

def load_dotenv():
    # Process environment wins over usr/.env: the launcher (run-baremetal.*)
    # derives values like ALLOWED_ORIGINS from the actual runtime, and a stale
    # .env line must not silently clobber it. Values absent from the process
    # environment are still loaded from the file.
    _load_dotenv(get_dotenv_file_path(), override=False)


def get_dotenv_file_path():
    return get_abs_path("usr/.env")

def get_dotenv_value(key: str, default: Any = None):
    # Reads os.environ only; the caller (an entrypoint) must have run load_dotenv().
    return os.getenv(key, default)

def save_dotenv_value(key: str, value: str, reload_env: bool = True):
    if value is None:
        value = ""
    dotenv_path = get_dotenv_file_path()
    if not os.path.isfile(dotenv_path):
        with open(dotenv_path, "w") as f:
            f.write("")
    with open(dotenv_path, "r+") as f:
        lines = f.readlines()
        found = False
        for i, line in enumerate(lines):
            if re.match(rf"^\s*{key}\s*=", line):
                lines[i] = f"{key}={value}\n"
                found = True
        if not found:
            lines.append(f"\n{key}={value}\n")
        f.seek(0)
        f.writelines(lines)
        f.truncate()
    if reload_env:
        # Set only the saved key so the change is visible immediately without
        # re-clobbering launcher-derived environment variables (see load_dotenv).
        os.environ[key] = value
