"""Core bridge: receive a Feishu message, call Claude Agent SDK, reply back."""

from __future__ import annotations

import json
from typing import Any

import lark_oapi as lark
from lark_oapi.api.im.v1 import (
    CreateMessageRequest,
    CreateMessageRequestBody,
    CreateMessageReactionRequest,
    CreateMessageReactionRequestBody,
    DeleteMessageReactionRequest,
    ReplyMessageRequest,
    ReplyMessageRequestBody,
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

from .runtime import FeishuClaudeRuntime


async def handle_message(
    runtime: FeishuClaudeRuntime,
    sender_id: str,
    text: str,
    message_id: str,
    thread_id: str | None,
    client: lark.Client,
) -> None:
    if runtime.allowed_user_ids and sender_id not in runtime.allowed_user_ids:
        return

    print(
        f"[runtime:{runtime.runtime_id}] "
        f"sender_id={sender_id} message_id={message_id} thread_id={thread_id or '-'}",
        flush=True,
    )

    command = text.strip()
    if command == "/reset":
        async with runtime.lock:
            runtime.sessions.clear()
        _send_reply(
            client=client,
            chat_id=runtime.chat_id,
            message_id=message_id,
            text="✅ 对话已重置，开始新会话。",
            reply_in_thread=bool(thread_id),
        )
        return

    if command == "/compact":
        try:
            async with runtime.lock:
                await _run_compact(
                    runtime=runtime,
                    message_id=message_id,
                    reply_in_thread=bool(thread_id),
                    client=client,
                )
        except Exception as e:
            print(f"[runtime:{runtime.runtime_id}] compact error: {e}", flush=True)
            _send_reply(
                client=client,
                chat_id=runtime.chat_id,
                message_id=message_id,
                text=f"❌ compact 出错：{e}",
                reply_in_thread=bool(thread_id),
            )
        return

    try:
        async with runtime.lock:
            await _run_claude(
                runtime=runtime,
                text=text,
                message_id=message_id,
                reply_in_thread=bool(thread_id),
                client=client,
            )
    except Exception as e:
        print(f"[runtime:{runtime.runtime_id}] unhandled error: {e}", flush=True)
        _send_reply(
            client=client,
            chat_id=runtime.chat_id,
            message_id=message_id,
            text=f"❌ 内部错误：{e}",
            reply_in_thread=bool(thread_id),
        )


async def _run_claude(
    runtime: FeishuClaudeRuntime,
    text: str,
    message_id: str,
    reply_in_thread: bool,
    client: lark.Client,
) -> None:
    session_id = runtime.sessions.get()
    new_session_id: str | None = None
    final_text = ""
    stderr_lines: list[str] = []

    reaction_id = _add_reaction(client, message_id, "Typing")

    gen = query(
        prompt=text,
        options=ClaudeAgentOptions(
            resume=session_id,
            cwd=str(runtime.config.cwd),
            allowed_tools=list(runtime.config.allowed_tools),
            permission_mode=runtime.config.permission_mode,
            stderr=lambda line: _collect_stderr(stderr_lines, line),
            extra_args={"debug-to-stderr": None},
            env={"CLAUDECODE": ""},
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
        _send_reply(
            client=client,
            chat_id=runtime.chat_id,
            message_id=message_id,
            text=f"❌ 出错了：{e}。{details}",
            reply_in_thread=reply_in_thread,
        )
        return
    finally:
        try:
            await gen.aclose()
        except Exception:
            pass

    if new_session_id:
        print(
            f"[runtime:{runtime.runtime_id}] session_id={new_session_id}",
            flush=True,
        )
        runtime.sessions.save(new_session_id)

    if reaction_id:
        _remove_reaction(client, message_id, reaction_id)
    reply_text = final_text or "(无回复)"
    print(
        f"[runtime:{runtime.runtime_id}] reply chars={len(reply_text)}",
        flush=True,
    )
    _send_reply(
        client=client,
        chat_id=runtime.chat_id,
        message_id=message_id,
        text=reply_text,
        reply_in_thread=reply_in_thread,
    )


async def _run_compact(
    runtime: FeishuClaudeRuntime,
    message_id: str,
    reply_in_thread: bool,
    client: lark.Client,
) -> None:
    session_id = runtime.sessions.get()
    if not session_id:
        _send_reply(
            client=client,
            chat_id=runtime.chat_id,
            message_id=message_id,
            text="ℹ️ 当前还没有会话，无需压缩。",
            reply_in_thread=reply_in_thread,
        )
        return

    stderr_lines: list[str] = []
    boundary_seen = False
    new_session_id: str | None = None
    result_subtype: str | None = None

    reaction_id = _add_reaction(client, message_id, "Typing")

    gen = query(
        prompt="/compact",
        options=ClaudeAgentOptions(
            resume=session_id,
            cwd=str(runtime.config.cwd),
            allowed_tools=[],
            max_turns=1,
            permission_mode="bypassPermissions",
            stderr=lambda line: _collect_stderr(stderr_lines, line),
            extra_args={"debug-to-stderr": None},
            env={"CLAUDECODE": ""},
        ),
    )
    try:
        async for msg in gen:
            if isinstance(msg, SystemMessage) and msg.subtype == "compact_boundary":
                boundary_seen = True
            elif isinstance(msg, ResultMessage):
                result_subtype = getattr(msg, "subtype", None)
                new_session_id = getattr(msg, "session_id", None) or new_session_id
    except Exception as e:
        details = _format_stderr(stderr_lines)
        if reaction_id:
            _remove_reaction(client, message_id, reaction_id)
        _send_reply(
            client=client,
            chat_id=runtime.chat_id,
            message_id=message_id,
            text=f"❌ compact 出错：{e}。{details}",
            reply_in_thread=reply_in_thread,
        )
        return
    finally:
        try:
            await gen.aclose()
        except Exception:
            pass

    if reaction_id:
        _remove_reaction(client, message_id, reaction_id)

    ok = boundary_seen and result_subtype == "success"
    if ok:
        if new_session_id:
            print(
                f"[runtime:{runtime.runtime_id}] compact session_id={new_session_id}",
                flush=True,
            )
            runtime.sessions.save(new_session_id)
        _send_reply(
            client=client,
            chat_id=runtime.chat_id,
            message_id=message_id,
            text="✅ 已完成 compact。",
            reply_in_thread=reply_in_thread,
        )
    else:
        details = _format_stderr(stderr_lines)
        _send_reply(
            client=client,
            chat_id=runtime.chat_id,
            message_id=message_id,
            text=(
                f"❌ compact 失败（boundary={boundary_seen}, "
                f"result={result_subtype or 'none'}）。{details}"
            ),
            reply_in_thread=reply_in_thread,
        )


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
        print(f"[add_reaction] error {resp.code}: {resp.msg}", flush=True)
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
        print(f"[remove_reaction] error {resp.code}: {resp.msg}", flush=True)


def _send_text(client: lark.Client, receive_id_type: str, receive_id: str, text: str) -> bool:
    req = (
        CreateMessageRequest.builder()
        .receive_id_type(receive_id_type)
        .request_body(
            CreateMessageRequestBody.builder()
            .receive_id(receive_id)
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .build()
        )
        .build()
    )
    resp = client.im.v1.message.create(req)
    if not resp.success():
        print(
            f"[send_text] error receive_id_type={receive_id_type} "
            f"receive_id={receive_id} code={resp.code}: {resp.msg}",
            flush=True,
        )
        return False
    print(
        f"[send_text] ok receive_id_type={receive_id_type} receive_id={receive_id} "
        f"message_id={resp.data.message_id}",
        flush=True,
    )
    return True


def _send_reply(
    client: lark.Client,
    chat_id: str,
    message_id: str,
    text: str,
    reply_in_thread: bool,
) -> None:
    req = (
        ReplyMessageRequest.builder()
        .message_id(message_id)
        .request_body(
            ReplyMessageRequestBody.builder()
            .msg_type("text")
            .content(json.dumps({"text": text}, ensure_ascii=False))
            .reply_in_thread(reply_in_thread)
            .build()
        )
        .build()
    )
    resp = client.im.v1.message.reply(req)
    if not resp.success():
        print(f"[send_reply] error {resp.code}: {resp.msg}", flush=True)
        _send_text(client, "chat_id", chat_id, text)
        return
    print(
        f"[send_reply] ok parent_message_id={message_id} "
        f"message_id={resp.data.message_id}",
        flush=True,
    )


def _extract_session_id(msg: Any) -> str | None:
    if isinstance(msg, ResultMessage):
        return getattr(msg, "session_id", None)
    if isinstance(msg, SystemMessage):
        return getattr(msg, "session_id", None)
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
