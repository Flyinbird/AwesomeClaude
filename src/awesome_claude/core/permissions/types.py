"""工具权限领域类型 - 判定结果、请求、结果与工具权限规格。"""

from collections.abc import Callable
from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from awesome_claude.core.tools.context import ToolContext

type DescribeFunc = Callable[[dict[str, Any], "ToolContext"], str]
type ResourcesFunc = Callable[[dict[str, Any], "ToolContext"], tuple[str, ...]]


class PermissionDecision(StrEnum):
    """工具执行的权限判定结果三态。"""

    ALLOW = "allow"
    DENY = "deny"
    ASK = "ask"


@dataclass(frozen=True, slots=True)
class PermissionSpec:
    """工具声明的权限规格，用于通用权限判定。

    工具以声明式方式参与权限体系：`default` 给出无更具体规则命中时的默认
    姿态，`describe` 产出面向人的动作描述，`resources` 产出用于规则匹配的
    `type:value` 资源标识元组。
    """

    default: PermissionDecision = PermissionDecision.ALLOW
    describe: DescribeFunc | None = None
    resources: ResourcesFunc | None = None


@dataclass(frozen=True, slots=True)
class PermissionRequest:
    """一次工具调用的权限判定请求。"""

    tool: str
    args: dict[str, Any]
    run_id: str = ""
    step_index: int | None = None
    action: str = ""
    resources: tuple[str, ...] = ()
    spec: PermissionSpec | None = None


@dataclass(frozen=True, slots=True)
class PermissionOutcome:
    """一次权限判定的结果。"""

    decision: PermissionDecision
    reason: str = ""

    @property
    def is_allowed(self) -> bool:
        """判定结果是否为允许。"""
        return self.decision is PermissionDecision.ALLOW
