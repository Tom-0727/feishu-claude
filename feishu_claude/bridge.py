"""
Core bridge: receive a Feishu message, call Claude Agent SDK, reply back.
"""

import asyncio
import json
import os
from collections import defaultdict
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    DeleteMessageReactionRequest,
)
from lark_oapi.api.im.v1.model.emoji import Emoji
from claude_agent_sdk import (
    query,
    ClaudeAgentOptions,
    ResultMessage,
    SystemMessage,
    AssistantMessage,
    TextBlock,
    ToolUseBlock,
)

from . import sessions

# Per-chat lock: prevents overlapping Claude calls for the same conversation
_chat_locks: dict[str, asyncio.Lock] = defaultdict(asyncio.Lock)

ALLOWED_USER_IDS: set[str] = set(
    uid.strip()
    for uid in os.getenv("ALLOWED_USER_IDS", "").split(",")
    if uid.strip()
)
CLAUDE_CWD = os.getenv("CLAUDE_CWD") or os.path.expanduser("~")


async def handle_message(chat_id: str, sender_id: str, text: str, message_id: str, client: lark.Client) -> None:
    # Access control
    if ALLOWED_USER_IDS and sender_id not in ALLOWED_USER_IDS:
        return

    # Built-in commands
    if text.strip() == "/reset":
        async with _chat_locks[chat_id]:
            sessions.clear(chat_id)
        _send_text(client, chat_id, "✅ 对话已重置，开始新会话。")
        return

    try:
        async with _chat_locks[chat_id]:
            await _run_claude(chat_id, text, message_id, client)
    except Exception as e:
        print(f"[handle_message] unhandled error: {e}")
        _send_text(client, chat_id, f"❌ 内部错误：{e}")


async def _run_claude(chat_id: str, text: str, message_id: str, client: lark.Client) -> None:
    session_id = sessions.get(chat_id)
    new_session_id: str | None = None
    final_text = ""
    stderr_lines: list[str] = []

    reaction_id = _add_reaction(client, message_id, "Typing")

    gen = query(
        prompt=text,
        options=ClaudeAgentOptions(
            resume=session_id,
            cwd=CLAUDE_CWD,
            allowed_tools=["Read", "Edit", "Write", "Bash", "Glob", "Grep", "Skill"],
            permission_mode="acceptEdits",
            stderr=lambda line: _collect_stderr(stderr_lines, line),
            extra_args={"debug-to-stderr": None},
            env={"CLAUDECODE": ""},  # allow running inside an existing Claude Code session
        ),
    )
    try:
        async for msg in gen:
            new_session_id = _extract_session_id(msg) or new_session_id

            if isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        continue
                    if isinstance(block, TextBlock) and block.text:
                        final_text = block.text
            elif isinstance(msg, ResultMessage):
                final_text = msg.result
    except Exception as e:
        details = _format_stderr(stderr_lines)
        if reaction_id:
            _remove_reaction(client, message_id, reaction_id)
        _send_text(client, chat_id, f"❌ 出错了：{e}。{details}")
        return
    finally:
        try:
            await gen.aclose()
        except Exception:
            pass

    if new_session_id:
        sessions.save(chat_id, new_session_id)

    if reaction_id:
        _remove_reaction(client, message_id, reaction_id)
    _send_text(client, chat_id, final_text or "(无回复)")


def _add_reaction(client: lark.Client, message_id: str, emoji_type: str) -> str | None:
    req = (
        CreateMessageReactionRequest.builder()
        .message_id(message_id)
        .request_body(
            CreateMessageReactionRequestBody.builder()
            .reaction_type(Emoji.builder().emoji_type(emoji_type).build())
            .build()
        )
        .build()
    )
    resp = client.im.v1.message_reaction.create(req)
    if not resp.success():
        print(f"[add_reaction] error {resp.code}: {resp.msg}")
        return None
    return resp.data.reaction_id


def _remove_reaction(client: lark.Client, message_id: str, reaction_id: str) -> None:
    req = (
        DeleteMessageReactionRequest.builder()
        .message_id(message_id)
        .reaction_id(reaction_id)
        .build()
    )
    resp = client.im.v1.message_reaction.delete(req)
    if not resp.success():
        print(f"[remove_reaction] error {resp.code}: {resp.msg}")


def _send_text(client: lark.Client, chat_id: str, text: str) -> None:
    req = (
        CreateMessageRequest.builder()
        .receive_id_type("chat_id")
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(chat_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        .build()
    )
    resp = client.im.v1.message.create(req)
    if not resp.success():
        print(f"[send_text] error {resp.code}: {resp.msg}")


def _extract_session_id(msg: Any) -> str | None:
    if isinstance(msg, ResultMessage):
        return msg.session_id
    if isinstance(msg, SystemMessage):
        return msg.data.get("session_id")
    return getattr(msg, "session_id", None)


def _collect_stderr(stderr_lines: list[str], line: str) -> None:
    if not line:
        return
    stderr_lines.append(line)
    if len(stderr_lines) > 20:
        del stderr_lines[:-20]


def _format_stderr(stderr_lines: list[str]) -> str:
    if not stderr_lines:
        return "请查看服务端日志。"
    last_line = stderr_lines[-1]
    return f"最后日志：{last_line}"
