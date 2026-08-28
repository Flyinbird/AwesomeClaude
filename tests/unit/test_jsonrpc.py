"""JSON-RPC 2.0 消息层单元测试。"""

from typing import Any

import pytest

from awesome_claude.protocol.errors import (
    LLM_AUTH_ERROR,
    LLM_ERROR,
    LLM_TIMEOUT_ERROR,
    build_error_response,
)
from awesome_claude.protocol.jsonrpc import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    INVALID_REQUEST,
    METHOD_NOT_FOUND,
    PARSE_ERROR,
    JsonRpcDecodeError,
    JsonRpcError,
    JsonRpcErrorDetail,
    JsonRpcProtocolError,
    JsonRpcRequest,
    JsonRpcResponse,
    JsonRpcValidationError,
    build_error,
    build_notification,
    build_request,
    build_response,
    decode_message,
    encode_message,
    parse_message,
)
from awesome_claude.protocol.methods import NOTIFY_CHAT_STREAM


class TestBuild:
    """构造函数的输出结构测试。"""

    def test_build_request_with_params_and_id(self) -> None:
        msg = build_request("echo", params={"text": "hi"}, id=1)
        assert msg == {
            "jsonrpc": "2.0",
            "method": "echo",
            "params": {"text": "hi"},
            "id": 1,
        }

    def test_build_request_no_params(self) -> None:
        assert build_request("echo", id="abc") == {
            "jsonrpc": "2.0",
            "method": "echo",
            "id": "abc",
        }

    def test_build_request_no_id_omits_id(self) -> None:
        msg = build_request("echo", params=[])
        assert msg == {"jsonrpc": "2.0", "method": "echo", "params": []}
        assert "id" not in msg

    def test_build_request_id_zero(self) -> None:
        assert build_request("m", id=0)["id"] == 0

    def test_build_response(self) -> None:
        assert build_response("ok", id=1) == {"jsonrpc": "2.0", "result": "ok", "id": 1}

    def test_build_response_null_result(self) -> None:
        assert build_response(None, id=1)["result"] is None

    def test_build_error(self) -> None:
        msg = build_error(METHOD_NOT_FOUND, "Method not found", id=1)
        assert msg == {
            "jsonrpc": "2.0",
            "error": {"code": -32601, "message": "Method not found"},
            "id": 1,
        }

    def test_build_error_with_data(self) -> None:
        msg = build_error(INVALID_PARAMS, "Invalid params", data={"detail": "x"}, id=1)
        assert msg["error"]["data"] == {"detail": "x"}

    def test_build_error_no_data_omits_data(self) -> None:
        msg = build_error(INVALID_REQUEST, "Invalid request")
        assert "data" not in msg["error"]

    def test_build_error_null_id(self) -> None:
        msg = build_error(PARSE_ERROR, "Parse error")
        assert msg["id"] is None

    def test_build_notification(self) -> None:
        msg = build_notification("ping", params={"v": 1})
        assert msg == {"jsonrpc": "2.0", "method": "ping", "params": {"v": 1}}
        assert "id" not in msg

    def test_build_notification_no_params(self) -> None:
        assert build_notification("ping") == {"jsonrpc": "2.0", "method": "ping"}

    def test_build_notification_with_dict_params_has_no_id(self) -> None:
        params = {
            "task_id": "t1",
            "chunk_index": 0,
            "text": "hi",
            "is_final": False,
        }
        msg = build_notification(NOTIFY_CHAT_STREAM, params)
        assert msg == {
            "jsonrpc": "2.0",
            "method": NOTIFY_CHAT_STREAM,
            "params": params,
        }
        assert "id" not in msg


class TestParseMessage:
    """消息自动类型识别测试。"""

    def test_parse_request(self) -> None:
        msg = parse_message(
            {"jsonrpc": "2.0", "method": "echo", "params": {"t": "hi"}, "id": 5}
        )
        assert isinstance(msg, JsonRpcRequest)
        assert msg.jsonrpc == "2.0"
        assert msg.method == "echo"
        assert msg.params == {"t": "hi"}
        assert msg.id == 5

    def test_parse_request_no_params(self) -> None:
        msg = parse_message({"jsonrpc": "2.0", "method": "echo", "id": 5})
        assert isinstance(msg, JsonRpcRequest)
        assert msg.params is None

    def test_parse_request_list_params(self) -> None:
        msg = parse_message(
            {"jsonrpc": "2.0", "method": "echo", "params": [1, 2], "id": 5}
        )
        assert msg.params == [1, 2]

    def test_parse_notification(self) -> None:
        msg = parse_message({"jsonrpc": "2.0", "method": "notify"})
        assert isinstance(msg, JsonRpcRequest)
        assert msg.id is None

    def test_parse_notification_explicit_null_id(self) -> None:
        msg = parse_message({"jsonrpc": "2.0", "method": "notify", "id": None})
        assert msg.id is None

    def test_parse_notification_versus_request(self) -> None:
        stream_params = {
            "task_id": "t1",
            "chunk_index": 1,
            "text": "hi",
            "is_final": False,
        }
        notification = parse_message(
            {"jsonrpc": "2.0", "method": NOTIFY_CHAT_STREAM, "params": stream_params}
        )
        assert isinstance(notification, JsonRpcRequest)
        assert notification.method == NOTIFY_CHAT_STREAM
        assert notification.params == stream_params
        assert notification.id is None

        request = parse_message(
            {
                "jsonrpc": "2.0",
                "method": NOTIFY_CHAT_STREAM,
                "params": stream_params,
                "id": 7,
            }
        )
        assert isinstance(request, JsonRpcRequest)
        assert request.id == 7

    def test_parse_id_zero(self) -> None:
        msg = parse_message({"jsonrpc": "2.0", "method": "m", "id": 0})
        assert msg.id == 0

    def test_parse_id_string(self) -> None:
        msg = parse_message({"jsonrpc": "2.0", "method": "m", "id": "abc"})
        assert msg.id == "abc"

    def test_parse_response(self) -> None:
        msg = parse_message({"jsonrpc": "2.0", "result": "ok", "id": 1})
        assert isinstance(msg, JsonRpcResponse)
        assert msg.result == "ok"
        assert msg.id == 1

    def test_parse_response_null_result(self) -> None:
        msg = parse_message({"jsonrpc": "2.0", "result": None, "id": 1})
        assert isinstance(msg, JsonRpcResponse)
        assert msg.result is None

    def test_parse_error(self) -> None:
        msg = parse_message(
            {
                "jsonrpc": "2.0",
                "error": {"code": METHOD_NOT_FOUND, "message": "Method not found"},
                "id": 1,
            }
        )
        assert isinstance(msg, JsonRpcError)
        assert isinstance(msg.error, JsonRpcErrorDetail)
        assert msg.error.code == -32601
        assert msg.error.message == "Method not found"
        assert msg.error.data is None
        assert msg.id == 1

    def test_parse_error_with_data(self) -> None:
        msg = parse_message(
            {
                "jsonrpc": "2.0",
                "error": {"code": -32000, "message": "bad", "data": {"x": 1}},
                "id": 1,
            }
        )
        assert msg.error.data == {"x": 1}

    def test_parse_error_null_id(self) -> None:
        msg = parse_message(
            {
                "jsonrpc": "2.0",
                "error": {"code": -32700, "message": "Parse error"},
                "id": None,
            }
        )
        assert msg.id is None


class TestParseInvalid:
    """非法消息的校验测试。"""

    @pytest.mark.parametrize(
        "data",
        [
            {"method": "echo", "id": 1},
            {"jsonrpc": "1.0", "method": "echo", "id": 1},
            {"jsonrpc": 2.0, "method": "echo", "id": 1},
            {"jsonrpc": "2.0"},
            {"jsonrpc": "2.0", "id": 1},
            {"jsonrpc": "2.0", "method": "echo", "result": 1, "id": 1},
            {
                "jsonrpc": "2.0",
                "method": "echo",
                "error": {"code": 1, "message": "m"},
                "id": 1,
            },
        ],
    )
    def test_invalid_message(self, data: dict[str, Any]) -> None:
        with pytest.raises(JsonRpcValidationError):
            parse_message(data)

    def test_method_missing(self) -> None:
        with pytest.raises(JsonRpcValidationError):
            parse_message({"jsonrpc": "2.0", "id": 1})

    def test_method_not_string(self) -> None:
        with pytest.raises(JsonRpcValidationError):
            parse_message({"jsonrpc": "2.0", "method": 123, "id": 1})

    def test_method_empty_string(self) -> None:
        with pytest.raises(JsonRpcValidationError):
            parse_message({"jsonrpc": "2.0", "method": "", "id": 1})

    @pytest.mark.parametrize("bad_id", [True, False, 1.5, {"a": 1}, [1, 2]])
    def test_invalid_id(self, bad_id: Any) -> None:
        with pytest.raises(JsonRpcValidationError):
            parse_message({"jsonrpc": "2.0", "method": "echo", "id": bad_id})

    def test_invalid_params_type(self) -> None:
        with pytest.raises(JsonRpcValidationError):
            parse_message({"jsonrpc": "2.0", "method": "echo", "params": 42, "id": 1})

    def test_response_both_result_and_error(self) -> None:
        with pytest.raises(JsonRpcValidationError):
            parse_message(
                {
                    "jsonrpc": "2.0",
                    "result": 1,
                    "error": {"code": 1, "message": "m"},
                    "id": 1,
                }
            )

    def test_response_neither_result_nor_error(self) -> None:
        with pytest.raises(JsonRpcValidationError):
            parse_message({"jsonrpc": "2.0", "id": 1})

    def test_error_not_object(self) -> None:
        with pytest.raises(JsonRpcValidationError):
            parse_message({"jsonrpc": "2.0", "error": "boom", "id": 1})

    def test_error_code_not_int(self) -> None:
        with pytest.raises(JsonRpcValidationError):
            parse_message(
                {"jsonrpc": "2.0", "error": {"code": "x", "message": "m"}, "id": 1}
            )

    def test_error_code_bool(self) -> None:
        with pytest.raises(JsonRpcValidationError):
            parse_message(
                {"jsonrpc": "2.0", "error": {"code": True, "message": "m"}, "id": 1}
            )

    def test_error_message_not_string(self) -> None:
        with pytest.raises(JsonRpcValidationError):
            parse_message(
                {"jsonrpc": "2.0", "error": {"code": 1, "message": 5}, "id": 1}
            )

    def test_error_missing_message(self) -> None:
        with pytest.raises(JsonRpcValidationError):
            parse_message({"jsonrpc": "2.0", "error": {"code": 1}, "id": 1})


class TestCodec:
    """编码 / 解码测试。"""

    def test_encode_message(self) -> None:
        assert encode_message({"jsonrpc": "2.0", "method": "echo"}) == (
            b'{"jsonrpc": "2.0", "method": "echo"}\n'
        )

    def test_encode_ends_with_newline(self) -> None:
        assert encode_message({"jsonrpc": "2.0", "method": "echo"}).endswith(b"\n")

    def test_encode_utf8(self) -> None:
        raw = encode_message(build_request("echo", params={"msg": "你好"}))
        assert "你好".encode() in raw

    def test_decode_message(self) -> None:
        raw = b'{"jsonrpc": "2.0", "method": "echo", "id": 1}\n'
        assert decode_message(raw) == {"jsonrpc": "2.0", "method": "echo", "id": 1}

    def test_decode_ignores_trailing_newline(self) -> None:
        assert (
            decode_message(b'{"jsonrpc": "2.0", "method": "echo"}\n\n')["method"]
            == "echo"
        )

    def test_decode_invalid_json(self) -> None:
        with pytest.raises(JsonRpcDecodeError):
            decode_message(b"{not json}")

    def test_decode_invalid_utf8(self) -> None:
        with pytest.raises(JsonRpcDecodeError):
            decode_message(b"\xff\xfe")

    def test_decode_empty_bytes(self) -> None:
        with pytest.raises(JsonRpcDecodeError):
            decode_message(b"")

    def test_decode_array_rejected(self) -> None:
        with pytest.raises(JsonRpcValidationError):
            decode_message(b"[1, 2, 3]")

    def test_decode_scalar_rejected(self) -> None:
        with pytest.raises(JsonRpcValidationError):
            decode_message(b'"hello"')

    @pytest.mark.parametrize(
        "msg",
        [
            build_request("echo", {"x": 1}, 1),
            build_request("echo"),
            build_notification("ping"),
            build_response("ok", 1),
            build_error(INTERNAL_ERROR, "Internal error", data={"k": "v"}, id=None),
        ],
    )
    def test_roundtrip(self, msg: dict[str, Any]) -> None:
        assert decode_message(encode_message(msg)) == msg


class TestPipeline:
    """端到端链路测试。"""

    def test_full_request_response_roundtrip(self) -> None:
        req = parse_message(
            decode_message(
                encode_message(build_request("echo", params={"t": "hi"}, id=7))
            )
        )
        assert isinstance(req, JsonRpcRequest)
        assert req.method == "echo"
        resp = parse_message(
            decode_message(encode_message(build_response(req.method, req.id)))
        )
        assert isinstance(resp, JsonRpcResponse)
        assert resp.result == "echo"
        assert resp.id == 7

    def test_notification_pipeline(self) -> None:
        msg = parse_message(decode_message(encode_message(build_notification("ping"))))
        assert isinstance(msg, JsonRpcRequest)
        assert msg.id is None

    def test_error_pipeline(self) -> None:
        err = parse_message(
            decode_message(
                encode_message(build_error(METHOD_NOT_FOUND, "Method not found", id=3))
            )
        )
        assert isinstance(err, JsonRpcError)
        assert err.error.code == METHOD_NOT_FOUND
        assert err.id == 3


class TestErrorCodes:
    """标准错误码与异常层级测试。"""

    def test_standard_codes(self) -> None:
        assert PARSE_ERROR == -32700
        assert INVALID_REQUEST == -32600
        assert METHOD_NOT_FOUND == -32601
        assert INVALID_PARAMS == -32602
        assert INTERNAL_ERROR == -32603

    def test_llm_codes(self) -> None:
        assert LLM_ERROR == -32001
        assert LLM_AUTH_ERROR == -32002
        assert LLM_TIMEOUT_ERROR == -32003

    def test_decode_error_code(self) -> None:
        with pytest.raises(JsonRpcDecodeError) as exc_info:
            decode_message(b"oops")
        assert exc_info.value.code == PARSE_ERROR

    def test_validation_error_code(self) -> None:
        with pytest.raises(JsonRpcValidationError) as exc_info:
            parse_message({"jsonrpc": "2.0", "id": 1})
        assert exc_info.value.code == INVALID_REQUEST

    def test_error_hierarchy(self) -> None:
        assert issubclass(JsonRpcDecodeError, JsonRpcProtocolError)
        assert issubclass(JsonRpcValidationError, JsonRpcProtocolError)

    def test_exception_is_catchable_as_base(self) -> None:
        with pytest.raises(JsonRpcProtocolError):
            decode_message(b"{bad}")


class TestErrorResponse:
    """build_error_response 构造测试。"""

    def test_with_data_and_id(self) -> None:
        resp = build_error_response(7, LLM_ERROR, "llm failed", data={"detail": "x"})
        assert resp == {
            "jsonrpc": "2.0",
            "error": {"code": -32001, "message": "llm failed", "data": {"detail": "x"}},
            "id": 7,
        }

    def test_without_data_omits_data_key(self) -> None:
        resp = build_error_response(1, METHOD_NOT_FOUND, "not found")
        assert resp == {
            "jsonrpc": "2.0",
            "error": {"code": -32601, "message": "not found"},
            "id": 1,
        }
        assert "data" not in resp["error"]

    def test_null_id(self) -> None:
        resp = build_error_response(None, LLM_AUTH_ERROR, "auth failed")
        assert resp["id"] is None
        assert resp["error"]["code"] == LLM_AUTH_ERROR

    def test_roundtrip_parse(self) -> None:
        resp = build_error_response(9, LLM_TIMEOUT_ERROR, "timeout")
        msg = parse_message(resp)
        assert isinstance(msg, JsonRpcError)
        assert msg.error.code == LLM_TIMEOUT_ERROR
        assert msg.id == 9
