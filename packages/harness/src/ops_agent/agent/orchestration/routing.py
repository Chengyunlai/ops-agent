"""主图的结构化意图理解和纯代码策略。"""

import json
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, SystemMessage

from ops_agent.agent.models import (
    CapabilityId,
    CapabilityRegistry,
    CapabilityScope,
    ExecutionMode,
    IntentProposal,
    InteractionContext,
    OperationKind,
    PolicyAction,
    PolicyDecision,
    PolicyReason,
    ResourceKind,
    ResultShape,
    RouteDestination,
)

INTERPRETER_PROMPT = """\
你是运维请求意图解释器，只返回 IntentProposal，不回答用户问题。

规则：
- Kubernetes 查询和诊断归类为 kubernetes。
- 其他运维系统或尚未接入的实时能力归类为 unsupported_operations。
- 天气、生活、闲聊和其他非运维请求归类为 out_of_scope。
- 读取、计数和诊断使用 read_only；修改资源使用 write。
- 简单单目标请求使用 direct；复杂的多阶段根因诊断使用 plan。
- 根据用户表达识别 Pod、Deployment、Service、Event、日志或工作负载。
- “几个”“多少”“数量”这类问题的 result_shape 使用 count。
- 可信应用上下文为 kubernetes scope 时，可以把“服务”等自然简称解释为
  Kubernetes Service，但不能改变上下文中的 environment 或 namespace。
- 存在多个合理解释或缺少关键资源时，将歧义写入 ambiguities。
"""

_KUBERNETES_PATTERN = re.compile(
    r"\b(?:kubernetes|k8s|kube|pods?|deployments?|statefulsets?|"
    r"daemonsets?|replicasets?|namespaces?|ingresses?|configmaps?|"
    r"cronjobs?|pvc|pv)\b|工作负载|命名空间",
    re.IGNORECASE,
)
_AMBIGUOUS_SERVICE_PATTERN = re.compile(r"\bservices?\b|服务", re.IGNORECASE)
_RESOURCE_PATTERNS = (
    (
        ResourceKind.LOG,
        re.compile(r"\blogs?\b|日志", re.IGNORECASE),
    ),
    (
        ResourceKind.EVENT,
        re.compile(r"\bevents?\b|事件", re.IGNORECASE),
    ),
    (
        ResourceKind.SERVICE,
        re.compile(r"\bservices?\b|服务", re.IGNORECASE),
    ),
    (
        ResourceKind.DEPLOYMENT,
        re.compile(r"\bdeployments?\b|部署", re.IGNORECASE),
    ),
    (
        ResourceKind.POD,
        re.compile(r"\bpods?\b|Pod", re.IGNORECASE),
    ),
    (
        ResourceKind.WORKLOAD,
        re.compile(
            r"\b(?:kubernetes|k8s|workloads?)\b|工作负载",
            re.IGNORECASE,
        ),
    ),
)
_REFERENTIAL_FOLLOW_UP_PATTERN = re.compile(
    r"哪个|哪些|其中|它们?|这个|这些|有问题|异常|分别",
    re.IGNORECASE,
)
_UNSUPPORTED_CAPABILITY_PATTERN = re.compile(
    r"\b(?:prometheus|grafana|loki|elasticsearch|datadog|new\s*relic|"
    r"splunk|opensearch)\b|日志平台|监控平台|告警平台|"
    r"\b(?:cpu|memory|qps|latency|throughput)\b|"
    r"内存使用率|资源使用率|延迟|吞吐量",
    re.IGNORECASE,
)
_OUT_OF_SCOPE_PATTERN = re.compile(
    r"天气|气温|降雨|新闻|"
    r"\b(?:weather|forecast|headline|node\s*\.?\s*js)\b",
    re.IGNORECASE,
)
_DIAGNOSTIC_INTENT_PATTERN = re.compile(
    r"检查|查看|查询|列出|获取|分析|诊断|解释|为什么|原因|状态|详情|"
    r"日志|事件|是否|有没有|几个|多少|数量|哪个|哪些|问题|异常|正常|"
    r"健康|运行|就绪|失败|重启|怎么样|怎么了|"
    r"\b(?:get|list|show|check|inspect|describe|diagnose|analyze|explain|"
    r"why|status|logs?|events?|how\s+many|count|which|issue|problem|"
    r"healthy|running|ready|failed?|did|has|have|restart(?:ed)?)\b",
    re.IGNORECASE,
)
_COUNT_INTENT_PATTERN = re.compile(
    r"几个|多少|数量|\b(?:how\s+many|count)\b",
    re.IGNORECASE,
)
_PLAN_INTENT_PATTERN = re.compile(
    r"完整|根因|分析|诊断|为什么|原因|失败|异常|"
    r"\b(?:root\s+cause|analy[sz]e|diagnos(?:e|is)|troubleshoot|why|"
    r"failed?|failure)\b",
    re.IGNORECASE,
)
_AFFIRMATIVE_PATTERN = re.compile(
    r"^\s*(?:是|是的|对|对的|没错|确认|可以|yes|y|correct)\s*[。.!！]?\s*$",
    re.IGNORECASE,
)
_THINK_PREFIX_PATTERN = re.compile(
    r"^\s*<think>.*?</think>\s*",
    re.DOTALL,
)
_JSON_FENCE_PATTERN = re.compile(
    r"^\s*```(?:json)?\s*(?P<body>.*?)\s*```\s*$",
    re.DOTALL | re.IGNORECASE,
)
_SCOPE_REFERENCE_PATTERNS = {
    "namespace": (
        re.compile(
            r"(?P<value>[A-Za-z0-9][A-Za-z0-9_.-]*)\s*"
            r"(?:namespace\b|命名空间)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:namespace|命名空间)\s*(?:是|为|=|:)\s*"
            r"(?P<value>[A-Za-z0-9][A-Za-z0-9_.-]*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:namespace|命名空间)\s+"
            r"(?P<value>[A-Za-z0-9][A-Za-z0-9_.-]*)",
            re.IGNORECASE,
        ),
    ),
    "environment": (
        re.compile(
            r"(?P<value>[A-Za-z0-9][A-Za-z0-9_.-]*)\s*"
            r"(?:environment\b|环境)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:environment|环境)\s*(?:是|为|=|:)\s*"
            r"(?P<value>[\w.-]+)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:environment|环境)\s+"
            r"(?P<value>[A-Za-z0-9][A-Za-z0-9_.-]*)",
            re.IGNORECASE,
        ),
        re.compile(
            r"(?:^|[\s，,])(?:检查|查看|查询)?"
            r"(?P<value>[\u4e00-\u9fff]{1,16})环境",
            re.IGNORECASE,
        ),
    ),
}
_GENERIC_SCOPE_REFERENCES = {
    "a",
    "all",
    "current",
    "for",
    "in",
    "k8s",
    "kubernetes",
    "the",
    "this",
    "within",
    "全部",
    "当前",
    "所有",
    "这个",
    "检查",
    "查看",
    "查询",
}
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


@dataclass(frozen=True)
class _CapabilityBinding:
    capability: CapabilityId
    resources: frozenset[ResourceKind]
    tool_names: frozenset[str]


_CAPABILITY_BINDINGS = (
    _CapabilityBinding(
        capability=CapabilityId.KUBERNETES_DIAGNOSTICS_READ,
        resources=frozenset(),
        tool_names=frozenset(
            {
                "diagnose_kubernetes_workloads",
                "get_kubernetes_pod_details",
                "get_kubernetes_pod_logs",
                "list_kubernetes_deployments",
                "list_kubernetes_events",
                "list_kubernetes_pods",
                "list_kubernetes_service_endpoints",
                "list_kubernetes_services",
            }
        ),
    ),
    _CapabilityBinding(
        capability=CapabilityId.KUBERNETES_WORKLOADS_READ,
        resources=frozenset(
            {
                ResourceKind.KUBERNETES,
                ResourceKind.WORKLOAD,
            }
        ),
        tool_names=frozenset({"diagnose_kubernetes_workloads"}),
    ),
    _CapabilityBinding(
        capability=CapabilityId.KUBERNETES_PODS_READ,
        resources=frozenset({ResourceKind.POD}),
        tool_names=frozenset(
            {
                "list_kubernetes_pods",
                "get_kubernetes_pod_details",
            }
        ),
    ),
    _CapabilityBinding(
        capability=CapabilityId.KUBERNETES_DEPLOYMENTS_READ,
        resources=frozenset({ResourceKind.DEPLOYMENT}),
        tool_names=frozenset({"list_kubernetes_deployments"}),
    ),
    _CapabilityBinding(
        capability=CapabilityId.KUBERNETES_SERVICES_READ,
        resources=frozenset({ResourceKind.SERVICE}),
        tool_names=frozenset(
            {
                "list_kubernetes_service_endpoints",
                "list_kubernetes_services",
            }
        ),
    ),
    _CapabilityBinding(
        capability=CapabilityId.KUBERNETES_EVENTS_READ,
        resources=frozenset({ResourceKind.EVENT}),
        tool_names=frozenset({"list_kubernetes_events"}),
    ),
    _CapabilityBinding(
        capability=CapabilityId.KUBERNETES_LOGS_READ,
        resources=frozenset({ResourceKind.LOG}),
        tool_names=frozenset({"get_kubernetes_pod_logs"}),
    ),
)


class IntentInterpreter:
    """隐藏结构化模型调用及失败到空建议的转换。"""

    def __init__(self, model: BaseChatModel) -> None:
        try:
            self._model = model.bind_tools([IntentProposal])
        except NotImplementedError:
            self._model = model

    def suggest(
        self,
        messages: list[Any],
        context: InteractionContext,
    ) -> IntentProposal | None:
        contextual_messages = [
            SystemMessage(
                content=(
                    f"{INTERPRETER_PROMPT}\n"
                    f"{_format_trusted_context(context)}\n"
                    "优先调用 IntentProposal 工具；若 Provider 不支持，"
                    "只输出符合以下 JSON Schema 的对象，不要使用 Markdown：\n"
                    f"{json.dumps(IntentProposal.model_json_schema(), ensure_ascii=False)}"
                )
            ),
            *messages,
        ]
        try:
            response = self._model.invoke(contextual_messages)
        except Exception:  # noqa: BLE001 - 解释失败必须返回空建议
            return None
        return _intent_proposal_from_response(response)


def evaluate_policy(
    question: str,
    proposal: IntentProposal | None,
    context: InteractionContext,
    capabilities: CapabilityRegistry,
    *,
    prior_assistant_answer: str | None = None,
    prior_capability: CapabilityId | None = None,
) -> PolicyDecision:
    """校验不可信意图；只有注册过的只读能力可以进入专业 Agent。"""

    if _UNSUPPORTED_CAPABILITY_PATTERN.search(question):
        return PolicyDecision(
            action=PolicyAction.REJECT_UNSUPPORTED,
            reason=PolicyReason.UNSUPPORTED_CAPABILITY,
        )
    if _OUT_OF_SCOPE_PATTERN.search(question):
        return PolicyDecision(
            action=PolicyAction.REJECT_OUT_OF_SCOPE,
            reason=PolicyReason.OUT_OF_SCOPE,
        )
    if _contains_write_action(question) or (
        proposal is not None and proposal.operation is OperationKind.WRITE
    ):
        return PolicyDecision(
            action=PolicyAction.REJECT_UNSUPPORTED,
            reason=PolicyReason.WRITE_OPERATION,
        )
    if proposal is None:
        return PolicyDecision(
            action=PolicyAction.REJECT_DEFAULT,
            reason=PolicyReason.INVALID_PROPOSAL,
        )
    if proposal.destination is RouteDestination.OUT_OF_SCOPE:
        return PolicyDecision(
            action=PolicyAction.REJECT_OUT_OF_SCOPE,
            reason=PolicyReason.OUT_OF_SCOPE,
        )
    if proposal.destination is RouteDestination.UNSUPPORTED_OPERATIONS:
        return PolicyDecision(
            action=PolicyAction.REJECT_UNSUPPORTED,
            reason=PolicyReason.UNSUPPORTED_CAPABILITY,
        )
    if proposal.ambiguities:
        return PolicyDecision(
            action=PolicyAction.CLARIFY_REQUEST,
            reason=PolicyReason.AMBIGUOUS,
        )

    confirmed_scope = prior_capability is not None and _confirms_kubernetes_scope(
        question,
        prior_assistant_answer,
    )
    if not _DIAGNOSTIC_INTENT_PATTERN.search(question) and not confirmed_scope:
        return PolicyDecision(
            action=PolicyAction.REJECT_OUT_OF_SCOPE,
            reason=PolicyReason.OUT_OF_SCOPE,
        )

    explicit_resource = _resource_from_question(question)
    proposed_capability = _capability_for_resource(
        explicit_resource or proposal.resource
    )
    if proposed_capability is None:
        return PolicyDecision(
            action=PolicyAction.CLARIFY_REQUEST,
            reason=PolicyReason.UNKNOWN_RESOURCE,
        )
    scope_conflict = _scope_conflict(question, context)
    if scope_conflict:
        return PolicyDecision(
            action=PolicyAction.CLARIFY_REQUEST,
            reason=PolicyReason.SCOPE_CONFLICT,
            proposed_capability=proposed_capability,
        )
    has_resource_reference = bool(
        explicit_resource is not None or _KUBERNETES_PATTERN.search(question)
    )
    refers_to_prior_capability = bool(
        prior_capability is not None
        and _REFERENTIAL_FOLLOW_UP_PATTERN.search(question)
        and not has_resource_reference
    )
    if not has_resource_reference and not (
        confirmed_scope or refers_to_prior_capability
    ):
        return PolicyDecision(
            action=PolicyAction.REJECT_OUT_OF_SCOPE,
            reason=PolicyReason.OUT_OF_SCOPE,
        )
    if (
        context.scope is CapabilityScope.AUTO
        and not _KUBERNETES_PATTERN.search(question)
        and not confirmed_scope
        and not refers_to_prior_capability
    ):
        if (
            proposed_capability is CapabilityId.KUBERNETES_SERVICES_READ
            and _AMBIGUOUS_SERVICE_PATTERN.search(question)
        ):
            return PolicyDecision(
                action=PolicyAction.CLARIFY_REQUEST,
                reason=PolicyReason.EXPLICIT_SCOPE_REQUIRED,
                proposed_capability=proposed_capability,
            )
        return PolicyDecision(
            action=PolicyAction.REJECT_OUT_OF_SCOPE,
            reason=PolicyReason.OUT_OF_SCOPE,
        )
    create_plan = bool(
        proposal.execution_mode is ExecutionMode.PLAN
        and proposal.result_shape is ResultShape.DIAGNOSIS
        and not _COUNT_INTENT_PATTERN.search(question)
        and _PLAN_INTENT_PATTERN.search(question)
    )
    capability = (
        CapabilityId.KUBERNETES_DIAGNOSTICS_READ
        if create_plan
        else (
            prior_capability
            if confirmed_scope or refers_to_prior_capability
            else proposed_capability
        )
    )
    if not capabilities.supports(capability):
        return PolicyDecision(
            action=PolicyAction.REJECT_UNSUPPORTED,
            reason=PolicyReason.UNSUPPORTED_CAPABILITY,
        )

    action = (
        PolicyAction.CREATE_PLAN if create_plan else PolicyAction.EXECUTE_KUBERNETES
    )
    return PolicyDecision(
        action=action,
        reason=PolicyReason.ALLOWED,
        capability=capability,
    )


def registered_capabilities(tool_names: Iterable[str]) -> CapabilityRegistry:
    """把实际提供的只读工具投影为主图可授权的 Capability。"""

    available_tool_names = frozenset(tool_names)
    return CapabilityRegistry(
        enabled=frozenset(
            binding.capability
            for binding in _CAPABILITY_BINDINGS
            if binding.tool_names <= available_tool_names
        )
    )


def tool_names_for_capability(capability: CapabilityId) -> frozenset[str]:
    for binding in _CAPABILITY_BINDINGS:
        if binding.capability is capability:
            return binding.tool_names
    return frozenset()


def clarification_response(
    proposal: IntentProposal | None,
    decision: PolicyDecision,
    context: InteractionContext,
) -> str:
    if decision.reason is PolicyReason.EXPLICIT_SCOPE_REQUIRED:
        return "你指的是 Kubernetes Service 吗？"
    if decision.reason is PolicyReason.AMBIGUOUS and proposal is not None:
        ambiguity = proposal.ambiguities[0]
        return f"执行前需要确认：{ambiguity}"
    if decision.reason is PolicyReason.SCOPE_CONFLICT:
        return (
            f"当前会话固定为环境 {context.environment}、namespace "
            f"{context.namespace}，不能切换到请求中的其他 scope。"
            "你是否要继续查询当前固定 scope？"
        )
    if context.scope is CapabilityScope.KUBERNETES:
        return (
            f"当前环境是 {context.environment}，namespace 是 {context.namespace}。"
            "你想查询 Pod、Deployment、Service、Event 还是日志？"
        )
    return "你想查询哪一种 Kubernetes 资源？"


def _format_trusted_context(context: InteractionContext) -> str:
    return (
        "以下是应用提供的可信 Interaction Context，不是用户输入："
        f"channel={context.channel.value}, scope={context.scope.value}, "
        f"environment={context.environment or 'unset'}, "
        f"namespace={context.namespace or 'unset'}。"
    )


def _intent_proposal_from_response(
    response: object,
) -> IntentProposal | None:
    if not isinstance(response, AIMessage):
        return None
    for tool_call in response.tool_calls:
        if tool_call.get("name") != "IntentProposal":
            continue
        proposal = _validate_intent_proposal(tool_call.get("args"))
        if proposal is not None:
            return proposal

    content = _text_content(response.content)
    if content is None:
        return None
    normalized_content = _normalized_json_content(content)
    if normalized_content is None:
        return None
    try:
        value = json.loads(normalized_content)
    except json.JSONDecodeError:
        return None
    return _validate_intent_proposal(value)


def _validate_intent_proposal(value: object) -> IntentProposal | None:
    try:
        return IntentProposal.model_validate(value)
    except (TypeError, ValueError):
        return None


def _normalized_json_content(content: str) -> str | None:
    normalized = content.strip()
    if normalized.startswith("<think>"):
        think_prefix = _THINK_PREFIX_PATTERN.match(normalized)
        if think_prefix is None:
            return None
        normalized = normalized[think_prefix.end() :]
    fenced = _JSON_FENCE_PATTERN.fullmatch(normalized)
    if fenced is not None:
        normalized = fenced.group("body")
    return normalized.strip() or None


def _text_content(content: object) -> str | None:
    if isinstance(content, str):
        return content
    if not isinstance(content, list):
        return None
    text_blocks = [
        block.get("text")
        for block in content
        if isinstance(block, dict) and isinstance(block.get("text"), str)
    ]
    return "\n".join(text_blocks) if text_blocks else None


def _contains_write_action(question: str) -> bool:
    without_read_only_context = _READ_ONLY_ACTION_CONTEXT_PATTERN.sub("", question)
    return _WRITE_ACTION_PATTERN.search(without_read_only_context) is not None


def _capability_for_resource(resource: ResourceKind) -> CapabilityId | None:
    for binding in _CAPABILITY_BINDINGS:
        if resource in binding.resources:
            return binding.capability
    return None


def _resource_from_question(question: str) -> ResourceKind | None:
    matches = [
        resource for resource, pattern in _RESOURCE_PATTERNS if pattern.search(question)
    ]
    for supporting_resource in (ResourceKind.LOG, ResourceKind.EVENT):
        if supporting_resource in matches:
            return supporting_resource
    specific_matches = [
        resource for resource in matches if resource is not ResourceKind.WORKLOAD
    ]
    if len(specific_matches) == 1:
        return specific_matches[0]
    if len(specific_matches) > 1:
        return ResourceKind.UNKNOWN
    return ResourceKind.WORKLOAD if matches else None


def _confirms_kubernetes_scope(
    question: str,
    prior_assistant_answer: str | None,
) -> bool:
    return bool(
        prior_assistant_answer
        and (
            "你指的是 Kubernetes" in prior_assistant_answer
            or "你是否要继续查询当前固定 scope" in prior_assistant_answer
        )
        and _AFFIRMATIVE_PATTERN.fullmatch(question)
    )


def _scope_conflict(
    question: str,
    context: InteractionContext,
) -> bool:
    if context.scope is not CapabilityScope.KUBERNETES:
        return False
    expected_values = {
        "namespace": context.namespace,
        "environment": context.environment,
    }
    for scope_name, patterns in _SCOPE_REFERENCE_PATTERNS.items():
        expected = expected_values[scope_name]
        for pattern in patterns:
            for match in pattern.finditer(question):
                requested = match.group("value")
                if requested.lower() in _GENERIC_SCOPE_REFERENCES:
                    continue
                if expected is not None and requested.lower() != expected.lower():
                    return True
    return False
