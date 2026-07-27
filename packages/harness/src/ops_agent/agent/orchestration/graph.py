"""受控主图的 State、Node 与 Edge 拓扑。"""

from collections.abc import Sequence

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool
from langgraph.graph import END, START, MessagesState, StateGraph

from ops_agent.agent.application import OpsAgent
from ops_agent.agent.orchestration.routing import (
    RequestRouter,
    RouteAction,
    RouteDecision,
    decide_route,
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


class _OpsGraphState(MessagesState):
    route_suggestion: RouteDecision | None
    candidate_answer: str | None
    evidence_count: int
    plan: ExecutionPlan | None


def create_ops_agent(
    model: BaseChatModel,
    kubernetes_tools: Sequence[BaseTool],
) -> OpsAgent:
    """构建受控主图和独立的 Kubernetes 诊断子图。"""

    router = RequestRouter(model)
    planner = KubernetesDiagnosticPlanner(model)
    kubernetes_agent = KubernetesAgent(model, kubernetes_tools)
    plan_executor = KubernetesPlanExecutor(kubernetes_agent)

    def classify_request(state: _OpsGraphState) -> dict[str, object]:
        return {
            "route_suggestion": router.suggest(state["messages"]),
        }

    def choose_route(state: _OpsGraphState) -> str:
        return decide_route(
            _last_user_question(state),
            state.get("route_suggestion"),
        ).value

    def execute_kubernetes(state: _OpsGraphState) -> dict[str, object]:
        result = kubernetes_agent.diagnose(_last_user_question(state))
        return {
            "candidate_answer": result.answer,
            "evidence_count": result.evidence_count,
        }

    def create_plan(state: _OpsGraphState) -> dict[str, object]:
        return {
            "plan": planner.create(_last_user_question(state)),
        }

    def choose_plan_result(state: _OpsGraphState) -> str:
        return "execute_plan" if state.get("plan") is not None else "reject_plan"

    def execute_plan(state: _OpsGraphState) -> dict[str, object]:
        plan = state.get("plan")
        if plan is None:
            return {"candidate_answer": None, "evidence_count": 0}
        result = plan_executor.execute(_last_user_question(state), plan)
        return {
            "candidate_answer": result.answer,
            "evidence_count": result.evidence_count,
        }

    def validate_evidence(state: _OpsGraphState) -> dict[str, object]:
        answer = state.get("candidate_answer")
        if state.get("evidence_count", 0) < 1 or not answer:
            answer = NO_EVIDENCE_RESPONSE
        return {"messages": [AIMessage(content=answer)]}

    builder = StateGraph(_OpsGraphState)
    builder.add_node("classify_request", classify_request)
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
    builder.add_edge(START, "classify_request")
    builder.add_conditional_edges(
        "classify_request",
        choose_route,
        {
            action.value: action.value
            for action in (
                RouteAction.EXECUTE_KUBERNETES,
                RouteAction.CREATE_PLAN,
                RouteAction.REJECT_DEFAULT,
                RouteAction.REJECT_OUT_OF_SCOPE,
                RouteAction.REJECT_UNSUPPORTED,
            )
        },
    )
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
