"""core/permissions 权限框架测试。"""

from typing import Any

from awesome_claude.core.permissions.broker import NonInteractiveBroker
from awesome_claude.core.permissions.manager import PermissionManager
from awesome_claude.core.permissions.policy import PermissionPolicy, PermissionRule
from awesome_claude.core.permissions.types import (
    PermissionDecision,
    PermissionRequest,
    PermissionSpec,
)
from awesome_claude.shared.types import TraceStage


def _req(
    *,
    tool: str = "t",
    resources: tuple[str, ...] = (),
    spec: PermissionSpec | None = None,
    step_index: int | None = 1,
) -> PermissionRequest:
    """构造测试用权限判定请求。"""
    return PermissionRequest(
        tool=tool,
        args={},
        run_id="r1",
        step_index=step_index,
        resources=resources,
        spec=spec,
    )


class FakeRecorder:
    """捕获轨迹调用的测试替身。"""

    def __init__(self) -> None:
        self.events: list[tuple[TraceStage, dict[str, Any], int | None]] = []

    async def record(
        self, stage: TraceStage, data: dict[str, Any], *, step_index: int | None = None
    ) -> None:
        self.events.append((stage, data, step_index))


class TestPermissionPolicy:
    """PermissionPolicy 优先级与匹配测试。"""

    def test_global_default_allow(self) -> None:
        policy = PermissionPolicy()
        assert policy.evaluate(_req()) is PermissionDecision.ALLOW

    def test_spec_default_used_when_no_rule(self) -> None:
        policy = PermissionPolicy()
        spec = PermissionSpec(default=PermissionDecision.ASK)
        assert policy.evaluate(_req(spec=spec)) is PermissionDecision.ASK

    def test_tool_default_override_beats_spec(self) -> None:
        policy = PermissionPolicy(tool_defaults={"t": PermissionDecision.DENY})
        spec = PermissionSpec(default=PermissionDecision.ALLOW)
        assert policy.evaluate(_req(spec=spec)) is PermissionDecision.DENY

    def test_global_default_deny_when_nothing_matches(self) -> None:
        policy = PermissionPolicy(global_default=PermissionDecision.DENY)
        assert policy.evaluate(_req()) is PermissionDecision.DENY

    def test_resource_rule_allow_beats_spec_default_ask(self) -> None:
        policy = PermissionPolicy(
            rules=[PermissionRule("cmd:git *", PermissionDecision.ALLOW)]
        )
        spec = PermissionSpec(default=PermissionDecision.ASK)
        req = _req(resources=("cmd:git status",), spec=spec)
        assert policy.evaluate(req) is PermissionDecision.ALLOW

    def test_resource_rule_deny_wins_over_allow(self) -> None:
        policy = PermissionPolicy(
            rules=[
                PermissionRule("cmd:git*", PermissionDecision.ALLOW),
                PermissionRule("cmd:git push*", PermissionDecision.DENY),
            ]
        )
        req = _req(resources=("cmd:git push origin main",))
        assert policy.evaluate(req) is PermissionDecision.DENY

    def test_type_prefix_isolates_resources(self) -> None:
        policy = PermissionPolicy(
            rules=[PermissionRule("cmd:git*", PermissionDecision.DENY)]
        )
        req = _req(resources=("path:/ws/a",))
        assert policy.evaluate(req) is PermissionDecision.ALLOW

    def test_path_prefix_glob_matches(self) -> None:
        policy = PermissionPolicy(
            rules=[PermissionRule("path:/ws/*", PermissionDecision.DENY)]
        )
        req = _req(resources=("path:/ws/sub/file",))
        assert policy.evaluate(req) is PermissionDecision.DENY

    def test_wildcard_rule_matches_any(self) -> None:
        policy = PermissionPolicy(rules=[PermissionRule("*", PermissionDecision.DENY)])
        assert policy.evaluate(_req(resources=("path:/x",))) is PermissionDecision.DENY


class TestNonInteractiveBroker:
    """NonInteractiveBroker 测试。"""

    async def test_ask_denies(self) -> None:
        broker = NonInteractiveBroker()
        outcome = await broker.ask(_req())
        assert outcome.decision is PermissionDecision.DENY
        assert outcome.is_allowed is False


class TestPermissionManager:
    """PermissionManager 编排与轨迹测试。"""

    async def test_allow_records_requested_and_granted(self) -> None:
        recorder = FakeRecorder()
        manager = PermissionManager(
            PermissionPolicy(), NonInteractiveBroker(), recorder=recorder
        )
        outcome = await manager.authorize(_req())
        assert outcome.decision is PermissionDecision.ALLOW
        stages = [event[0] for event in recorder.events]
        assert stages == [
            TraceStage.PERMISSION_REQUESTED,
            TraceStage.PERMISSION_GRANTED,
        ]
        assert recorder.events[0][2] == 1

    async def test_deny_records_requested_and_denied(self) -> None:
        recorder = FakeRecorder()
        manager = PermissionManager(
            PermissionPolicy(global_default=PermissionDecision.DENY),
            NonInteractiveBroker(),
            recorder=recorder,
        )
        outcome = await manager.authorize(_req())
        assert outcome.decision is PermissionDecision.DENY
        stages = [event[0] for event in recorder.events]
        assert stages == [
            TraceStage.PERMISSION_REQUESTED,
            TraceStage.PERMISSION_DENIED,
        ]

    async def test_ask_fail_closed_records_denied(self) -> None:
        recorder = FakeRecorder()
        manager = PermissionManager(
            PermissionPolicy(rules=[PermissionRule("*", PermissionDecision.ASK)]),
            NonInteractiveBroker(),
            recorder=recorder,
        )
        outcome = await manager.authorize(_req(resources=("cmd:rm",)))
        assert outcome.decision is PermissionDecision.DENY
        assert recorder.events[-1][0] is TraceStage.PERMISSION_DENIED

    async def test_works_without_recorder(self) -> None:
        manager = PermissionManager(PermissionPolicy(), NonInteractiveBroker())
        outcome = await manager.authorize(_req())
        assert outcome.decision is PermissionDecision.ALLOW
