"""JSON-RPC 错误码定义与应用错误码。"""

from typing import Any

PARSE_ERROR: int = -32700
INVALID_REQUEST: int = -32600
METHOD_NOT_FOUND: int = -32601
INVALID_PARAMS: int = -32602
INTERNAL_ERROR: int = -32603

LLM_ERROR: int = -32001
LLM_AUTH_ERROR: int = -32002
LLM_TIMEOUT_ERROR: int = -32003


def build_error_response(
    request_id: str | int | None,
    code: int,
    message: str,
    data: Any = None,
) -> dict[str, Any]:
    """构造 JSON-RPC 错误响应字典。

    Args:
        request_id: 对应请求的 id。
        code: 错误码。
        message: 错误描述。
        data: 附加错误数据（可选）。

    Returns:
        JSON-RPC 错误响应字典。
    """
    error: dict[str, Any] = {"code": code, "message": message}
    if data is not None:
        error["data"] = data
    return {"jsonrpc": "2.0", "error": error, "id": request_id}
