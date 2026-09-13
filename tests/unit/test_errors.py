"""protocol/errors.py 错误码与错误响应构造测试。"""

from awesome_claude.protocol.errors import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    LLM_AUTH_ERROR,
    LLM_ERROR,
    LLM_TIMEOUT_ERROR,
    PARSE_ERROR,
    SESSION_BUSY,
    build_error_response,
)


class TestErrorCodes:
    """错误码常量测试。"""

    def test_standard_codes(self) -> None:
        assert PARSE_ERROR == -32700
        assert INVALID_PARAMS == -32602
        assert INTERNAL_ERROR == -32603

    def test_llm_codes(self) -> None:
        assert LLM_ERROR == -32001
        assert LLM_AUTH_ERROR == -32002
        assert LLM_TIMEOUT_ERROR == -32003

    def test_session_busy_code(self) -> None:
        assert SESSION_BUSY == -32004


class TestBuildErrorResponse:
    """build_error_response 测试。"""

    def test_shape_without_data(self) -> None:
        resp = build_error_response(7, SESSION_BUSY, "会话忙")
        assert resp == {
            "jsonrpc": "2.0",
            "error": {"code": -32004, "message": "会话忙"},
            "id": 7,
        }

    def test_shape_with_data(self) -> None:
        resp = build_error_response(None, INTERNAL_ERROR, "boom", {"k": 1})
        assert resp["id"] is None
        assert resp["error"]["data"] == {"k": 1}
