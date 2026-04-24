"""Runtime objects that bind one Feishu chat to one Claude session."""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from .config import ClaudeRuntimeConfig
from .sessions import SessionStore


@dataclass
class FeishuClaudeRuntime:
    config: ClaudeRuntimeConfig
    sessions: SessionStore
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    @property
    def runtime_id(self) -> str:
        return self.config.runtime_id

    @property
    def chat_id(self) -> str:
        return self.config.chat_id

    @property
    def allowed_user_ids(self) -> frozenset[str]:
        return self.config.allowed_user_ids


def build_runtime(config: ClaudeRuntimeConfig) -> FeishuClaudeRuntime:
    return FeishuClaudeRuntime(
        config=config,
        sessions=SessionStore(config.session_path),
    )
