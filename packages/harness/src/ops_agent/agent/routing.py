"""运维请求的结构化路由和纯代码策略。"""

import re
from enum import StrEnum
from typing import Any

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field

ROUTER_PROMPT = """\
你是运维请求分类器，只返回 RouteDecision，不回答用户问题。

规则：
- Kubernetes 查询和诊断归类为 kubernetes。
- 其他运维系统或尚未接入的实时能力归类为 unsupported_operations。
- 天气、生活、闲聊和其他非运维请求归类为 out_of_scope。
- 读取和诊断使用 read_only；修改资源使用 write。
- 简单单目标请求使用 direct；复杂的多阶段根因诊断使用 plan。
"""

_KUBERNETES_PATTERN = re.compile(
    r"\b(?:kubernetes|k8s|kube|pods?|deployments?|statefulsets?|"
    r"daemonsets?|replicasets?|namespaces?|ingresses?|configmaps?|"
    r"cronjobs?|pvc|pv)\b|工作负载|命名空间",
    re.IGNORECASE,
)
_UNSUPPORTED_CAPABILITY_PATTERN = re.compile(
    r"\b(?:prometheus|grafana|loki|elasticsearch|datadog|new\s*relic|"
    r"splunk|opensearch)\b|日志平台|监控平台|告警平台|"
    r"\b(?:cpu|memory|qps|latency|throughput)\b|"
    r"内存使用率|资源使用率|延迟|吞吐量",
    re.IGNORECASE,
)
_READ_ONLY_INTENT_PATTERN = re.compile(
    r"检查|查看|查询|列出|获取|分析|诊断|解释|为什么|原因|状态|详情|"
    r"日志|事件|是否|有没有|吗|？"
    r"|\b(?:get|list|show|check|inspect|describe|diagnose|analyze|explain|"
    r"why|status|logs?|events?|what|which|did|has|have|is|are)\b",
    re.IGNORECASE,
)
_READ_ONLY_ACTION_CONTEXT_PATTERN = re.compile(
    r"(?:发生过|曾经|是否|有没有)?重启(?:次数|记录|历史|原因|状态)"
    r"|(?:发生过|曾经|是否|有没有)重启|重启(?:了)?吗"
    r"|\bdid\b.{0,40}\brestart(?:ed)?\b"
    r"|\bhas\b.{0,40}\brestarted\b"
    r"|\brestarted\b\s*\?",
    re.IGNORECASE,
)
_WRITE_ACTION_PATTERN = re.compile(
    r"(?:删除|重启|扩容|缩容|回滚|驱逐|修改|更新|创建|停止|启动|设置|"
    r"应用|编辑|替换|标记|注解|封锁|排空)"
    r"|\b(?:delete|restart|scale|rollback|evict|patch|update|create|"
    r"start|stop|set|apply|edit|replace|cordon|uncordon|drain|label|"
    r"annotate)\b",
    re.IGNORECASE,
)


class RouteDestination(StrEnum):
    KUBERNETES = "kubernetes"
    UNSUPPORTED_OPERATIONS = "unsupported_operations"
    OUT_OF_SCOPE = "out_of_scope"


class ExecutionMode(StrEnum):
    DIRECT = "direct"
    PLAN = "plan"


class OperationKind(StrEnum):
    READ_ONLY = "read_only"
    WRITE = "write"


class RouteAction(StrEnum):
    EXECUTE_KUBERNETES = "execute_kubernetes"
    CREATE_PLAN = "create_plan"
    REJECT_DEFAULT = "reject_default"
    REJECT_OUT_OF_SCOPE = "reject_out_of_scope"
    REJECT_UNSUPPORTED = "reject_unsupported"


class RouteDecision(BaseModel):
    """模型只能提出的结构化路由建议。"""

    model_config = ConfigDict(extra="forbid", frozen=True)

    destination: RouteDestination = Field(description="请求所属的能力范围")
    execution_mode: ExecutionMode = Field(description="直接执行或先规划")
    operation: OperationKind = Field(description="请求是否包含写操作")


class RequestRouter:
    """隐藏结构化模型调用及失败到空建议的转换。"""

    def __init__(self, model: BaseChatModel) -> None:
        self._runner = create_agent(
            model=model,
            tools=[],
            system_prompt=ROUTER_PROMPT,
            response_format=ToolStrategy(
                RouteDecision,
                handle_errors=False,
            ),
            name="ops_request_router",
        )

    def suggest(self, messages: list[Any]) -> RouteDecision | None:
        try:
            result = self._runner.invoke({"messages": messages})
        except Exception:  # noqa: BLE001 - 路由失败必须返回空建议
            return None
        decision = result.get("structured_response")
        return decision if isinstance(decision, RouteDecision) else None


def decide_route(
    question: str,
    suggestion: RouteDecision | None,
) -> RouteAction:
    """用原始问题和 allowlist 校验模型建议；未知请求默认拒绝。"""

    if _UNSUPPORTED_CAPABILITY_PATTERN.search(question):
        return RouteAction.REJECT_UNSUPPORTED
    if not _KUBERNETES_PATTERN.search(question):
        return RouteAction.REJECT_OUT_OF_SCOPE
    if _contains_write_action(question):
        return RouteAction.REJECT_UNSUPPORTED
    if not _READ_ONLY_INTENT_PATTERN.search(question):
        return RouteAction.REJECT_UNSUPPORTED
    if (
        suggestion is None
        or suggestion.destination is not RouteDestination.KUBERNETES
        or suggestion.operation is not OperationKind.READ_ONLY
    ):
        return RouteAction.REJECT_DEFAULT
    if suggestion.execution_mode is ExecutionMode.PLAN:
        return RouteAction.CREATE_PLAN
    return RouteAction.EXECUTE_KUBERNETES


def _contains_write_action(question: str) -> bool:
    without_read_only_context = _READ_ONLY_ACTION_CONTEXT_PATTERN.sub("", question)
    return _WRITE_ACTION_PATTERN.search(without_read_only_context) is not None
