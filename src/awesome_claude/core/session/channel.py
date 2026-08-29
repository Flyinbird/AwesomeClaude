"""会话通道 - 每个连接的会话门面，屏蔽扇出与单播的差异。"""

from typing import Any

from awesome_claude.core.session.registry import ConnectionSink, SessionRegistry


class SessionChannel:
    """每个连接一个的会话门面。

    维护当前连接订阅的 session_id；broadcast 在已订阅时扇出到整个会话，
    未订阅时退化为单播回本连接，保证向后兼容（ping/echo 等无会话场景）。
    """

    def __init__(self, sink: ConnectionSink, registry: SessionRegistry) -> None:
        """初始化通道。

        Args:
            sink: 本连接的发送端点。
            registry: 全局会话注册表。
        """
        self._sink = sink
        self._registry = registry
        self._session_id: str | None = None

    @property
    def session_id(self) -> str | None:
        """当前订阅的会话 ID（未订阅时为 None）。"""
        return self._session_id

    def attach(self, session_id: str) -> dict[str, Any]:
        """订阅到指定会话并返回回放状态。

        Args:
            session_id: 会话标识。

        Returns:
            会话回放状态字典。
        """
        self._registry.attach(session_id, self._sink)
        self._session_id = session_id
        return self._registry.state(session_id)

    def detach(self) -> None:
        """取消订阅当前会话。"""
        if self._session_id is not None:
            self._registry.detach(self._sink)
            self._session_id = None

    async def broadcast(
        self, method: str, params: dict[str, Any], *, exclude_self: bool = False
    ) -> None:
        """广播通知：已订阅时扇出到会话，否则单播回本连接。

        Args:
            method: 通知方法名。
            params: 通知参数。
            exclude_self: 已订阅时是否排除本连接（默认 False）。
        """
        if self._session_id is not None:
            await self._registry.broadcast(
                self._session_id,
                method,
                params,
                exclude=self._sink if exclude_self else None,
            )
        else:
            await self._sink.send(method, params)

    def record_turn(self, user_message: str, assistant_text: str, task_id: str) -> None:
        """将一轮对话追加到会话历史（供回放与 Phase 4 Memory 复用）。

        Args:
            user_message: 用户输入。
            assistant_text: assistant 最终回复文本。
            task_id: 本轮任务 ID。
        """
        session = self._registry.get(self._session_id) if self._session_id else None
        if session is None:
            return
        session.history.append(
            {"role": "user", "content": user_message, "task_id": task_id}
        )
        session.history.append(
            {"role": "assistant", "content": assistant_text, "task_id": task_id}
        )
        session.task_ids.add(task_id)
