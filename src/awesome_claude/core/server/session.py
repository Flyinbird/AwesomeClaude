"""会话管理 - 客户端连接读循环、请求分发与在途 Run 编排。"""

import asyncio
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from typing import Any

from awesome_claude.core.observability.trace_recorder import TraceRecorder
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.core.router.dispatcher import Dispatcher
from awesome_claude.core.session.channel import SessionChannel
from awesome_claude.core.session.registry import (
    ConnectionSink,
    Session,
    SessionRegistry,
)
from awesome_claude.core.session.run import Run, RunInitiator, RunState
from awesome_claude.protocol.errors import (
    INTERNAL_ERROR,
    SESSION_BUSY,
    build_error_response,
)
from awesome_claude.protocol.jsonrpc import (
    INVALID_REQUEST,
    JsonRpcProtocolError,
    JsonRpcRequest,
    build_error,
    build_notification,
    build_response,
    decode_message,
    encode_message,
    parse_message,
)
from awesome_claude.protocol.methods import METHOD_CHAT, METHOD_SHUTDOWN
from awesome_claude.shared.logging.app_logger import get_app_logger

type ContextFactory = Callable[[SessionChannel], HandlerContext]


class ClientSession:
    """管理单个客户端连接的读写、消息分发与上下文。

    读循环与控制类请求解耦：控制类请求（ping / echo / session.* /
    shutdown）按到达顺序内联处理；仅 chat 作为可追踪的 Run 异步执行，
    使读循环在对话期间仍能受理消息并及时感知断开。
    """

    def __init__(
        self,
        reader: asyncio.StreamReader,
        writer: asyncio.StreamWriter,
        dispatcher: Dispatcher,
        context_factory: ContextFactory,
        registry: SessionRegistry,
        on_shutdown: Callable[[], None] | None = None,
    ) -> None:
        """初始化会话。

        Args:
            reader: 连接读流。
            writer: 连接写流。
            dispatcher: 请求分发器。
            context_factory: 为每个连接创建 HandlerContext 的工厂（注入 SessionChannel）。
            registry: 会话注册表。
            on_shutdown: shutdown 通知触发时的回调（默认无）。
        """
        self._reader = reader
        self._writer = writer
        self._dispatcher = dispatcher
        self._context_factory = context_factory
        self._registry = registry
        self._on_shutdown = on_shutdown
        self._addr = writer.get_extra_info("peername")
        self._logger = get_app_logger("core.session")
        self._sink: ConnectionSink | None = None
        self._channel: SessionChannel | None = None
        self._runs: set[Run] = set()
        self._send_lock = asyncio.Lock()
        self._closed = False

    async def handle_connection(self) -> None:
        """主循环：读取 JSON-RPC 消息并逐条处理。"""
        sink = ConnectionSink(self._send_notification)
        channel = SessionChannel(sink, self._registry)
        context = self._context_factory(channel)
        self._sink = sink
        self._channel = channel
        self._logger.info("client connected", addr=self._addr)
        try:
            while True:
                raw = await self._reader.readline()
                if not raw:
                    break
                line = raw.strip()
                if not line:
                    continue
                if not await self._process_line(line, context):
                    break
        except (ConnectionError, OSError):
            self._logger.info("connection dropped", addr=self._addr)
        except Exception:
            self._logger.exception("session crashed", addr=self._addr)
        finally:
            self._closed = True
            try:
                await channel.detach()
            except Exception:
                self._logger.debug("detach failed", addr=self._addr, exc_info=True)
            for run in list(self._runs):
                if run.is_active:
                    await self._cancel_run_if_unobserved(run)
            self._writer.close()
            try:
                await self._writer.wait_closed()
            except Exception:
                self._logger.debug(
                    "error closing writer", addr=self._addr, exc_info=True
                )
            self._logger.info("client disconnected", addr=self._addr)

    async def _cancel_run_if_unobserved(self, run: Run) -> None:
        """若 Run 所属会话已无订阅者则取消它。"""
        session = self._registry.get(run.session_id)
        if session is None or not session.sinks:
            await self._registry.cancel_run(run)

    async def _process_line(self, line: bytes, context: HandlerContext) -> bool:
        """解析并处理一行消息；返回 False 表示应结束连接。"""
        try:
            data = decode_message(line)
            parsed = parse_message(data)
        except JsonRpcProtocolError as exc:
            await self._send(build_error(exc.code, str(exc), id=None))
            return True

        if not isinstance(parsed, JsonRpcRequest):
            await self._send(
                build_error(INVALID_REQUEST, "服务端只接收请求消息", id=None)
            )
            return True

        if parsed.method == METHOD_CHAT:
            await self._start_chat(parsed, context)
            return True

        return await self._dispatch_inline(parsed, context)

    async def _dispatch_inline(
        self, parsed: JsonRpcRequest, context: HandlerContext
    ) -> bool:
        """内联执行控制类请求并按序回写响应。"""
        request_id = parsed.id
        try:
            result = await self._dispatcher.dispatch(
                parsed.method, parsed.params, context
            )
        except Exception:
            self._logger.exception("dispatch failed for method %s", parsed.method)
            result = build_error_response(None, INTERNAL_ERROR, "Internal error")

        await self._send_result(result, request_id)

        if parsed.method == METHOD_SHUTDOWN and self._on_shutdown is not None:
            self._on_shutdown()
            return False
        return True

    async def _start_chat(
        self, parsed: JsonRpcRequest, context: HandlerContext
    ) -> None:
        """受理 chat：校验、解析会话、检查忙闲并作为 Run 异步执行。"""
        params = parsed.params
        if not isinstance(params, dict) or "message" not in params:
            await self._dispatch_inline(parsed, context)
            return
        assert self._channel is not None

        session_id = params.get("session_id")
        if not isinstance(session_id, str) or not session_id:
            session_id = f"ephemeral-{uuid.uuid4().hex[:8]}"
            session = self._registry.get_or_create(session_id, ephemeral=True)
        else:
            session = self._registry.get_or_create(session_id)
            if self._channel.session_id != session_id:
                self._channel.attach(session_id)

        existing = session.active_run
        if existing is not None and existing.is_active:
            await self._send(
                build_error_response(
                    parsed.id, SESSION_BUSY, "会话已有在途对话，请稍后重试"
                )
            )
            return

        run_id = uuid.uuid4().hex[:8]
        start_time = time.monotonic()
        recorder = TraceRecorder(context.trace_store, run_id, start_time)
        run = Run(
            run_id=run_id,
            session_id=session_id,
            start_time=start_time,
            initiator=RunInitiator(request_id=parsed.id, sink=self._sink),
            recorder=recorder,
        )
        session.active_run = run

        try:
            await recorder.run_created(
                {
                    "user_input": str(params["message"]),
                    "client_addr": str(self._addr),
                }
            )
        except Exception:
            self._logger.exception("create trace recorder failed")
            if session.active_run is run:
                session.active_run = None
            if session.ephemeral:
                self._registry.discard(session.session_id)
            await self._send(
                build_error_response(parsed.id, INTERNAL_ERROR, "任务创建失败")
            )
            return

        task = asyncio.create_task(self._execute_chat(parsed, context, run, session))
        run.set_task(task)
        self._runs.add(run)
        task.add_done_callback(lambda _task: self._runs.discard(run))

    async def _execute_chat(
        self,
        parsed: JsonRpcRequest,
        context: HandlerContext,
        run: Run,
        session: Session,
    ) -> None:
        """执行对话 Run 并回写最终响应，保证会话引用被清理。"""
        cancelled = False
        try:
            if run.is_terminal:
                return
            request_context = replace(context, run=run)
            try:
                result = await self._dispatcher.dispatch(
                    parsed.method, parsed.params, request_context
                )
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception("dispatch failed for method %s", parsed.method)
                result = build_error_response(None, INTERNAL_ERROR, "Internal error")
            await self._send_result(result, parsed.id)
        except asyncio.CancelledError:
            cancelled = True
            raise
        finally:
            if session.active_run is run:
                session.active_run = None
            if session.ephemeral:
                self._registry.discard(session.session_id)
            if not cancelled and not run.is_terminal:
                run.finish(RunState.FAILED)

    async def _send_result(self, result: dict[str, Any], request_id: Any) -> None:
        """将 handler 结果包装为响应并发送（notification 请求不回写）。"""
        if request_id is None:
            return
        if isinstance(result, dict) and "error" in result:
            response = dict(result)
            response["id"] = request_id
        else:
            response = build_response(result, id=request_id)
        await self._send(response)

    async def _send_notification(self, method: str, params: dict[str, Any]) -> None:
        """构造 JSON-RPC notification 并写入 writer。"""
        await self._send(build_notification(method, params))

    async def _send(self, msg: dict[str, Any]) -> None:
        """串行化编码并发送消息；连接已关闭时安全丢弃。"""
        async with self._send_lock:
            if self._closed:
                return
            try:
                self._writer.write(encode_message(msg))
                await self._writer.drain()
            except (ConnectionError, OSError):
                self._closed = True
                self._logger.debug("send dropped: connection closed", addr=self._addr)
