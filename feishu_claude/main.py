"""Entry point: start configured Feishu WebSocket long-connection bots."""

from __future__ import annotations

import argparse
import asyncio
import json
import signal
import threading
from concurrent.futures import Future
from pathlib import Path

import lark_oapi as lark
import lark_oapi.ws.client as lark_ws_client
from lark_oapi.api.im.v1 import P2ImMessageReceiveV1

from .bridge import handle_message
from .config import ServiceConfig, load_config
from .runtime import FeishuClaudeRuntime, build_runtime
from .sessions import migrate_legacy_sessions


class FeishuClaudeService:
    def __init__(self, config: ServiceConfig) -> None:
        self.config = config
        self._loop = asyncio.new_event_loop()
        self._loop_thread = threading.Thread(target=self._loop.run_forever, daemon=True)
        self._stop_event = threading.Event()
        self._stopping = False
        self._runtimes = {
            runtime_id: build_runtime(runtime_config)
            for runtime_id, runtime_config in config.runtimes.items()
        }
        self._routes: dict[tuple[str, str], FeishuClaudeRuntime] = {
            (runtime.config.app_key, runtime.chat_id): runtime
            for runtime in self._runtimes.values()
        }
        self._ws_clients: list[lark.ws.Client] = []

    def start(self) -> None:
        self._loop_thread.start()
        future = asyncio.run_coroutine_threadsafe(self._start_feishu_apps(), self._loop)
        future.result(timeout=60)
        print(
            f"Started feishu-claude with {len(self.config.apps)} Feishu app(s) "
            f"and {len(self._runtimes)} Claude runtime(s).",
            flush=True,
        )

    def wait(self) -> None:
        self._stop_event.wait()

    async def _start_feishu_apps(self) -> None:
        lark_ws_client.loop = asyncio.get_running_loop()
        for app in self.config.apps.values():
            api_client = lark.Client.builder().app_id(app.app_id).app_secret(app.app_secret).build()
            event_handler = self._build_event_handler(app.key, api_client)
            ws_client = lark.ws.Client(
                app.app_id,
                app.app_secret,
                event_handler=event_handler,
                log_level=lark.LogLevel.WARNING,
            )
            print(f"Starting Feishu app {app.key} (app_id={app.app_id[:8]}...).", flush=True)
            await ws_client._connect()
            self._loop.create_task(ws_client._ping_loop())
            self._ws_clients.append(ws_client)
            print(f"Connected Feishu app {app.key}.", flush=True)

    def shutdown(self) -> None:
        if self._stopping:
            return
        self._stopping = True
        future = asyncio.run_coroutine_threadsafe(self._stop_ws(), self._loop)
        try:
            future.result(timeout=15)
        except Exception as exc:
            print(f"[shutdown] ws cleanup failed: {exc}", flush=True)
        self._loop.call_soon_threadsafe(self._loop.stop)
        self._stop_event.set()

    def _build_event_handler(self, app_key: str, api_client: lark.Client) -> lark.EventDispatcherHandler:
        def on_message(data: P2ImMessageReceiveV1) -> None:
            msg = data.event.message
            if msg.message_type != "text":
                return

            runtime = self._routes.get((app_key, msg.chat_id))
            if runtime is None:
                return

            try:
                content = json.loads(msg.content)
            except json.JSONDecodeError:
                return
            text = content.get("text", "")
            if not isinstance(text, str):
                return
            text = text.strip()
            if not text:
                return

            sender = data.event.sender.sender_id
            sender_id = (
                getattr(sender, "open_id", None)
                or getattr(sender, "user_id", None)
                or getattr(sender, "union_id", None)
                or ""
            )

            future = asyncio.run_coroutine_threadsafe(
                handle_message(
                    runtime=runtime,
                    sender_id=sender_id,
                    text=text,
                    message_id=msg.message_id,
                    thread_id=msg.thread_id,
                    client=api_client,
                ),
                self._loop,
            )
            future.add_done_callback(lambda item: self._log_message_error(runtime, item))

        return (
            lark.EventDispatcherHandler.builder("", "")
            .register_p2_im_message_receive_v1(on_message)
            .build()
        )

    async def _stop_ws(self) -> None:
        for client in self._ws_clients:
            client._auto_reconnect = False
        await asyncio.gather(
            *(client._disconnect() for client in self._ws_clients),
            return_exceptions=True,
        )

    def _log_message_error(self, runtime: FeishuClaudeRuntime, future: Future[object]) -> None:
        try:
            future.result()
        except Exception as exc:
            print(f"[runtime:{runtime.runtime_id}] message handling failed: {exc}", flush=True)


_service: FeishuClaudeService | None = None


def _shutdown(signum: int, _frame: object) -> None:
    if _service is not None:
        _service.shutdown()
    raise SystemExit(128 + signum)


def main() -> None:
    args = _parse_args()
    config = load_config(args.config)
    migrate_legacy_sessions(config)

    global _service
    _service = FeishuClaudeService(config)
    signal.signal(signal.SIGINT, _shutdown)
    signal.signal(signal.SIGTERM, _shutdown)
    _service.start()
    try:
        _service.wait()
    finally:
        _service.shutdown()


def _parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run feishu-claude.")
    parser.add_argument(
        "--config",
        type=Path,
        default=Path("feishu-claude.yaml"),
        help="YAML config path. Defaults to ./feishu-claude.yaml.",
    )
    return parser.parse_args()


if __name__ == "__main__":
    main()
