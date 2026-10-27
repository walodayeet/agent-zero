from __future__ import annotations

import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[1]
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))

from plugins._model_config.helpers import model_config


def test_chat_fallback_configs_are_additive(monkeypatch):
    monkeypatch.setattr(model_config, "get_effective_preset_name", lambda _agent=None: "P")
    monkeypatch.setattr(
        model_config,
        "resolve_preset",
        lambda _name: {
            "name": "P",
            "fallback": [
                {"provider": "other", "name": "backup", "api_base": "http://x/v1"},
                "ignored",
                {"provider": "other", "name": "second"},
            ],
        },
    )
    assert [item["name"] for item in model_config.get_chat_fallback_configs(None)] == [
        "backup",
        "second",
    ]


def test_role_config_falls_back_to_chat(monkeypatch):
    monkeypatch.setattr(model_config, "get_effective_preset_name", lambda _agent=None: "P")
    monkeypatch.setattr(
        model_config,
        "resolve_preset",
        lambda _name: {
            "name": "P",
            "chat": {"provider": "other", "name": "chat"},
            "roles": {"vision": {"provider": "other", "name": "vision"}},
        },
    )
    monkeypatch.setattr(model_config, "get_chat_model_config", lambda _agent=None: {"name": "chat"})
    assert model_config.get_role_config(None, "vision")["name"] == "vision"
    assert model_config.get_role_config(None, "title")["name"] == "chat"


def test_update_preset_preserves_fallback(monkeypatch):
    preset = {"name": "P", "chat": {"name": "chat"}, "fallback": [{"name": "backup"}]}
    monkeypatch.setattr(model_config, "get_presets", lambda: [preset])
    monkeypatch.setattr(model_config, "save_presets", lambda _presets: None)
    monkeypatch.setattr(
        model_config,
        "config_to_preset",
        lambda _config, name: {"name": name, "chat": {"name": "new"}},
    )
    updated = model_config.update_preset_from_config("P", {})
    assert updated["fallback"] == [{"name": "backup"}]


def test_connection_errors_are_fallback_eligible():
    import httpx
    import openai
    from agent import _is_fallback_error

    request = httpx.Request("POST", "http://127.0.0.1:59999/v1")
    assert _is_fallback_error(openai.APIConnectionError(request=request))
    assert _is_fallback_error(ConnectionError())
    assert _is_fallback_error(type("HttpError", (), {"status_code": 402})())
    for status in (400, 404, 409):
        assert not _is_fallback_error(type("HttpError", (), {"status_code": status})())


def test_preset_clean_and_validate_round_trip_keep_extras():
    preset = {
        "name": "P",
        "chat": {"provider": "other", "name": "chat"},
        "fallback": [{"provider": "other", "name": "backup"}],
        "roles": {"vision": {"provider": "other", "name": "vision"}},
    }
    cleaned = model_config.clean_presets_for_file([preset])
    validated = model_config.validate_presets(cleaned, require_default=False)
    assert validated[0]["fallback"] == preset["fallback"]
    assert validated[0]["roles"] == preset["roles"]


def test_utility_and_vision_roles_override_slots(monkeypatch):
    preset = {
        "name": "P",
        "chat": {"name": "chat"},
        "utility": {"name": "utility"},
        "vision": {"name": "vision"},
        "roles": {
            "utility": {"name": "role-utility"},
            "vision": {"name": "role-vision"},
        },
    }
    monkeypatch.setattr(model_config, "get_effective_preset_name", lambda _agent=None: "P")
    monkeypatch.setattr(model_config, "resolve_preset", lambda _name: preset)
    monkeypatch.setattr(
        model_config,
        "get_effective_config",
        lambda _agent=None: {
            "chat_model": {"name": "chat"},
            "utility_model": {"name": "utility"},
            "vision_model": {"name": "vision"},
        },
    )
    assert model_config.get_utility_model_config()["name"] == "role-utility"
    assert model_config.get_vision_model_config()["name"] == "role-vision"
