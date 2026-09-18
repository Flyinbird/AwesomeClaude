"""会话注册表 - 连接端点、逻辑会话、在途 Run 与多连接扇出。"""

import asyncio
from collections.abc import Awaitable, Callable
from typing import Any

from awesome_claude.core.session.run import Run, RunState
from awesome_claude.shared.logging.app_logger import get_app_logger

type SendFn = Callable[[str, dict[str, Any]], Awaitable[None]]


class ConnectionSink:
    """封装单个客户端连接的发送端点。"""

    def __init__(self, send: SendFn) -> None:
        """初始化连接端点。

        Args:
            send: 向该连接写入 JSON-RPC notification 的异步函数。
        """
        self._send = send

    async def send(self, method: str, params: dict[str, Any]) -> None:
        """向该连接发送一条 notification。

        Args:
            method: 通知方法名。
            params: 通知参数。
        """
        await self._send(method, params)


class Session:
    """逻辑会话：订阅连接集合、对话历史与在途 Run。

    Attributes:
        session_id: 会话标识。
        sinks: 订阅该会话的连接端点集合。
        history: 对话历史（用户/助手轮次）。
        active_run: 当前在途 Run（至多一个）。
        ephemeral: 是否为临时会话（无订阅时销毁，不保留历史）。
    """

    def __init__(self, session_id: str, *, ephemeral: bool = False) -> None:
        """初始化会话。

        Args:
            session_id: 会话标识。
            ephemeral: 是否为服务端生成的临时会话。
        """
        self.session_id = session_id
        self.sinks: set[ConnectionSink] = set()
        self.history: list[dict[str, Any]] = []
        self.active_run: Run | None = None
        self.ephemeral = ephemeral


class SessionRegistry:
    """会话注册表，管理会话的创建、订阅、在途 Run 与广播。"""

    def __init__(self) -> None:
        """初始化空注册表。"""
        self._sessions: dict[str, Session] = {}
        self._logger = get_app_logger("core.session")

    def get(self, session_id: str) -> Session | None:
        """按 ID 查找会话。

        Args:
            session_id: 会话标识。

        Returns:
            对应会话，不存在时返回 None。
        """
        return self._sessions.get(session_id)

    def get_or_create(self, session_id: str, *, ephemeral: bool = False) -> Session:
        """按 ID 获取会话，不存在时创建。

        Args:
            session_id: 会话标识。
            ephemeral: 新建会话是否为临时会话（已存在时忽略）。

        Returns:
            现有或新建的会话。
        """
        session = self._sessions.get(session_id)
        if session is None:
            session = Session(session_id, ephemeral=ephemeral)
            self._sessions[session_id] = session
        return session

    def discard(self, session_id: str) -> None:
        """从注册表移除会话（临时会话销毁用）。

        Args:
            session_id: 会话标识。
        """
        self._sessions.pop(session_id, None)

    def attach(self, session_id: str, sink: ConnectionSink) -> Session:
        """将连接端点订阅到会话（会话不存在则创建）。

        Args:
            session_id: 会话标识。
            sink: 连接端点。

        Returns:
            目标会话。
        """
        session = self.get_or_create(session_id)
        session.sinks.add(sink)
        return session

    async def detach(self, sink: ConnectionSink) -> None:
        """从所有会话移除该连接端点，并处理变为空置的会话。

        会话因移除而变为零订阅时：取消其活跃 Run；若为临时会话则销毁。

        Args:
            sink: 连接端点。
        """
        emptied: list[Session] = []
        for session in list(self._sessions.values()):
            if sink in session.sinks:
                session.sinks.discard(sink)
                if not session.sinks:
                    emptied.append(session)
        for session in emptied:
            await self._on_session_empty(session)

    async def _on_session_empty(self, session: Session) -> None:
        """处理零订阅会话：取消在途 Run 并在临时会话时销毁。"""
        run = session.active_run
        if run is not None and run.is_active:
            await self.cancel_run(run)
        if session.ephemeral:
            self._sessions.pop(session.session_id, None)

    async def cancel_run(self, run: Run) -> None:
        """取消一个在途 Run，等待任务落地并记录终态。

        由未被取消的上下文调用（连接断开 / 服务端关闭）。已终态则无操作。

        Args:
            run: 待取消的 Run。
        """
        if run.is_terminal:
            return
        run.cancel()
        if run.task is not None:
            await asyncio.gather(run.task, return_exceptions=True)
        if not run.is_terminal:
            run.finish(RunState.CANCELLED)
        if run.state is RunState.CANCELLED and run.recorder is not None:
            await run.recorder.run_cancelled({"session_id": run.session_id})
        session = self._sessions.get(run.session_id)
        if session is not None and session.active_run is run:
            session.active_run = None

    async def cancel_all_active_runs(self) -> None:
        """取消所有会话中的在途 Run（服务端优雅退出用）。"""
        for session in list(self._sessions.values()):
            run = session.active_run
            if run is not None and run.is_active:
                await self.cancel_run(run)

    async def broadcast(
        self,
        session_id: str,
        method: str,
        params: dict[str, Any],
        *,
        exclude: ConnectionSink | None = None,
    ) -> None:
        """向会话内所有连接端点扇出通知，单个端点失败不影响其余。

        Args:
            session_id: 会话标识。
            method: 通知方法名。
            params: 通知参数。
            exclude: 需跳过的连接端点（可选，如排除消息发起方）。
        """
        session = self._sessions.get(session_id)
        if session is None:
            return
        for sink in list(session.sinks):
            if exclude is not None and sink is exclude:
                continue
            try:
                await sink.send(method, params)
            except Exception:
                self._logger.exception(
                    "broadcast to sink failed", session_id=session_id
                )

    def state(self, session_id: str) -> dict[str, Any]:
        """返回会话回放状态（历史与在途 Run）。

        Args:
            session_id: 会话标识。

        Returns:
            含 session_id、history、active_runs 的状态字典。active_runs
            仅包含当前在途 Run 的标识，已完成/已取消的不出现。
        """
        session = self._sessions.get(session_id)
        if session is None:
            return {"session_id": session_id, "history": [], "active_runs": []}
        active_runs: list[str] = []
        if session.active_run is not None and session.active_run.is_active:
            active_runs.append(session.active_run.run_id)
        return {
            "session_id": session.session_id,
            "history": list(session.history),
            "active_runs": active_runs,
        }
