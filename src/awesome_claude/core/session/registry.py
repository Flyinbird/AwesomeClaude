"""会话注册表 - 连接端点、逻辑会话与多连接扇出。"""

from collections.abc import Awaitable, Callable
from typing import Any

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
    """逻辑会话：订阅连接集合、对话历史与关联任务。"""

    def __init__(self, session_id: str) -> None:
        """初始化会话。

        Args:
            session_id: 会话标识。
        """
        self.session_id = session_id
        self.sinks: set[ConnectionSink] = set()
        self.history: list[dict[str, Any]] = []
        self.task_ids: set[str] = set()


class SessionRegistry:
    """会话注册表，管理会话的创建、订阅与广播。"""

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

    def get_or_create(self, session_id: str) -> Session:
        """按 ID 获取会话，不存在时创建。

        Args:
            session_id: 会话标识。

        Returns:
            现有或新建的会话。
        """
        session = self._sessions.get(session_id)
        if session is None:
            session = Session(session_id)
            self._sessions[session_id] = session
        return session

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

    def detach(self, sink: ConnectionSink) -> None:
        """从所有会话中移除该连接端点。

        Args:
            sink: 连接端点。
        """
        for session in self._sessions.values():
            session.sinks.discard(sink)

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
        """返回会话回放状态（历史与活跃任务）。

        Args:
            session_id: 会话标识。

        Returns:
            含 session_id、history、active_tasks 的状态字典。
        """
        session = self._sessions.get(session_id)
        if session is None:
            return {"session_id": session_id, "history": [], "active_tasks": []}
        return {
            "session_id": session.session_id,
            "history": list(session.history),
            "active_tasks": list(session.task_ids),
        }
