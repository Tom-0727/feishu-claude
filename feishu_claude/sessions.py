"""Per-runtime Claude session persistence and one-shot legacy migration."""

from __future__ import annotations

import json
from pathlib import Path

from .config import ServiceConfig


_LEGACY_STORE = Path.home() / ".feishu-claude" / "sessions.json"


class SessionStore:
    def __init__(self, path: Path) -> None:
        self._path = path

    def get(self) -> str | None:
        if not self._path.exists():
            return None
        try:
            data = json.loads(self._path.read_text(encoding="utf-8"))
        except Exception:
            return None
        value = data.get("session_id") if isinstance(data, dict) else None
        return value if isinstance(value, str) and value else None

    def save(self, session_id: str) -> None:
        self._path.parent.mkdir(parents=True, exist_ok=True)
        self._path.write_text(
            json.dumps({"session_id": session_id}, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )

    def clear(self) -> None:
        if self._path.exists():
            self._path.unlink()


def migrate_legacy_sessions(config: ServiceConfig) -> None:
    if not _LEGACY_STORE.exists():
        return
    try:
        legacy = json.loads(_LEGACY_STORE.read_text(encoding="utf-8"))
    except Exception:
        legacy = None
    if not isinstance(legacy, dict):
        _LEGACY_STORE.unlink()
        return

    for runtime_config in config.runtimes.values():
        session_id = legacy.get(runtime_config.chat_id)
        if not isinstance(session_id, str) or not session_id:
            continue
        store = SessionStore(runtime_config.session_path)
        if store.get():
            continue
        store.save(session_id)
        print(
            f"[migrate] runtime={runtime_config.runtime_id} "
            f"chat_id={runtime_config.chat_id} session_id={session_id}",
            flush=True,
        )

    _LEGACY_STORE.unlink()
    print("[migrate] removed legacy sessions.json", flush=True)
