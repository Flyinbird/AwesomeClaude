"""JSON-RPC 2.0 消息构造/解析/校验。"""

import json
from dataclasses import dataclass
from typing import Any

type JsonRpcId = str | int

JSONRPC_VERSION: str = "2.0"

PARSE_ERROR: int = -32700
INVALID_REQUEST: int = -32600
METHOD_NOT_FOUND: int = -32601
INVALID_PARAMS: int = -32602
INTERNAL_ERROR: int = -32603


class JsonRpcProtocolError(Exception):
    """JSON-RPC 协议异常基类，携带对应的标准错误码。"""

    code: int

    def __init__(self, message: str, *, code: int) -> None:
        """初始化协议异常。

        Args:
            message: 错误描述。
            code: 对应 JSON-RPC 标准错误码。
        """
        super().__init__(message)
        self.code = code


class JsonRpcDecodeError(JsonRpcProtocolError):
    """JSON 反序列化失败，对应 -32700 Parse error。"""

    def __init__(self, message: str) -> None:
        """初始化解码异常。

        Args:
            message: 错误描述。
        """
        super().__init__(message, code=PARSE_ERROR)


class JsonRpcValidationError(JsonRpcProtocolError):
    """消息结构非法，对应 -32600 Invalid request。"""

    def __init__(self, message: str) -> None:
        """初始化校验异常。

        Args:
            message: 错误描述。
        """
        super().__init__(message, code=INVALID_REQUEST)


@dataclass(frozen=True, slots=True)
class JsonRpcRequest:
    """JSON-RPC 请求（id 为 None 时表示 notification）。"""

    method: str
    jsonrpc: str = JSONRPC_VERSION
    params: Any = None
    id: JsonRpcId | None = None


@dataclass(frozen=True, slots=True)
class JsonRpcErrorDetail:
    """JSON-RPC 错误详情对象。"""

    code: int
    message: str
    data: Any = None


@dataclass(frozen=True, slots=True)
class JsonRpcResponse:
    """JSON-RPC 成功响应。"""

    result: Any
    id: JsonRpcId | None = None
    jsonrpc: str = JSONRPC_VERSION


@dataclass(frozen=True, slots=True)
class JsonRpcError:
    """JSON-RPC 错误响应。"""

    error: JsonRpcErrorDetail
    id: JsonRpcId | None = None
    jsonrpc: str = JSONRPC_VERSION


def build_request(
    method: str, params: Any = None, id: JsonRpcId | None = None
) -> dict[str, Any]:
    """构造 JSON-RPC 请求消息。

    Args:
        method: 方法名。
        params: 参数（可选），应为结构化值。
        id: 请求标识（可选），为 None 时省略 id 字段（视为 notification）。

    Returns:
        JSON-RPC 请求字典。
    """
    msg: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        msg["params"] = params
    if id is not None:
        msg["id"] = id
    return msg


def build_response(result: Any, id: JsonRpcId | None = None) -> dict[str, Any]:
    """构造 JSON-RPC 成功响应消息。

    Args:
        result: 调用结果。
        id: 对应请求的 id。

    Returns:
        JSON-RPC 成功响应字典。
    """
    return {"jsonrpc": JSONRPC_VERSION, "result": result, "id": id}


def build_error(
    code: int, message: str, data: Any = None, id: JsonRpcId | None = None
) -> dict[str, Any]:
    """构造 JSON-RPC 错误响应消息。

    Args:
        code: 错误码。
        message: 错误描述。
        data: 附加错误数据（可选）。
        id: 对应请求的 id。

    Returns:
        JSON-RPC 错误响应字典。
    """
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": JSONRPC_VERSION, "error": error, "id": id}


def build_notification(method: str, params: Any = None) -> dict[str, Any]:
    """构造 JSON-RPC 通知消息（不含 id）。

    Args:
        method: 方法名。
        params: 参数（可选），应为结构化值。

    Returns:
        JSON-RPC 通知字典。
    """
    msg: dict[str, Any] = {"jsonrpc": JSONRPC_VERSION, "method": method}
    if params is not None:
        msg["params"] = params
    return msg


def encode_message(msg: dict[str, Any]) -> bytes:
    """将消息序列化为 UTF-8 JSON 字节，以换行符结尾便于 TCP 分帧。

    Args:
        msg: 待序列化的消息字典。

    Returns:
        UTF-8 JSON 字节流。
    """
    return json.dumps(msg, ensure_ascii=False).encode("utf-8") + b"\n"


def decode_message(data: bytes) -> dict[str, Any]:
    """从字节流反序列化 JSON 对象。

    Args:
        data: 原始字节流。

    Returns:
        解析后的消息字典。

    Raises:
        JsonRpcDecodeError: 字节流不是合法 JSON 文本。
        JsonRpcValidationError: JSON 合法但不是对象。
    """
    try:
        raw: Any = json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as exc:
        raise JsonRpcDecodeError(f"JSON 解析失败: {exc}") from exc
    if not isinstance(raw, dict):
        raise JsonRpcValidationError(
            f"消息必须是 JSON 对象，实际为 {type(raw).__name__}"
        )
    return raw


def parse_message(
    data: dict[str, Any],
) -> JsonRpcRequest | JsonRpcResponse | JsonRpcError:
    """解析原始 JSON 对象并自动判断消息类型。

    Args:
        data: 消息字典。

    Returns:
        对应的请求 / 响应 / 错误消息对象。

    Raises:
        JsonRpcValidationError: 消息结构不符合 JSON-RPC 2.0 规范。
    """
    if data.get("jsonrpc") != JSONRPC_VERSION:
        raise JsonRpcValidationError(
            f"'jsonrpc' 字段必须为 {JSONRPC_VERSION!r}，实际为 {data.get('jsonrpc')!r}"
        )
    has_method = "method" in data
    has_result = "result" in data
    has_error = "error" in data
    if has_method:
        if has_result or has_error:
            raise JsonRpcValidationError(
                "请求消息不能同时包含 'method' 与 'result'/'error'"
            )
        return _parse_request(data)
    if has_result or has_error:
        return _parse_response(data)
    raise JsonRpcValidationError("消息必须包含 'method' 或 'result'/'error' 字段")


def _parse_request(data: dict[str, Any]) -> JsonRpcRequest:
    """解析请求消息（前提：jsonrpc 与 method 已粗校验）。"""
    method: Any = data.get("method")
    if not isinstance(method, str) or method == "":
        raise JsonRpcValidationError("请求的 'method' 必须是非空字符串")
    params: Any = data.get("params")
    if params is not None and not isinstance(params, (dict, list)):
        raise JsonRpcValidationError("请求的 'params' 必须是数组或对象")
    return JsonRpcRequest(method=method, params=params, id=_parse_id(data.get("id")))


def _parse_response(data: dict[str, Any]) -> JsonRpcResponse | JsonRpcError:
    """解析响应消息（前提：jsonrpc 已校验）。"""
    has_result = "result" in data
    has_error = "error" in data
    if has_result == has_error:
        raise JsonRpcValidationError("响应必须且只能包含 'result' 或 'error' 之一")
    resp_id = _parse_id(data.get("id"))
    if has_error:
        return JsonRpcError(error=_parse_error_detail(data["error"]), id=resp_id)
    return JsonRpcResponse(result=data["result"], id=resp_id)


def _parse_id(value: Any) -> JsonRpcId | None:
    """校验并规范化 id 字段。"""
    if value is None:
        return None
    if isinstance(value, bool):
        raise JsonRpcValidationError(f"非法的请求 id: {value!r}")
    if isinstance(value, (str, int)):
        return value
    raise JsonRpcValidationError(f"请求 id 必须是字符串、整数或 null: {value!r}")


def _parse_error_detail(value: Any) -> JsonRpcErrorDetail:
    """校验 error 详情对象。"""
    if not isinstance(value, dict):
        raise JsonRpcValidationError("'error' 必须是对象")
    code: Any = value.get("code")
    if not isinstance(code, int) or isinstance(code, bool):
        raise JsonRpcValidationError("error.code 必须是整数")
    message: Any = value.get("message")
    if not isinstance(message, str):
        raise JsonRpcValidationError("error.message 必须是字符串")
    return JsonRpcErrorDetail(code=code, message=message, data=value.get("data"))
