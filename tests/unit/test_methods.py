"""protocol/methods.py 方法定义测试。"""

from awesome_claude.protocol.methods import (
    METHOD_CHAT,
    METHOD_ECHO,
    METHOD_PING,
    METHOD_SHUTDOWN,
    NOTIFY_CHAT_STREAM,
    ChatParams,
    StreamNotificationParams,
)
from awesome_claude.shared.types import ChatResponse


class TestMethodConstants:
    """方法名常量测试。"""

    def test_phase1_methods(self) -> None:
        assert METHOD_PING == "ping"
        assert METHOD_ECHO == "echo"
        assert METHOD_SHUTDOWN == "shutdown"

    def test_chat_method(self) -> None:
        assert METHOD_CHAT == "chat"

    def test_stream_notification(self) -> None:
        assert NOTIFY_CHAT_STREAM == "chat.stream"


class TestChatParams:
    """ChatParams 结构测试。"""

    def test_required_message_only(self) -> None:
        params: ChatParams = {"message": "hello"}
        assert params["message"] == "hello"
        assert "conversation_id" not in params
        assert "max_tokens" not in params

    def test_optional_fields(self) -> None:
        params: ChatParams = {
            "message": "hi",
            "conversation_id": "conv-1",
            "max_tokens": 2048,
        }
        assert params["conversation_id"] == "conv-1"
        assert params["max_tokens"] == 2048

    def test_optional_fields_null(self) -> None:
        params: ChatParams = {"message": "hi", "conversation_id": None}
        assert params["conversation_id"] is None


class TestStreamNotificationParams:
    """StreamNotificationParams 结构测试。"""

    def test_creation(self) -> None:
        params: StreamNotificationParams = {
            "task_id": "t1",
            "chunk_index": 0,
            "text": "增量文本",
            "is_final": False,
        }
        assert params["task_id"] == "t1"
        assert params["chunk_index"] == 0
        assert params["text"] == "增量文本"
        assert params["is_final"] is False

    def test_final_flag(self) -> None:
        params: StreamNotificationParams = {
            "task_id": "t1",
            "chunk_index": 5,
            "text": "",
            "is_final": True,
        }
        assert params["is_final"] is True


class TestChatReturnType:
    """chat 返回类型来自 shared.types。"""

    def test_chat_response_importable(self) -> None:
        assert ChatResponse is not None
