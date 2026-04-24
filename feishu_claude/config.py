"""YAML configuration for feishu-claude."""

from __future__ import annotations

import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml


_PERMISSION_MODE_VALUES = {"default", "acceptEdits", "plan", "bypassPermissions"}
_DEFAULT_ALLOWED_TOOLS: tuple[str, ...] = (
    "Read",
    "Edit",
    "Write",
    "Bash",
    "Glob",
    "Grep",
    "Skill",
)


@dataclass(frozen=True)
class FeishuAppConfig:
    key: str
    app_id: str
    app_secret: str


@dataclass(frozen=True)
class ClaudeRuntimeConfig:
    runtime_id: str
    app_key: str
    chat_id: str
    allowed_user_ids: frozenset[str]
    session_path: Path
    cwd: Path
    allowed_tools: tuple[str, ...]
    permission_mode: str


@dataclass(frozen=True)
class ServiceConfig:
    apps: dict[str, FeishuAppConfig]
    runtimes: dict[str, ClaudeRuntimeConfig]


def load_config(path: str | Path) -> ServiceConfig:
    config_path = Path(path).expanduser()
    with config_path.open("r", encoding="utf-8") as file:
        raw = yaml.safe_load(file)

    root = _mapping(raw, "config")
    apps = _load_apps(root.get("apps"))
    runtimes = _load_runtimes(root.get("runtimes"), apps)
    return ServiceConfig(apps=apps, runtimes=runtimes)


def _load_apps(raw: object) -> dict[str, FeishuAppConfig]:
    apps_raw = _mapping(raw, "apps")
    if not apps_raw:
        raise ValueError("apps must define at least one Feishu app")

    apps: dict[str, FeishuAppConfig] = {}
    for key, value in apps_raw.items():
        app_key = _key(key, "app key")
        item = _mapping(value, f"apps.{app_key}")
        apps[app_key] = FeishuAppConfig(
            key=app_key,
            app_id=_required_str(item, "app_id", f"apps.{app_key}"),
            app_secret=_required_str(item, "app_secret", f"apps.{app_key}"),
        )
    return apps


def _load_runtimes(raw: object, apps: dict[str, FeishuAppConfig]) -> dict[str, ClaudeRuntimeConfig]:
    runtimes_raw = _mapping(raw, "runtimes")
    if not runtimes_raw:
        raise ValueError("runtimes must define at least one Claude runtime")

    routes: set[tuple[str, str]] = set()
    runtimes: dict[str, ClaudeRuntimeConfig] = {}
    for key, value in runtimes_raw.items():
        runtime_id = _key(key, "runtime id")
        item = _mapping(value, f"runtimes.{runtime_id}")
        app_key = _required_str(item, "app", f"runtimes.{runtime_id}")
        if app_key not in apps:
            raise ValueError(f"runtimes.{runtime_id}.app references unknown app {app_key!r}")

        chat_id = _required_str(item, "chat_id", f"runtimes.{runtime_id}")
        route = (app_key, chat_id)
        if route in routes:
            raise ValueError(f"duplicate runtime route for app {app_key!r} and chat_id {chat_id!r}")
        routes.add(route)

        claude = _mapping(item.get("claude"), f"runtimes.{runtime_id}.claude")
        cwd = _directory(
            _required_str(claude, "cwd", f"runtimes.{runtime_id}.claude"),
            f"runtimes.{runtime_id}.claude.cwd",
        )
        permission_mode = _enum(
            _optional_str(claude, "permission_mode", "acceptEdits", f"runtimes.{runtime_id}.claude"),
            _PERMISSION_MODE_VALUES,
            f"runtimes.{runtime_id}.claude.permission_mode",
        )
        allowed_tools = _allowed_tools(
            claude.get("allowed_tools"),
            f"runtimes.{runtime_id}.claude.allowed_tools",
        )

        runtimes[runtime_id] = ClaudeRuntimeConfig(
            runtime_id=runtime_id,
            app_key=app_key,
            chat_id=chat_id,
            allowed_user_ids=frozenset(
                _str_list(item.get("allowed_user_ids"), f"runtimes.{runtime_id}.allowed_user_ids")
            ),
            session_path=_session_path(runtime_id, item.get("session_path")),
            cwd=cwd,
            allowed_tools=allowed_tools,
            permission_mode=permission_mode,
        )
    return runtimes


def _mapping(value: object, name: str) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError(f"{name} must be a mapping")
    return value


def _key(value: object, name: str) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name} must be a non-empty string")
    return value.strip()


def _required_str(item: dict[str, Any], key: str, name: str) -> str:
    value = item.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}.{key} must be a non-empty string")
    return value.strip()


def _optional_str(item: dict[str, Any], key: str, default: str, name: str) -> str:
    value = item.get(key, default)
    if value is None:
        return default
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{name}.{key} must be a non-empty string")
    return value.strip()


def _enum(value: str, allowed: set[str], name: str) -> str:
    if value not in allowed:
        choices = ", ".join(sorted(allowed))
        raise ValueError(f"{name} must be one of: {choices}")
    return value


def _str_list(value: object, name: str) -> list[str]:
    if value is None:
        return []
    if not isinstance(value, list):
        raise ValueError(f"{name} must be a list of strings")
    result: list[str] = []
    for index, item in enumerate(value):
        if not isinstance(item, str) or not item.strip():
            raise ValueError(f"{name}[{index}] must be a non-empty string")
        result.append(item.strip())
    return result


def _allowed_tools(value: object, name: str) -> tuple[str, ...]:
    if value is None:
        return _DEFAULT_ALLOWED_TOOLS
    return tuple(_str_list(value, name))


def _path(value: str) -> Path:
    return Path(value).expanduser()


def _directory(value: str, name: str) -> Path:
    path = _path(value)
    if not path.is_dir():
        raise ValueError(f"{name} must be an existing directory")
    return path


def _session_path(runtime_id: str, value: object) -> Path:
    if value is not None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"runtimes.{runtime_id}.session_path must be a non-empty string")
        return _path(value.strip())

    safe_id = re.sub(r"[^A-Za-z0-9_.-]+", "_", runtime_id).strip("._") or "runtime"
    return Path.home() / ".feishu-claude" / "runtimes" / safe_id / "session.json"
