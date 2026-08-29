"""chat 处理器 - 委托 AgentLoop 执行 LLM 对话（Phase 2/3）。"""

import time
from dataclasses import asdict
from typing import Any

from awesome_claude.core.agent.events import (
    StepFinished,
    StepStarted,
    ToolFinished,
    ToolStarted,
)
from awesome_claude.core.handlers.base import register_handler
from awesome_claude.core.llm.events import TextDeltaEvent
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
from awesome_claude.protocol.methods import (
    METHOD_CHAT,
    NOTIFY_CHAT_STREAM,
    NOTIFY_CHAT_TOOL_FINISHED,
    NOTIFY_CHAT_TOOL_STARTED,
    NOTIFY_CHAT_USER_MESSAGE,
)
from awesome_claude.shared.logging.app_logger import get_app_logger
from awesome_claude.shared.types import ChatResponse, TaskStage

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

    流程：TASK_CREATED → CONTEXT_BUILT →（每轮 step）STEP_STARTED →
    LLM_REQUEST_SENT → LLM_STREAMING → LLM_RESPONSE_DONE →（工具调用）
    TOOL_STARTED → TOOL_COMPLETED/TOOL_FAILED → TASK_COMPLETED。
    实际对话由 AgentLoop 编排，支持多轮工具调用。

    若 params 携带 session_id，则当前连接订阅到该会话，所有通知
    （chat.stream / chat.tool_* / chat.user_message）在会话内广播，并
    将本轮对话记录到会话历史。

    Args:
        params: 必须包含 message 字段，可选 session_id。
        context: 运行时上下文。

    Returns:
        ChatResponse 的 dict 形式，或错误响应 dict。
    """
    if params is None or not isinstance(params, dict) or "message" not in params:
        return build_error_response(None, INVALID_PARAMS, "chat 需要参数 message")

    message = params["message"]
    session_id = params.get("session_id")
    task_manager = context.task_manager
    agent_loop = context.agent_loop
    channel = context.sessions
    if agent_loop is None:
        return build_error_response(None, INTERNAL_ERROR, "agent loop 未配置")

    try:
        task_id, start_time = await task_manager.create_task(
            message, client_addr="unknown"
        )
    except Exception as exc:
        _logger.exception("create task failed")
        return build_error_response(None, INTERNAL_ERROR, f"任务创建失败: {exc}")

    if session_id is not None and channel.session_id != session_id:
        channel.attach(session_id)

    chunk_index = 0
    current_step = 0

    async def on_event(event: Any) -> None:
        nonlocal chunk_index
        if isinstance(event, TextDeltaEvent):
            chunk_index += 1
            await channel.broadcast(
                NOTIFY_CHAT_STREAM,
                {
                    "task_id": task_id,
                    "chunk_index": chunk_index,
                    "text": event.text,
                    "is_final": False,
                    "session_id": session_id,
                },
            )

    async def on_step(event: Any) -> None:
        nonlocal current_step
        if isinstance(event, StepStarted):
            current_step = event.step_index
            await task_manager.record_stage(
                task_id,
                start_time,
                TaskStage.STEP_STARTED,
                {},
                step_index=event.step_index,
            )
            await task_manager.record_stage(
                task_id,
                start_time,
                TaskStage.LLM_REQUEST_SENT,
                {"model": context.config.model},
                step_index=event.step_index,
            )
            await task_manager.record_stage(
                task_id,
                start_time,
                TaskStage.LLM_STREAMING,
                {},
                step_index=event.step_index,
            )
        elif isinstance(event, StepFinished):
            await task_manager.record_stage(
                task_id,
                start_time,
                TaskStage.LLM_RESPONSE_DONE,
                {
                    "stop_reason": event.stop_reason,
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                    "has_tool_calls": event.has_tool_calls,
                },
                step_index=event.step_index,
            )
        elif isinstance(event, ToolStarted):
            await task_manager.record_stage(
                task_id,
                start_time,
                TaskStage.TOOL_STARTED,
                {"tool_name": event.tool_name, "args": event.args},
                step_index=event.step_index,
            )
            await channel.broadcast(
                NOTIFY_CHAT_TOOL_STARTED,
                {
                    "task_id": task_id,
                    "step_index": event.step_index,
                    "tool_name": event.tool_name,
                    "args": event.args,
                    "session_id": session_id,
                },
            )
        elif isinstance(event, ToolFinished):
            await task_manager.record_stage(
                task_id,
                start_time,
                TaskStage.TOOL_FAILED if event.is_error else TaskStage.TOOL_COMPLETED,
                {"tool_name": event.tool_name, "is_error": event.is_error},
                step_index=event.step_index,
            )
            await channel.broadcast(
                NOTIFY_CHAT_TOOL_FINISHED,
                {
                    "task_id": task_id,
                    "step_index": event.step_index,
                    "tool_name": event.tool_name,
                    "is_error": event.is_error,
                    "session_id": session_id,
                },
            )

    try:
        await task_manager.record_stage(
            task_id, start_time, TaskStage.CONTEXT_BUILT, {"message_count": 1}
        )

        if session_id is not None:
            await channel.broadcast(
                NOTIFY_CHAT_USER_MESSAGE,
                {"session_id": session_id, "message": message},
                exclude_self=True,
            )

        result = await agent_loop.run(message, on_event=on_event, on_step=on_step)

        await channel.broadcast(
            NOTIFY_CHAT_STREAM,
            {
                "task_id": task_id,
                "chunk_index": chunk_index,
                "text": "",
                "is_final": True,
                "session_id": session_id,
            },
        )

        if session_id is not None:
            channel.record_turn(message, result.text, task_id)

        response = ChatResponse(
            task_id=task_id,
            text=result.text,
            stop_reason=result.stop_reason,
            usage=result.usage,
            duration_ms=(time.monotonic() - start_time) * 1000.0,
            model=context.config.model,
        )
        await task_manager.complete_task(
            task_id,
            start_time,
            {
                "text_length": len(response.text),
                "stop_reason": response.stop_reason,
                "steps": result.steps,
            },
        )
        return asdict(response)
    except LLMError as exc:
        await task_manager.fail_task(
            task_id,
            start_time,
            exc,
            _failed_stage(exc),
            step_index=current_step or None,
        )
        return build_error_response(None, _error_code(exc), str(exc))
    except Exception as exc:
        _logger.exception("chat handler failed")
        await task_manager.fail_task(
            task_id,
            start_time,
            exc,
            TaskStage.LLM_STREAMING,
            step_index=current_step or None,
        )
        return build_error_response(None, INTERNAL_ERROR, f"Internal error: {exc}")
