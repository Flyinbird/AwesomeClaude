"""客户端传输层测试：入站活动时间与连接关闭回调。"""

import asyncio
from collections.abc import Callable

from awesome_claude.client.transport.receiver import MessageReceiver
from awesome_claude.protocol.jsonrpc import build_notification, encode_message


async def _wait_for(predicate: Callable[[], bool], timeout: float = 1.0) -> None:
    deadline = asyncio.get_running_loop().time() + timeout
    while asyncio.get_running_loop().time() < deadline:
        if predicate():
            return
        await asyncio.sleep(0.01)
    raise AssertionError("等待条件超时")


class TestReceiverActivity:
    """last_activity 随入站消息更新。"""

    async def test_activity_updates_on_message(self) -> None:
        reader = asyncio.StreamReader()
        receiver = MessageReceiver(reader)
        await receiver.start()
        try:
            before = receiver.last_activity
            await asyncio.sleep(0.01)
            reader.feed_data(
                encode_message(build_notification("chat.heartbeat", {"run_id": "r"}))
            )
            await _wait_for(lambda: receiver.last_activity > before)
        finally:
            await receiver.stop()

    async def test_close_handler_fires_on_eof(self) -> None:
        reader = asyncio.StreamReader()
        receiver = MessageReceiver(reader)
        closed = asyncio.Event()

        async def on_close() -> None:
            closed.set()

        receiver.on_close(on_close)
        await receiver.start()
        reader.feed_eof()
        await asyncio.wait_for(closed.wait(), timeout=1.0)

    async def test_stop_does_not_fire_close_handler(self) -> None:
        reader = asyncio.StreamReader()
        receiver = MessageReceiver(reader)
        closed = asyncio.Event()

        async def on_close() -> None:
            closed.set()

        receiver.on_close(on_close)
        await receiver.start()
        await receiver.stop()
        assert not closed.is_set()
