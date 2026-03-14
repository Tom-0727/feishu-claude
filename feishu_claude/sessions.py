"""
Persist chat_id -> claude_session_id mappings to disk.
Each Feishu chat gets its own Claude session for continuous conversation.
"""

import json
from pathlib import Path

_STORE = Path.home() / ".feishu-claude" / "sessions.json"


def _load() -> dict[str, str]:
    if not _STORE.exists():
        return {}
    try:
        return json.loads(_STORE.read_text())
    except Exception:
        return {}


def get(chat_id: str) -> str | None:
    return _load().get(chat_id)


def save(chat_id: str, session_id: str) -> None:
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    data = _load()
    data[chat_id] = session_id
    _STORE.write_text(json.dumps(data, indent=2, ensure_ascii=False))


def clear(chat_id: str) -> None:
    """Reset a chat's session (start fresh conversation)."""
    data = _load()
    data.pop(chat_id, None)
    _STORE.parent.mkdir(parents=True, exist_ok=True)
    _STORE.write_text(json.dumps(data, indent=2, ensure_ascii=False))
