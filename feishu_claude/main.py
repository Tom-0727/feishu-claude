"""
Entry point: start Feishu WebSocket long-connection bot.
"""

import asyncio
import json
import os
import threading

import lark_oapi as lark
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1
from dotenv import load_dotenv

from .bridge import handle_message

load_dotenv()

APP_ID = os.environ["FEISHU_APP_ID"]
APP_SECRET = os.environ["FEISHU_APP_SECRET"]

# Dedicated event loop for async Claude calls (runs in background thread)
_loop = asyncio.new_event_loop()
threading.Thread(target=_loop.run_forever, daemon=True).start()


def _on_message(data: P2ImMessageReceiveV1) -> None:
    msg = data.event.message
    # Only handle text messages
    if msg.message_type != "text":
        return
    text = json.loads(msg.content).get("text", "").strip()
    if not text:
        return

    chat_id = msg.chat_id
    sender = data.event.sender.sender_id
    sender_id = getattr(sender, "open_id", None) or getattr(sender, "user_id", None) or getattr(sender, "union_id", None) or ""

    # Submit async work to the dedicated event loop
    asyncio.run_coroutine_threadsafe(
        handle_message(
            chat_id=chat_id,
            sender_id=sender_id,
            text=text,
            message_id=msg.message_id,
            thread_id=msg.thread_id,
            client=_api_client,
        ),
        _loop,
    )


_api_client = lark.Client.builder().app_id(APP_ID).app_secret(APP_SECRET).build()

_event_handler = (
    lark.EventDispatcherHandler.builder("", "")
    .register_p2_im_message_receive_v1(_on_message)
    .build()
)


def main() -> None:
    print(f"Starting feishu-claude bot (app_id={APP_ID[:8]}…)", flush=True)
    ws = lark.ws.Client(
        APP_ID,
        APP_SECRET,
        event_handler=_event_handler,
        log_level=lark.LogLevel.INFO,
    )
    ws.start()  # blocking


if __name__ == "__main__":
    main()
