"""chat 处理器 - 委托 AgentLoop 执行 LLM 对话（Phase 2/3）。"""

import time
import uuid
from dataclasses import asdict
from typing import Any

from awesome_claude.core.agent.events import (
    StepFinished,
    StepStarted,
    ToolFinished,
    ToolStarted,
)
from awesome_claude.core.agent.prompt import PROMPT_VERSION, build_system_prompt
from awesome_claude.core.handlers.base import register_handler
from awesome_claude.core.llm.events import TextDeltaEvent
from awesome_claude.core.llm.exceptions import (
    LLMAuthError,
    LLMError,
    LLMTimeoutError,
)
from awesome_claude.core.observability.trace_recorder import TraceRecorder
from awesome_claude.core.router.context import HandlerContext
from awesome_claude.core.session.run import RunState
from awesome_claude.core.task.graph import TaskChange, TaskChangeKind, TaskGraph
from awesome_claude.core.tools.context import ToolScope
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
    NOTIFY_CHAT_INTERRUPTED,
    NOTIFY_CHAT_PLAN_UPDATED,
    NOTIFY_CHAT_STREAM,
    NOTIFY_CHAT_TOOL_FINISHED,
    NOTIFY_CHAT_TOOL_STARTED,
    NOTIFY_CHAT_USER_MESSAGE,
)
from awesome_claude.shared.logging.app_logger import get_app_logger
from awesome_claude.shared.types import ChatResponse, StopReason, TraceStage

_logger = get_app_logger("core.handlers.chat")

_TASK_CHANGE_STAGE: dict[TaskChangeKind, TraceStage] = {
    TaskChangeKind.ADDED: TraceStage.TASK_ADDED,
    TaskChangeKind.STARTED: TraceStage.TASK_STARTED,
    TaskChangeKind.COMPLETED: TraceStage.TASK_COMPLETED,
    TaskChangeKind.REOPENED: TraceStage.TASK_REOPENED,
    TaskChangeKind.SUSPENDED: TraceStage.TASK_SUSPENDED,
}


def _clip_text(text: str, *, head: int = 100, tail: int = 100) -> tuple[str, bool]:
    """裁剪长文本为「前 head + 省略标记 + 后 tail」，供日志记录。

    Args:
        text: 原始文本。
        head: 保留的头部字符数。
        tail: 保留的尾部字符数。

    Returns:
        (预览文本, 是否被裁剪)。短文本原样返回、未裁剪标记为 False。
    """
    if len(text) <= head + tail:
        return text, False
    omitted = len(text) - head - tail
    return f"{text[:head]}…[省略 {omitted} 字符]…{text[-tail:]}", True


def _clip_message_content(value: object, *, head: int = 100, tail: int = 100) -> object:
    """裁剪单条消息 content（字符串或内容块列表），返回副本供日志使用。

    递归处理 Anthropic 内容块中的 text / tool_result 长字段，非字符串
    字段原样保留。

    Args:
        value: 消息的 content 字段。
        head: 保留的头部字符数。
        tail: 保留的尾部字符数。

    Returns:
        裁剪后的 content 副本。
    """
    if isinstance(value, str):
        return _clip_text(value, head=head, tail=tail)[0]
    if isinstance(value, list):
        clipped_blocks: list[object] = []
        for block in value:
            if not isinstance(block, dict):
                clipped_blocks.append(block)
                continue
            block_copy = dict(block)
            for key in ("text", "content"):
                field = block_copy.get(key)
                if isinstance(field, str):
                    block_copy[key] = _clip_text(field, head=head, tail=tail)[0]
            clipped_blocks.append(block_copy)
        return clipped_blocks
    return value


def _clip_messages(
    messages: list[dict[str, Any]], *, head: int = 100, tail: int = 100
) -> list[dict[str, Any]]:
    """返回 messages 的日志副本：裁剪其中的长文本与 tool_result 内容。

    Args:
        messages: 原始消息列表。
        head: 保留的头部字符数。
        tail: 保留的尾部字符数。

    Returns:
        裁剪后的消息列表副本，原列表不被修改。
    """
    clipped: list[dict[str, Any]] = []
    for message in messages:
        message_copy = dict(message)
        if "content" in message_copy:
            message_copy["content"] = _clip_message_content(
                message_copy["content"], head=head, tail=tail
            )
        clipped.append(message_copy)
    return clipped


def _error_code(exc: LLMError) -> int:
    """LLM 异常 → JSON-RPC 错误码。"""
    if isinstance(exc, LLMAuthError):
        return LLM_AUTH_ERROR
    if isinstance(exc, LLMTimeoutError):
        return LLM_TIMEOUT_ERROR
    return LLM_ERROR


def _failed_stage(exc: LLMError) -> TraceStage:
    """LLM 异常对应的失败阶段。"""
    if isinstance(exc, LLMAuthError):
        return TraceStage.LLM_REQUEST_SENT
    return TraceStage.LLM_STREAMING


@register_handler(METHOD_CHAT)
async def handle_chat(
    params: dict[str, Any] | None, context: HandlerContext
) -> dict[str, Any]:
    """处理 chat 请求的完整流程。

    流程：RUN_CREATED → CONTEXT_BUILT →（每轮 step）STEP_STARTED →
    LLM_REQUEST_SENT → LLM_STREAMING → LLM_RESPONSE_DONE →（工具调用）
    TOOL_STARTED → TOOL_COMPLETED/TOOL_FAILED → RUN_COMPLETED。
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
    agent_loop = context.agent_loop
    channel = context.sessions
    run = context.run
    if agent_loop is None:
        if run is not None:
            run.finish(RunState.FAILED)
        return build_error_response(None, INTERNAL_ERROR, "agent loop 未配置")

    if run is not None and run.recorder is not None:
        run_id = run.run_id
        start_time = run.start_time
        recorder = run.recorder
    else:
        run_id = run.run_id if run is not None else uuid.uuid4().hex[:8]
        start_time = run.start_time if run is not None else time.monotonic()
        recorder = TraceRecorder(context.trace_store, run_id, start_time)
        if run is None:
            await recorder.run_created(
                {"user_input": str(message), "client_addr": "unknown"}
            )

    if session_id is not None and channel.session_id != session_id:
        channel.attach(session_id)

    named_session = session_id if isinstance(session_id, str) and session_id else None

    async def emit(
        method: str, payload: dict[str, Any], *, exclude_self: bool = False
    ) -> None:
        """向会话（命名会话）或发起连接（临时会话）发送通知。"""
        if named_session is not None:
            await channel.broadcast_to(
                named_session, method, payload, exclude_self=exclude_self
            )
        else:
            await channel.broadcast(method, payload, exclude_self=exclude_self)

    chunk_index = 0
    current_step = 0

    task_graph = TaskGraph(run_id)

    async def on_task_change(change: TaskChange) -> None:
        """任务图变更 → 轨迹事件 + 计划进度通知。"""
        stage = _TASK_CHANGE_STAGE.get(change.kind)
        if stage is not None:
            await recorder.record(
                stage,
                {
                    "task_id": change.task.id,
                    "goal": change.task.goal,
                    "status": change.task.status.value,
                    "deps": list(change.task.deps),
                    "attempts": change.task.attempts,
                    "last_error": change.task.last_error,
                },
                step_index=current_step or None,
            )
        await emit(
            NOTIFY_CHAT_PLAN_UPDATED,
            {
                "run_id": run_id,
                "tasks": task_graph.snapshot(),
                "session_id": session_id,
            },
        )

    task_graph = TaskGraph(run_id, on_change=on_task_change)
    scope = ToolScope(run_id=run_id, task_graph=task_graph)
    system = (
        build_system_prompt(context.system_prompt)
        if context.system_prompt is not None
        else None
    )

    async def on_event(event: Any) -> None:
        nonlocal chunk_index
        if isinstance(event, TextDeltaEvent):
            chunk_index += 1
            await emit(
                NOTIFY_CHAT_STREAM,
                {
                    "run_id": run_id,
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
            if event.step_index == 1:
                await recorder.record(
                    TraceStage.CONTEXT_BUILT,
                    {
                        "system": event.system,
                        "system_prompt_version": PROMPT_VERSION
                        if event.system
                        else None,
                        "messages": _clip_messages(event.messages),
                        "message_count": len(event.messages),
                        "tools": [t["name"] for t in event.tools]
                        if event.tools
                        else [],
                    },
                )
            await recorder.record(
                TraceStage.STEP_STARTED,
                {"task_id": task_graph.current_task_id()},
                step_index=event.step_index,
            )
            await recorder.record(
                TraceStage.LLM_REQUEST_SENT,
                {
                    "task_id": task_graph.current_task_id(),
                    "model": context.config.model,
                    "messages": _clip_messages(event.messages),
                },
                step_index=event.step_index,
            )
            await recorder.record(
                TraceStage.LLM_STREAMING,
                {"task_id": task_graph.current_task_id()},
                step_index=event.step_index,
            )
        elif isinstance(event, StepFinished):
            text, truncated = _clip_text(event.text)
            await recorder.record(
                TraceStage.LLM_RESPONSE_DONE,
                {
                    "task_id": task_graph.current_task_id(),
                    "stop_reason": event.stop_reason,
                    "input_tokens": event.input_tokens,
                    "output_tokens": event.output_tokens,
                    "has_tool_calls": event.has_tool_calls,
                    "text": text,
                    "text_truncated": truncated,
                },
                step_index=event.step_index,
            )
        elif isinstance(event, ToolStarted):
            await recorder.record(
                TraceStage.TOOL_STARTED,
                {
                    "task_id": task_graph.current_task_id(),
                    "tool_name": event.tool_name,
                    "args": event.args,
                },
                step_index=event.step_index,
            )
            await emit(
                NOTIFY_CHAT_TOOL_STARTED,
                {
                    "run_id": run_id,
                    "step_index": event.step_index,
                    "tool_name": event.tool_name,
                    "args": event.args,
                    "session_id": session_id,
                },
            )
        elif isinstance(event, ToolFinished):
            content, content_truncated = _clip_text(event.content)
            await recorder.record(
                TraceStage.TOOL_FAILED if event.is_error else TraceStage.TOOL_COMPLETED,
                {
                    "task_id": task_graph.current_task_id(),
                    "tool_name": event.tool_name,
                    "is_error": event.is_error,
                    "content": content,
                    "content_truncated": content_truncated,
                },
                step_index=event.step_index,
            )
            await emit(
                NOTIFY_CHAT_TOOL_FINISHED,
                {
                    "run_id": run_id,
                    "step_index": event.step_index,
                    "tool_name": event.tool_name,
                    "is_error": event.is_error,
                    "session_id": session_id,
                },
            )

    try:
        if named_session is not None:
            await emit(
                NOTIFY_CHAT_USER_MESSAGE,
                {"session_id": session_id, "message": message},
                exclude_self=True,
            )

        result = await agent_loop.run(
            message, system=system, on_event=on_event, on_step=on_step, scope=scope
        )

        await emit(
            NOTIFY_CHAT_STREAM,
            {
                "run_id": run_id,
                "chunk_index": chunk_index,
                "text": "",
                "is_final": True,
                "session_id": session_id,
            },
        )

        if named_session is not None:
            channel.record_turn(message, result.text, run_id, session_id=named_session)

        response = ChatResponse(
            run_id=run_id,
            text=result.text,
            stop_reason=result.stop_reason,
            usage=result.usage,
            duration_ms=(time.monotonic() - start_time) * 1000.0,
            model=context.config.model,
        )
        if result.stop_reason == StopReason.MAX_STEPS:
            text, truncated = _clip_text(response.text)
            await recorder.run_interrupted(
                {
                    "stop_reason": result.stop_reason,
                    "steps": result.steps,
                    "text_length": len(response.text),
                    "text": text,
                    "text_truncated": truncated,
                },
                step_index=result.steps,
            )
            await emit(
                NOTIFY_CHAT_INTERRUPTED,
                {
                    "run_id": run_id,
                    "stop_reason": result.stop_reason,
                    "step_index": result.steps,
                    "session_id": session_id,
                },
            )
            if run is not None:
                run.finish(RunState.INTERRUPTED)
        else:
            text, truncated = _clip_text(response.text)
            await recorder.run_completed(
                {
                    "text_length": len(response.text),
                    "stop_reason": response.stop_reason,
                    "steps": result.steps,
                    "text": text,
                    "text_truncated": truncated,
                }
            )
            if run is not None:
                run.finish(RunState.COMPLETED)
        return asdict(response)
    except LLMError as exc:
        await recorder.run_failed(
            exc,
            _failed_stage(exc),
            step_index=current_step or None,
        )
        if run is not None:
            run.finish(RunState.FAILED)
        return build_error_response(None, _error_code(exc), str(exc))
    except Exception as exc:
        _logger.exception("chat handler failed")
        await recorder.run_failed(
            exc,
            TraceStage.LLM_STREAMING,
            step_index=current_step or None,
        )
        if run is not None:
            run.finish(RunState.FAILED)
        return build_error_response(None, INTERNAL_ERROR, f"Internal error: {exc}")
