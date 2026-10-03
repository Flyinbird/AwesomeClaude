"""权限策略 - 进程级规则求值（资源规则 / 工具默认 / 全局默认）。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from fnmatch import fnmatchcase

from awesome_claude.core.permissions.types import (
    PermissionDecision,
    PermissionRequest,
    PermissionSpec,
)


@dataclass(frozen=True, slots=True)
class PermissionRule:
    """一条资源模式规则。

    `pattern` 形式为 `type:glob`（如 `cmd:git *`、`path:/ws/*`），或 `*`
    匹配全部；`type` 与资源标识的类型段精确相等后，值段按 glob 匹配。
    """

    pattern: str
    decision: PermissionDecision


def _matches(pattern: str, resource: str) -> bool:
    """判断资源标识是否命中某条规则模式。

    Args:
        pattern: 规则模式，`*`、`type`、`type:glob` 之一。
        resource: 资源标识，形如 `type:value`。

    Returns:
        是否命中。
    """
    if pattern == "*":
        return True
    if ":" not in pattern:
        return fnmatchcase(resource, pattern)
    ptype, _, pvalue = pattern.partition(":")
    rtype, _, rvalue = resource.partition(":")
    if ptype != rtype:
        return False
    if pvalue in ("", "*"):
        return True
    return fnmatchcase(rvalue, pvalue)


class PermissionPolicy:
    """进程级权限策略，按固定优先级求值权限判定。

    优先级依次为：命中的资源规则（拒绝 > 询问 > 允许）、工具默认（策略内
    覆盖，其次工具的 `PermissionSpec.default`）、全局默认姿态。
    """

    def __init__(
        self,
        *,
        global_default: PermissionDecision = PermissionDecision.ALLOW,
        tool_defaults: Mapping[str, PermissionDecision] | None = None,
        rules: Sequence[PermissionRule] = (),
    ) -> None:
        """初始化策略。

        Args:
            global_default: 无任何规则与工具默认命中时的全局默认姿态。
            tool_defaults: 按工具名覆盖的默认姿态（可选）。
            rules: 资源模式规则序列。
        """
        self._global_default = global_default
        self._tool_defaults = dict(tool_defaults or {})
        self._rules = tuple(rules)

    @property
    def global_default(self) -> PermissionDecision:
        """全局默认姿态。"""
        return self._global_default

    def evaluate(
        self, request: PermissionRequest, spec: PermissionSpec | None = None
    ) -> PermissionDecision:
        """对一次工具调用求权限判定结果。

        Args:
            request: 权限判定请求。
            spec: 工具权限规格，缺省时取 ``request.spec``。

        Returns:
            判定结果（允许 / 拒绝 / 询问）。
        """
        effective_spec = spec if spec is not None else request.spec
        matched = {
            rule.decision
            for rule in self._rules
            if any(_matches(rule.pattern, res) for res in request.resources)
        }
        if PermissionDecision.DENY in matched:
            return PermissionDecision.DENY
        if PermissionDecision.ASK in matched:
            return PermissionDecision.ASK
        if PermissionDecision.ALLOW in matched:
            return PermissionDecision.ALLOW
        override = self._tool_defaults.get(request.tool)
        if override is not None:
            return override
        if effective_spec is not None:
            return effective_spec.default
        return self._global_default
