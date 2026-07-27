"""受控主图的 State、Node 与 Edge 拓扑。"""

from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph

from ops_agent.agent.application import OpsAgent
from ops_agent.agent.models import (
    AgentEvent,
    AgentStage,
    CapabilityId,
    IntentProposal,
    InteractionContext,
    PolicyAction,
    PolicyDecision,
)
from ops_agent.agent.orchestration.routing import (
    IntentInterpreter,
    clarification_response,
    evaluate_policy,
    registered_capabilities,
    tool_names_for_capability,
)
from ops_agent.agent.specialists.kubernetes import (
    ExecutionPlan,
    KubernetesAgent,
    KubernetesDiagnosticPlanner,
    KubernetesPlanExecutor,
)

OUT_OF_SCOPE_RESPONSE = "当前系统只处理 Kubernetes 运维与诊断问题。"
DEFAULT_REJECT_RESPONSE = "无法安全确定请求所属能力，已拒绝执行。"
UNSUPPORTED_RESPONSE = (
    "这是运维问题，但当前尚未接入对应的专业诊断能力，因此无法获取或推测实时状态。"
)
NO_EVIDENCE_RESPONSE = "没有获取到 Kubernetes 实时证据，无法给出当前状态结论。"
PLAN_REJECTED_RESPONSE = "无法生成满足当前只读能力约束的诊断计划。"
_CAPABILITY_MESSAGE_KEY = "ops_agent_capability"
_PENDING_CAPABILITY_MESSAGE_KEY = "ops_agent_pending_capability"


class _OpsGraphState(MessagesState):
    interaction_context: InteractionContext
    intent_proposal: IntentProposal | None
    policy_decision: PolicyDecision | None
    progress_event: AgentEvent | None
    candidate_answer: str | None
    evidence_count: int
    plan: ExecutionPlan | None


def create_ops_agent(
    model: BaseChatModel,
    kubernetes_tools: Sequence[BaseTool],
) -> OpsAgent:
    """构建受控主图和独立的 Kubernetes 诊断子图。"""

    interpreter = IntentInterpreter(model)
    planner = KubernetesDiagnosticPlanner(model)
    tools_by_name = {tool.name: tool for tool in kubernetes_tools}
    capabilities = registered_capabilities(tool.name for tool in kubernetes_tools)
    direct_agents = {
        capability: KubernetesAgent(
            model,
            [
                tools_by_name[tool_name]
                for tool_name in sorted(tool_names_for_capability(capability))
            ],
        )
        for capability in capabilities.enabled
    }
    plan_tool_names = tool_names_for_capability(
        CapabilityId.KUBERNETES_DIAGNOSTICS_READ
    )
    plan_executor = (
        KubernetesPlanExecutor(
            KubernetesAgent(
                model,
                [tools_by_name[tool_name] for tool_name in sorted(plan_tool_names)],
            )
        )
        if capabilities.supports(CapabilityId.KUBERNETES_DIAGNOSTICS_READ)
        else None
    )

    def interpret_request(state: _OpsGraphState) -> dict[str, object]:
        return {
            "intent_proposal": interpreter.suggest(
                state["messages"],
                _interaction_context(state),
            ),
            "progress_event": AgentEvent(
                stage=AgentStage.INTENT_INTERPRETED,
                message="已理解请求意图",
            ),
        }

    def validate_policy(state: _OpsGraphState) -> dict[str, object]:
        decision = evaluate_policy(
            _last_user_question(state),
            state.get("intent_proposal"),
            _interaction_context(state),
            capabilities,
            prior_assistant_answer=_prior_assistant_answer(state),
            prior_capability=_prior_capability(state),
        )
        return {
            "policy_decision": decision,
            "progress_event": AgentEvent(
                stage=AgentStage.POLICY_VALIDATED,
                message=_policy_progress_message(decision),
            ),
        }

    def choose_route(state: _OpsGraphState) -> str:
        decision = state.get("policy_decision")
        return (
            decision.action.value
            if decision is not None
            else PolicyAction.REJECT_DEFAULT.value
        )

    def clarify_request(state: _OpsGraphState) -> dict[str, object]:
        decision = state.get("policy_decision")
        if decision is None:
            return {"messages": [AIMessage(content=DEFAULT_REJECT_RESPONSE)]}
        content = clarification_response(
            state.get("intent_proposal"),
            decision,
            _interaction_context(state),
        )
        message_kwargs = (
            {_PENDING_CAPABILITY_MESSAGE_KEY: (decision.proposed_capability.value)}
            if decision.proposed_capability is not None
            else {}
        )
        return {
            "messages": [
                AIMessage(
                    content=content,
                    additional_kwargs=message_kwargs,
                )
            ]
        }

    def prepare_kubernetes(_: _OpsGraphState) -> dict[str, object]:
        return {
            "progress_event": AgentEvent(
                stage=AgentStage.QUERYING,
                message="正在查询 Kubernetes 实时证据",
            )
        }

    def prepare_plan(_: _OpsGraphState) -> dict[str, object]:
        return {
            "progress_event": AgentEvent(
                stage=AgentStage.PLANNING,
                message="正在生成受控诊断计划",
            )
        }

    def execute_kubernetes(state: _OpsGraphState) -> dict[str, object]:
        decision = state.get("policy_decision")
        agent = (
            direct_agents.get(decision.capability)
            if decision is not None and decision.capability is not None
            else None
        )
        if agent is None:
            return {"candidate_answer": None, "evidence_count": 0}
        result = agent.diagnose(_conversation_request(state))
        return {
            "candidate_answer": result.answer,
            "evidence_count": result.evidence_count,
        }

    def create_plan(state: _OpsGraphState) -> dict[str, object]:
        return {
            "plan": planner.create(_conversation_request(state)),
        }

    def choose_plan_result(state: _OpsGraphState) -> str:
        return "execute_plan" if state.get("plan") is not None else "reject_plan"

    def execute_plan(state: _OpsGraphState) -> dict[str, object]:
        plan = state.get("plan")
        if plan is None or plan_executor is None:
            return {"candidate_answer": None, "evidence_count": 0}
        result = plan_executor.execute(_conversation_request(state), plan)
        return {
            "candidate_answer": result.answer,
            "evidence_count": result.evidence_count,
        }

    def validate_evidence(state: _OpsGraphState) -> dict[str, object]:
        answer = state.get("candidate_answer")
        if state.get("evidence_count", 0) < 1 or not answer:
            answer = NO_EVIDENCE_RESPONSE
        decision = state.get("policy_decision")
        message_kwargs = (
            {_CAPABILITY_MESSAGE_KEY: decision.capability.value}
            if decision is not None and decision.capability is not None
            else {}
        )
        return {
            "messages": [
                AIMessage(
                    content=answer,
                    additional_kwargs=message_kwargs,
                )
            ],
            "progress_event": AgentEvent(
                stage=AgentStage.EVIDENCE_VALIDATED,
                message="已完成实时工具证据校验",
            ),
        }

    builder = StateGraph(_OpsGraphState)
    builder.add_node("interpret_request", interpret_request)
    builder.add_node("validate_policy", validate_policy)
    builder.add_node("clarify_request", clarify_request)
    builder.add_node("prepare_kubernetes", prepare_kubernetes)
    builder.add_node("prepare_plan", prepare_plan)
    builder.add_node("create_plan", create_plan)
    builder.add_node("execute_kubernetes", execute_kubernetes)
    builder.add_node("execute_plan", execute_plan)
    builder.add_node("validate_evidence", validate_evidence)
    builder.add_node(
        "reject_default",
        _fixed_response(DEFAULT_REJECT_RESPONSE),
    )
    builder.add_node(
        "reject_out_of_scope",
        _fixed_response(OUT_OF_SCOPE_RESPONSE),
    )
    builder.add_node(
        "reject_unsupported",
        _fixed_response(UNSUPPORTED_RESPONSE),
    )
    builder.add_node(
        "reject_plan",
        _fixed_response(PLAN_REJECTED_RESPONSE),
    )
    builder.add_edge(START, "interpret_request")
    builder.add_edge("interpret_request", "validate_policy")
    builder.add_conditional_edges(
        "validate_policy",
        choose_route,
        {
            PolicyAction.EXECUTE_KUBERNETES.value: "prepare_kubernetes",
            PolicyAction.CREATE_PLAN.value: "prepare_plan",
            PolicyAction.CLARIFY_REQUEST.value: "clarify_request",
            PolicyAction.REJECT_DEFAULT.value: "reject_default",
            PolicyAction.REJECT_OUT_OF_SCOPE.value: "reject_out_of_scope",
            PolicyAction.REJECT_UNSUPPORTED.value: "reject_unsupported",
        },
    )
    builder.add_edge("prepare_kubernetes", "execute_kubernetes")
    builder.add_edge("prepare_plan", "create_plan")
    builder.add_conditional_edges(
        "create_plan",
        choose_plan_result,
        {
            "execute_plan": "execute_plan",
            "reject_plan": "reject_plan",
        },
    )
    builder.add_edge("execute_kubernetes", "validate_evidence")
    builder.add_edge("execute_plan", "validate_evidence")
    builder.add_edge("validate_evidence", END)
    builder.add_edge("clarify_request", END)
    builder.add_edge("reject_default", END)
    builder.add_edge("reject_out_of_scope", END)
    builder.add_edge("reject_unsupported", END)
    builder.add_edge("reject_plan", END)
    return OpsAgent(builder.compile())


def _fixed_response(content: str):
    def respond(_: _OpsGraphState) -> dict[str, object]:
        return {"messages": [AIMessage(content=content)]}

    return respond


def _last_user_question(state: _OpsGraphState) -> str:
    for message in reversed(state["messages"]):
        if message.type == "human" and isinstance(message.content, str):
            return message.content
    return ""


def _prior_assistant_answer(state: _OpsGraphState) -> str | None:
    found_latest_user = False
    for message in reversed(state["messages"]):
        if message.type == "human":
            found_latest_user = True
            continue
        if (
            found_latest_user
            and message.type == "ai"
            and isinstance(message.content, str)
        ):
            return message.content
    return None


def _prior_capability(state: _OpsGraphState) -> CapabilityId | None:
    found_latest_user = False
    for message in reversed(state["messages"]):
        if message.type == "human":
            found_latest_user = True
            continue
        if not found_latest_user or message.type != "ai":
            continue
        raw_capability = message.additional_kwargs.get(
            _PENDING_CAPABILITY_MESSAGE_KEY
        ) or message.additional_kwargs.get(_CAPABILITY_MESSAGE_KEY)
        if isinstance(raw_capability, str):
            try:
                return CapabilityId(raw_capability)
            except ValueError:
                return None
    return None


def _conversation_request(state: _OpsGraphState) -> str:
    question = _last_user_question(state)
    messages = state["messages"]
    latest_user_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if messages[index].type == "human"
        ),
        0,
    )
    history = [
        f"{'用户' if message.type == 'human' else '助手'}：{message.content}"
        for message in messages[:latest_user_index]
        if message.type in {"human", "ai"} and isinstance(message.content, str)
    ]
    if not history:
        return question
    parts = [
        part
        for part in (
            "同一会话的历史：\n" + "\n".join(history) if history else "",
            f"当前用户请求：{question}",
        )
        if part
    ]
    return "\n".join(parts)


def _interaction_context(state: _OpsGraphState) -> InteractionContext:
    context = state.get("interaction_context")
    return context if isinstance(context, InteractionContext) else InteractionContext()


def _policy_progress_message(decision: PolicyDecision) -> str:
    if decision.capability is not None:
        return f"已通过只读能力校验：{decision.capability.value}"
    if decision.action is PolicyAction.CLARIFY_REQUEST:
        return "请求需要进一步澄清"
    return "请求已完成策略校验"
