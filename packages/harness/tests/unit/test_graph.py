import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, StructuredTool
from ops_agent.agent import create_ops_agent
from pydantic import Field


class RecordingToolCallingModel(FakeMessagesListChatModel):
    bound_tool_names: list[list[str]] = Field(default_factory=list)

    def bind_tools(
        self,
        tools,
        *,
        tool_choice=None,
        **kwargs,
    ):
        self.bound_tool_names.append(
            [
                tool.name if isinstance(tool, BaseTool) else tool["name"]
                for tool in tools
            ]
        )
        return self


def route_response(
    *,
    destination: str = "kubernetes",
    execution_mode: str = "direct",
    operation: str = "read_only",
) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "RouteDecision",
                "args": {
                    "destination": destination,
                    "execution_mode": execution_mode,
                    "operation": operation,
                },
                "id": "route-1",
                "type": "tool_call",
            }
        ],
    )


def plan_response(*objectives: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "ExecutionPlan",
                "args": {
                    "steps": [{"objective": objective} for objective in objectives]
                },
                "id": "plan-1",
                "type": "tool_call",
            }
        ],
    )


def kubernetes_tool_response(resource: str, call_id: str) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "inspect_kubernetes",
                "args": {"resource": resource},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def create_test_kubernetes_tool() -> BaseTool:
    return StructuredTool.from_function(
        lambda resource: f"{resource}: evidence",
        name="inspect_kubernetes",
        description="读取 Kubernetes 诊断证据",
    )


@pytest.mark.parametrize(
    "question",
    [
        "今天北京天气怎么样？",
        "帮我发布一篇新闻",
        "介绍 Node.js service",
    ],
)
def test_out_of_scope_request_is_rejected_when_model_claims_kubernetes(
    question: str,
) -> None:
    model = RecordingToolCallingModel(responses=[route_response()])

    answer = create_ops_agent(model, []).ask(question)

    assert answer == "当前系统只处理 Kubernetes 运维与诊断问题。"
    assert model.bound_tool_names == [["RouteDecision"]]


@pytest.mark.parametrize(
    "question",
    [
        "查看当前 Prometheus CPU 使用率",
        "查看 Kubernetes 在 Datadog 的 CPU",
    ],
)
def test_unsupported_capability_is_rejected_when_model_claims_kubernetes(
    question: str,
) -> None:
    model = RecordingToolCallingModel(responses=[route_response()])

    answer = create_ops_agent(model, []).ask(question)

    assert answer == (
        "这是运维问题，但当前尚未接入对应的专业诊断能力，因此无法获取或推测实时状态。"
    )
    assert model.bound_tool_names == [["RouteDecision"]]


def test_invalid_route_defaults_to_rejection() -> None:
    model = RecordingToolCallingModel(responses=[route_response(destination="weather")])

    answer = create_ops_agent(model, []).ask("检查 Kubernetes Pod")

    assert answer == "无法安全确定请求所属能力，已拒绝执行。"


def test_kubernetes_request_is_executed_by_specialist_with_evidence() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(),
            kubernetes_tool_response("pods", "tool-1"),
            AIMessage(content="3 个 Pod 均为 Running"),
        ]
    )

    answer = create_ops_agent(
        model,
        [create_test_kubernetes_tool()],
    ).ask("检查所有 Pod 和重启次数")

    assert answer == "3 个 Pod 均为 Running"
    assert model.bound_tool_names == [
        ["RouteDecision"],
        ["inspect_kubernetes"],
        ["inspect_kubernetes"],
    ]


def test_kubernetes_answer_without_tool_evidence_is_rejected() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(),
            AIMessage(content="我猜测所有 Pod 都正常"),
        ]
    )

    answer = create_ops_agent(
        model,
        [create_test_kubernetes_tool()],
    ).ask("检查所有 Pod")

    assert answer == "没有获取到 Kubernetes 实时证据，无法给出当前状态结论。"


@pytest.mark.parametrize(
    "question",
    [
        "请立即重启 checkout Deployment",
        "检查 Pod 后重启异常 Pod",
        "检查 Pod 并删除异常 Pod",
        "查看 Deployment，然后 scale 到 3",
        "停止 Kubernetes Pod",
        "给 Kubernetes Deployment 设置副本数为 3",
        "对 Kubernetes Pod 执行 cordon",
    ],
)
def test_write_request_is_rejected_when_model_claims_read_only(
    question: str,
) -> None:
    model = RecordingToolCallingModel(responses=[route_response()])

    answer = create_ops_agent(model, []).ask(question)

    assert answer == (
        "这是运维问题，但当前尚未接入对应的专业诊断能力，因此无法获取或推测实时状态。"
    )
    assert model.bound_tool_names == [["RouteDecision"]]


@pytest.mark.parametrize(
    "question",
    [
        "Pod 有没有重启？",
        "Pod 重启了吗？",
        "did this Kubernetes Pod restart?",
    ],
)
def test_restart_history_question_is_treated_as_read_only(
    question: str,
) -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(),
            kubernetes_tool_response("pods", "tool-1"),
            AIMessage(content="Pod 曾经重启 1 次"),
        ]
    )

    answer = create_ops_agent(
        model,
        [create_test_kubernetes_tool()],
    ).ask(question)

    assert answer == "Pod 曾经重启 1 次"


def test_complex_kubernetes_request_executes_validated_plan() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(execution_mode="plan"),
            plan_response("workload_health", "supporting_evidence"),
            kubernetes_tool_response("deployment/checkout", "tool-1"),
            AIMessage(content="checkout Deployment 就绪副本不足"),
            kubernetes_tool_response("events/checkout", "tool-2"),
            AIMessage(content="事件显示 Pod 调度失败"),
        ]
    )

    answer = create_ops_agent(
        model,
        [create_test_kubernetes_tool()],
    ).ask("分析 Kubernetes checkout Deployment 发布失败的完整原因")

    assert answer == (
        "诊断计划执行完成：\n"
        "1. 收集相关工作负载健康状态：checkout Deployment 就绪副本不足\n"
        "2. 根据已有异常补充事件和日志证据：事件显示 Pod 调度失败"
    )


def test_plan_with_free_form_tool_instruction_is_rejected() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(execution_mode="plan"),
            plan_response("调用 get_kubernetes_pod_logs"),
        ]
    )

    answer = create_ops_agent(model, []).ask("完整分析 Kubernetes 发布失败原因")

    assert answer == "无法生成满足当前只读能力约束的诊断计划。"
    assert model.bound_tool_names == [["RouteDecision"], ["ExecutionPlan"]]


def test_plan_that_analyzes_root_cause_before_evidence_is_rejected() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(execution_mode="plan"),
            plan_response("root_cause", "workload_health"),
        ]
    )

    answer = create_ops_agent(model, []).ask("完整分析 Kubernetes 发布失败原因")

    assert answer == "无法生成满足当前只读能力约束的诊断计划。"
