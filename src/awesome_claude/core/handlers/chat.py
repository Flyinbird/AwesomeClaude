"""chat 处理器 - LLM 对话（Phase 2）。"""

import time
from dataclasses import asdict
from typing import Any

from awesome_claude.core.handlers.base import register_handler
from awesome_claude.core.llm.events import DoneEvent, TextDeltaEvent
from awesome_claude.core.llm.exceptions import (
    LLMAuthError,
    LLMError,
    LLMTimeoutError,
)
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.protocol.errors import (
    INTERNAL_ERROR,
    INVALID_PARAMS,
    LLM_AUTH_ERROR,
    LLM_ERROR,
    LLM_TIMEOUT_ERROR,
    build_error_response,
)
from awesome_claude.protocol.methods import METHOD_CHAT, NOTIFY_CHAT_STREAM
from awesome_claude.shared.logging.app_logger import get_app_logger
from awesome_claude.shared.types import ChatResponse, TaskStage, TokenUsage

_logger = get_app_logger("core.handlers.chat")


def _error_code(exc: LLMError) -> int:
    """LLM 异常 → JSON-RPC 错误码。"""
    if isinstance(exc, LLMAuthError):
        return LLM_AUTH_ERROR
    if isinstance(exc, LLMTimeoutError):
        return LLM_TIMEOUT_ERROR
    return LLM_ERROR


def _failed_stage(exc: LLMError) -> TaskStage:
    """LLM 异常对应的失败阶段。"""
    if isinstance(exc, LLMAuthError):
        return TaskStage.LLM_REQUEST_SENT
    return TaskStage.LLM_STREAMING


@register_handler(METHOD_CHAT)
async def handle_chat(
    params: dict[str, Any] | None, context: HandlerContext
) -> dict[str, Any]:
    """处理 chat 请求的完整流程。

    流程：TASK_CREATED → CONTEXT_BUILT → LLM_REQUEST_SENT → LLM_STREAMING
    （推送 chat.stream 通知）→ LLM_RESPONSE_DONE → TASK_COMPLETED。

    Args:
        params: 必须包含 message 字段。
        context: 运行时上下文。

    Returns:
        ChatResponse 的 dict 形式，或错误响应 dict。
    """
    if params is None or not isinstance(params, dict) or "message" not in params:
        return build_error_response(None, INVALID_PARAMS, "chat 需要参数 message")

    message = params["message"]
    task_manager = context.task_manager

    try:
        task_id, start_time = await task_manager.create_task(
            message, client_addr="unknown"
        )
    except Exception as exc:
        _logger.exception("create task failed")
        return build_error_response(None, INTERNAL_ERROR, f"任务创建失败: {exc}")

    try:
        await task_manager.record_stage(
            task_id, start_time, TaskStage.CONTEXT_BUILT, {"message_count": 1}
        )
        messages = [{"role": "user", "content": message}]

        await task_manager.record_stage(
            task_id,
            start_time,
            TaskStage.LLM_REQUEST_SENT,
            {"model": context.config.model},
        )

        chunk_index = 0
        done_event: DoneEvent | None = None
        await task_manager.record_stage(
            task_id, start_time, TaskStage.LLM_STREAMING, {"chunk_index": 0}
        )
        async for event in context.llm_client.chat_stream(messages):
            if isinstance(event, TextDeltaEvent):
                chunk_index += 1
                await context.send_notification(
                    NOTIFY_CHAT_STREAM,
                    {
                        "task_id": task_id,
                        "chunk_index": chunk_index,
                        "text": event.text,
                        "is_final": False,
                    },
                )
            elif isinstance(event, DoneEvent):
                done_event = event
                await context.send_notification(
                    NOTIFY_CHAT_STREAM,
                    {
                        "task_id": task_id,
                        "chunk_index": chunk_index,
                        "text": "",
                        "is_final": True,
                    },
                )

        await task_manager.record_stage(
            task_id,
            start_time,
            TaskStage.LLM_RESPONSE_DONE,
            {
                "chunk_count": chunk_index,
                "stop_reason": done_event.stop_reason if done_event else "",
                "input_tokens": done_event.usage.input_tokens if done_event else 0,
                "output_tokens": done_event.usage.output_tokens if done_event else 0,
            },
        )

        response = ChatResponse(
            task_id=task_id,
            text=done_event.full_text if done_event else "",
            stop_reason=done_event.stop_reason if done_event else "",
            usage=(
                done_event.usage
                if done_event
                else TokenUsage(input_tokens=0, output_tokens=0)
            ),
            duration_ms=(time.monotonic() - start_time) * 1000.0,
            model=context.config.model,
        )
        await task_manager.complete_task(
            task_id,
            start_time,
            {"text_length": len(response.text), "stop_reason": response.stop_reason},
        )
        return asdict(response)
    except LLMError as exc:
        await task_manager.fail_task(task_id, start_time, exc, _failed_stage(exc))
        return build_error_response(None, _error_code(exc), str(exc))
    except Exception as exc:
        _logger.exception("chat handler failed")
        await task_manager.fail_task(task_id, start_time, exc, TaskStage.LLM_STREAMING)
        return build_error_response(None, INTERNAL_ERROR, f"Internal error: {exc}")
