from enum import StrEnum
from typing import Annotated, Self

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    model_validator,
)

NonBlankText = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class _AgentModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class InteractionChannel(StrEnum):
    CLI = "cli"
    TUI = "tui"
    API = "api"


class CapabilityScope(StrEnum):
    AUTO = "auto"
    KUBERNETES = "kubernetes"


class InteractionContext(_AgentModel):
    channel: InteractionChannel = InteractionChannel.CLI
    scope: CapabilityScope = CapabilityScope.AUTO
    environment: NonBlankText | None = None
    namespace: NonBlankText | None = None

    @model_validator(mode="after")
    def kubernetes_scope_requires_runtime_context(self) -> Self:
        if self.scope is CapabilityScope.KUBERNETES and (
            not self.environment or not self.namespace
        ):
            raise ValueError("Kubernetes scope 必须包含 environment 和 namespace")
        return self


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


class ResourceKind(StrEnum):
    KUBERNETES = "kubernetes"
    WORKLOAD = "workload"
    POD = "pod"
    DEPLOYMENT = "deployment"
    SERVICE = "service"
    EVENT = "event"
    LOG = "log"
    UNKNOWN = "unknown"


class ResultShape(StrEnum):
    DETAIL = "detail"
    LIST = "list"
    COUNT = "count"
    DIAGNOSIS = "diagnosis"
    UNKNOWN = "unknown"


class IntentProposal(_AgentModel):
    """模型对用户请求提出的、不可信的结构化解释。"""

    destination: RouteDestination = Field(description="请求所属的能力范围")
    execution_mode: ExecutionMode = Field(description="直接执行或先规划")
    operation: OperationKind = Field(description="请求是否包含写操作")
    resource: ResourceKind = Field(description="用户希望查询或诊断的资源")
    result_shape: ResultShape = Field(description="用户期望的回答形态")
    ambiguities: tuple[NonBlankText, ...] = Field(
        default=(),
        max_length=3,
        description="执行前仍需澄清的歧义，不确定时必须填写",
    )


class CapabilityId(StrEnum):
    KUBERNETES_DIAGNOSTICS_READ = "kubernetes.diagnostics.read"
    KUBERNETES_WORKLOADS_READ = "kubernetes.workloads.read"
    KUBERNETES_PODS_READ = "kubernetes.pods.read"
    KUBERNETES_DEPLOYMENTS_READ = "kubernetes.deployments.read"
    KUBERNETES_SERVICES_READ = "kubernetes.services.read"
    KUBERNETES_EVENTS_READ = "kubernetes.events.read"
    KUBERNETES_LOGS_READ = "kubernetes.logs.read"


class CapabilityRegistry(_AgentModel):
    """当前组合根实际提供给专业 Agent 的能力集合。"""

    enabled: frozenset[CapabilityId] = Field(default_factory=frozenset)

    def supports(self, capability: CapabilityId) -> bool:
        return capability in self.enabled


class PolicyAction(StrEnum):
    EXECUTE_KUBERNETES = "execute_kubernetes"
    CREATE_PLAN = "create_plan"
    CLARIFY_REQUEST = "clarify_request"
    REJECT_DEFAULT = "reject_default"
    REJECT_OUT_OF_SCOPE = "reject_out_of_scope"
    REJECT_UNSUPPORTED = "reject_unsupported"


class PolicyReason(StrEnum):
    ALLOWED = "allowed"
    AMBIGUOUS = "ambiguous"
    EXPLICIT_SCOPE_REQUIRED = "explicit_scope_required"
    SCOPE_CONFLICT = "scope_conflict"
    UNKNOWN_RESOURCE = "unknown_resource"
    OUT_OF_SCOPE = "out_of_scope"
    UNSUPPORTED_CAPABILITY = "unsupported_capability"
    WRITE_OPERATION = "write_operation"
    INVALID_PROPOSAL = "invalid_proposal"


class PolicyDecision(_AgentModel):
    """代码校验 Intent Proposal 后产生的可信执行决策。"""

    action: PolicyAction
    reason: PolicyReason
    capability: CapabilityId | None = None
    proposed_capability: CapabilityId | None = None

    @model_validator(mode="after")
    def execution_requires_an_allowed_capability(self) -> Self:
        execution_actions = {
            PolicyAction.EXECUTE_KUBERNETES,
            PolicyAction.CREATE_PLAN,
        }
        if self.action in execution_actions and (
            self.reason is not PolicyReason.ALLOWED or self.capability is None
        ):
            raise ValueError("执行决策必须包含通过校验的 Capability")
        if self.action not in execution_actions and (
            self.reason is PolicyReason.ALLOWED or self.capability is not None
        ):
            raise ValueError("非执行决策不能声明已授权 Capability")
        if (
            self.proposed_capability is not None
            and self.action is not PolicyAction.CLARIFY_REQUEST
        ):
            raise ValueError("只有澄清决策可以保留候选 Capability")
        return self


class AgentStage(StrEnum):
    UNDERSTANDING = "understanding"
    INTENT_INTERPRETED = "intent_interpreted"
    POLICY_VALIDATED = "policy_validated"
    PLANNING = "planning"
    QUERYING = "querying"
    EVIDENCE_VALIDATED = "evidence_validated"
    COMPLETED = "completed"


class AgentEvent(_AgentModel):
    """应用入口可以稳定消费的对话进度。"""

    stage: AgentStage
    message: NonBlankText
    answer: NonBlankText | None = None

    @model_validator(mode="after")
    def answer_only_appears_on_completion(self) -> Self:
        if self.stage is AgentStage.COMPLETED and self.answer is None:
            raise ValueError("完成事件必须包含回答")
        if self.stage is not AgentStage.COMPLETED and self.answer is not None:
            raise ValueError("只有完成事件可以包含回答")
        return self
