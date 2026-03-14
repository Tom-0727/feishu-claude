"""
Core bridge: receive a Feishu message, call Claude Agent SDK, reply back.
"""

import asyncio
import json
import os
from collections import defaultdict

import lark_oapi as lark
from lark_oapi.api.im.v1 import CreateMessageRequest, CreateMessageRequestBody
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


async def handle_message(chat_id: str, sender_id: str, text: str, client: lark.Client) -> None:
    # Access control
    if ALLOWED_USER_IDS and sender_id not in ALLOWED_USER_IDS:
        return

    # Built-in commands
    if text.strip() == "/reset":
        sessions.clear(chat_id)
        _send_text(client, chat_id, "✅ 对话已重置，开始新会话。")
        return

    async with _chat_locks[chat_id]:
        await _run_claude(chat_id, text, client)


async def _run_claude(chat_id: str, text: str, client: lark.Client) -> None:
    session_id = sessions.get(chat_id)
    new_session_id: str | None = None
    final_text = ""
    tool_calls: list[str] = []

    # Show "thinking" indicator
    _send_text(client, chat_id, "⏳ 思考中…")

    try:
        async for msg in query(
            prompt=text,
            options=ClaudeAgentOptions(
                resume=session_id,
                cwd=CLAUDE_CWD,
                allowed_tools=["Read", "Edit", "Write", "Bash", "Glob", "Grep"],
                permission_mode="acceptEdits",
            ),
        ):
            # Capture session id on first message
            if isinstance(msg, SystemMessage) and msg.subtype == "init":
                new_session_id = msg.data.get("session_id")

            # Collect intermediate tool calls for progress display
            elif isinstance(msg, AssistantMessage):
                for block in msg.content:
                    if isinstance(block, ToolUseBlock):
                        tool_calls.append(block.name)
                        # Show progress every time a new tool is called
                        _send_text(client, chat_id, f"🔧 {block.name}…")
                    elif isinstance(block, TextBlock) and block.text:
                        final_text = block.text

            # Final result
            elif isinstance(msg, ResultMessage):
                final_text = msg.result

    except Exception as e:
        _send_text(client, chat_id, f"❌ 出错了：{e}")
        return

    if new_session_id:
        sessions.save(chat_id, new_session_id)

    _send_text(client, chat_id, final_text or "(无回复)")


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
