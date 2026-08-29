"""core/session 会话注册表与通道测试。"""

from typing import Any

from awesome_claude.core.session.channel import SessionChannel
from awesome_claude.core.session.registry import ConnectionSink, SessionRegistry


class Recorder:
    """记录通知的替身发送端点。"""

    def __init__(self) -> None:
        self.sent: list[tuple[str, dict[str, Any]]] = []

    async def send(self, method: str, params: dict[str, Any]) -> None:
        self.sent.append((method, params))


class TestSessionRegistry:
    """SessionRegistry 创建、订阅、广播与状态测试。"""

    async def test_get_or_create(self) -> None:
        reg = SessionRegistry()
        first = reg.get_or_create("abc")
        second = reg.get_or_create("abc")
        assert first is second
        assert reg.get("abc") is first
        assert reg.get("nope") is None

    async def test_attach_adds_sink(self) -> None:
        reg = SessionRegistry()
        sink = ConnectionSink(Recorder().send)
        session = reg.attach("abc", sink)
        assert sink in session.sinks

    async def test_detach_removes_sink_from_all(self) -> None:
        reg = SessionRegistry()
        sink = ConnectionSink(Recorder().send)
        reg.attach("a", sink)
        reg.attach("b", sink)
        reg.detach(sink)
        assert sink not in reg.get("a").sinks
        assert sink not in reg.get("b").sinks

    async def test_broadcast_to_all_sinks(self) -> None:
        reg = SessionRegistry()
        r1 = Recorder()
        r2 = Recorder()
        reg.attach("abc", ConnectionSink(r1.send))
        reg.attach("abc", ConnectionSink(r2.send))
        await reg.broadcast("abc", "m", {"k": 1})
        assert r1.sent == [("m", {"k": 1})]
        assert r2.sent == [("m", {"k": 1})]

    async def test_broadcast_exclude(self) -> None:
        reg = SessionRegistry()
        r1 = Recorder()
        r2 = Recorder()
        sink1 = ConnectionSink(r1.send)
        reg.attach("abc", sink1)
        reg.attach("abc", ConnectionSink(r2.send))
        await reg.broadcast("abc", "m", {}, exclude=sink1)
        assert r1.sent == []
        assert r2.sent == [("m", {})]

    async def test_broadcast_missing_session_noop(self) -> None:
        reg = SessionRegistry()
        await reg.broadcast("nope", "m", {})

    async def test_state(self) -> None:
        reg = SessionRegistry()
        session = reg.get_or_create("abc")
        session.history.append({"role": "user"})
        session.task_ids.add("t1")
        state = reg.state("abc")
        assert state["session_id"] == "abc"
        assert state["history"] == [{"role": "user"}]
        assert state["active_tasks"] == ["t1"]

    async def test_state_missing_returns_empty(self) -> None:
        reg = SessionRegistry()
        assert reg.state("nope") == {
            "session_id": "nope",
            "history": [],
            "active_tasks": [],
        }


class TestSessionChannel:
    """SessionChannel 单播/广播切换与历史记录测试。"""

    async def test_broadcast_unicast_before_attach(self) -> None:
        r = Recorder()
        channel = SessionChannel(ConnectionSink(r.send), SessionRegistry())
        await channel.broadcast("m", {"k": 1})
        assert r.sent == [("m", {"k": 1})]

    async def test_attach_then_broadcast_fans_out(self) -> None:
        reg = SessionRegistry()
        r1 = Recorder()
        r2 = Recorder()
        ch1 = SessionChannel(ConnectionSink(r1.send), reg)
        ch2 = SessionChannel(ConnectionSink(r2.send), reg)
        ch1.attach("abc")
        ch2.attach("abc")
        await ch1.broadcast("m", {"k": 1})
        assert r1.sent == [("m", {"k": 1})]
        assert r2.sent == [("m", {"k": 1})]

    async def test_broadcast_exclude_self(self) -> None:
        reg = SessionRegistry()
        r1 = Recorder()
        r2 = Recorder()
        ch1 = SessionChannel(ConnectionSink(r1.send), reg)
        ch2 = SessionChannel(ConnectionSink(r2.send), reg)
        ch1.attach("abc")
        ch2.attach("abc")
        await ch1.broadcast("m", {}, exclude_self=True)
        assert r1.sent == []
        assert r2.sent == [("m", {})]

    async def test_attach_returns_state(self) -> None:
        reg = SessionRegistry()
        channel = SessionChannel(ConnectionSink(Recorder().send), reg)
        state = channel.attach("abc")
        assert state["session_id"] == "abc"
        assert channel.session_id == "abc"

    async def test_detach_restores_unicast(self) -> None:
        reg = SessionRegistry()
        r1 = Recorder()
        r2 = Recorder()
        ch1 = SessionChannel(ConnectionSink(r1.send), reg)
        ch2 = SessionChannel(ConnectionSink(r2.send), reg)
        ch1.attach("abc")
        ch2.attach("abc")
        ch1.detach()
        await ch1.broadcast("m", {})
        assert r2.sent == []
        assert r1.sent == [("m", {})]

    async def test_record_turn_appends_history(self) -> None:
        reg = SessionRegistry()
        channel = SessionChannel(ConnectionSink(Recorder().send), reg)
        channel.attach("abc")
        channel.record_turn("hi", "hello", "task1")
        session = reg.get("abc")
        assert session is not None
        assert session.history == [
            {"role": "user", "content": "hi", "task_id": "task1"},
            {"role": "assistant", "content": "hello", "task_id": "task1"},
        ]
        assert session.task_ids == {"task1"}

    async def test_record_turn_noop_without_attach(self) -> None:
        reg = SessionRegistry()
        channel = SessionChannel(ConnectionSink(Recorder().send), reg)
        channel.record_turn("hi", "hello", "task1")
        assert reg.get("abc") is None
