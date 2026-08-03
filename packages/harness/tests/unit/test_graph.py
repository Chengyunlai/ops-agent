import pytest
from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage
from langchain_core.tools import BaseTool, StructuredTool
from ops_agent.agent import (
    AgentStage,
    CapabilityScope,
    InteractionChannel,
    InteractionContext,
    create_ops_agent,
)
from ops_agent.agent.orchestration.routing import IntentInterpreter
from pydantic import Field


class RecordingToolCallingModel(FakeMessagesListChatModel):
    bound_tool_names: list[list[str]] = Field(default_factory=list)
    received_message_contents: list[list[str]] = Field(default_factory=list)
    received_message_records: list[list[tuple[str, str]]] = Field(default_factory=list)

    def _generate(
        self,
        messages,
        stop=None,
        run_manager=None,
        **kwargs,
    ):
        self.received_message_contents.append(
            [
                message.content
                for message in messages
                if isinstance(message.content, str)
            ]
        )
        self.received_message_records.append(
            [
                (type(message).__name__, message.content)
                for message in messages
                if isinstance(message.content, str)
            ]
        )
        return super()._generate(
            messages,
            stop=stop,
            run_manager=run_manager,
            **kwargs,
        )

    def bind_tools(
        self,
        tools,
        *,
        tool_choice=None,
        **kwargs,
    ):
        self.bound_tool_names.append(
            [
                (
                    tool.name
                    if isinstance(tool, BaseTool)
                    else (tool["name"] if isinstance(tool, dict) else tool.__name__)
                )
                for tool in tools
            ]
        )
        return self


class JsonOnlyModel(FakeMessagesListChatModel):
    def bind_tools(
        self,
        tools,
        *,
        tool_choice=None,
        **kwargs,
    ):
        raise NotImplementedError


def route_response(
    *,
    destination: str = "kubernetes",
    execution_mode: str = "direct",
    operation: str = "read_only",
    resource: str = "kubernetes",
    result_shape: str = "diagnosis",
    ambiguities: tuple[str, ...] = (),
) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": "IntentProposal",
                "args": {
                    "destination": destination,
                    "execution_mode": execution_mode,
                    "operation": operation,
                    "resource": resource,
                    "result_shape": result_shape,
                    "ambiguities": list(ambiguities),
                },
                "id": "proposal-1",
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


def kubernetes_tool_response(
    resource: str,
    call_id: str,
    *,
    tool_name: str = "diagnose_kubernetes_workloads",
) -> AIMessage:
    return AIMessage(
        content="",
        tool_calls=[
            {
                "name": tool_name,
                "args": {"resource": resource},
                "id": call_id,
                "type": "tool_call",
            }
        ],
    )


def create_test_kubernetes_tool(
    name: str = "diagnose_kubernetes_workloads",
) -> BaseTool:
    if name == "diagnose_kubernetes_workloads":
        return StructuredTool.from_function(
            lambda resource=None: {
                "namespace": "sample",
                "resource": resource,
                "findings": [],
            },
            name=name,
            description="读取 Kubernetes 诊断证据",
        )
    return StructuredTool.from_function(
        lambda resource: f"{resource}: evidence",
        name=name,
        description="读取 Kubernetes 诊断证据",
    )


def create_test_kubernetes_tools() -> list[BaseTool]:
    return [
        create_test_kubernetes_tool(name)
        for name in (
            "diagnose_kubernetes_workloads",
            "get_kubernetes_pod_details",
            "get_kubernetes_pod_logs",
            "list_kubernetes_deployments",
            "list_kubernetes_events",
            "list_kubernetes_pods",
            "list_kubernetes_service_endpoints",
            "list_kubernetes_services",
        )
    ]


def create_test_pod_tools() -> list[BaseTool]:
    return [
        create_test_kubernetes_tool("get_kubernetes_pod_details"),
        create_test_kubernetes_tool("list_kubernetes_pods"),
    ]


def create_test_service_tools() -> list[BaseTool]:
    return [
        create_test_kubernetes_tool("list_kubernetes_service_endpoints"),
        create_test_kubernetes_tool("list_kubernetes_services"),
    ]


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
    assert model.bound_tool_names == [["IntentProposal"]]


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
    assert model.bound_tool_names == [["IntentProposal"]]


def test_invalid_route_defaults_to_rejection() -> None:
    model = RecordingToolCallingModel(responses=[route_response(destination="weather")])

    answer = create_ops_agent(model, []).ask("检查 Kubernetes Pod")

    assert answer == "无法安全确定请求所属能力，已拒绝执行。"


def test_kubernetes_request_is_executed_by_specialist_with_evidence() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(),
            kubernetes_tool_response(
                "pods",
                "tool-1",
                tool_name="list_kubernetes_pods",
            ),
            AIMessage(content="3 个 Pod 均为 Running"),
        ]
    )

    answer = create_ops_agent(
        model,
        create_test_pod_tools(),
    ).ask("检查所有 Pod 和重启次数")

    assert answer == "3 个 Pod 均为 Running"
    assert model.bound_tool_names == [
        ["IntentProposal"],
        ["get_kubernetes_pod_details", "list_kubernetes_pods"],
        ["get_kubernetes_pod_details", "list_kubernetes_pods"],
    ]


@pytest.mark.parametrize(
    "question",
    [
        "sample现在几个服务",
        "sample namespace 现在几个服务",
        "services in namespace sample 现在有几个",
    ],
)
def test_kubernetes_scoped_session_understands_contextual_service_count(
    question: str,
) -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(resource="pod", result_shape="count"),
            kubernetes_tool_response(
                "services",
                "tool-1",
                tool_name="list_kubernetes_services",
            ),
            AIMessage(content="sample namespace 中有 4 个 Service"),
        ]
    )
    session = create_ops_agent(
        model,
        [
            create_test_kubernetes_tool("list_kubernetes_service_endpoints"),
            create_test_kubernetes_tool("list_kubernetes_services"),
            create_test_kubernetes_tool(),
        ],
    ).open_session(
        InteractionContext(
            channel=InteractionChannel.TUI,
            scope=CapabilityScope.KUBERNETES,
            environment="test",
            namespace="sample",
        )
    )

    events = list(session.stream(question))

    assert [event.stage for event in events] == [
        AgentStage.UNDERSTANDING,
        AgentStage.INTENT_INTERPRETED,
        AgentStage.POLICY_VALIDATED,
        AgentStage.QUERYING,
        AgentStage.EVIDENCE_VALIDATED,
        AgentStage.COMPLETED,
    ]
    assert events[-1].answer == "sample namespace 中有 4 个 Service"
    assert model.bound_tool_names == [
        ["IntentProposal"],
        ["list_kubernetes_service_endpoints", "list_kubernetes_services"],
        ["list_kubernetes_service_endpoints", "list_kubernetes_services"],
    ]
    assert not any(
        "可信执行上下文" in content for content in model.received_message_contents[-1]
    )


@pytest.mark.parametrize(
    "intent_content",
    [
        (
            '{"destination":"kubernetes","execution_mode":"direct",'
            '"operation":"read_only","resource":"service",'
            '"result_shape":"count","ambiguities":[]}'
        ),
        (
            "```json\n"
            '{"destination":"kubernetes","execution_mode":"direct",'
            '"operation":"read_only","resource":"service",'
            '"result_shape":"count","ambiguities":[]}\n'
            "```"
        ),
        (
            "<think>"
            '{"destination":"kubernetes","execution_mode":"direct",'
            '"operation":"read_only","resource":"pod",'
            '"result_shape":"count","ambiguities":[]}'
            "</think>\n"
            '{"destination":"kubernetes","execution_mode":"direct",'
            '"operation":"read_only","resource":"service",'
            '"result_shape":"count","ambiguities":[]}'
        ),
        [
            {
                "type": "text",
                "text": (
                    '{"destination":"kubernetes","execution_mode":"direct",'
                    '"operation":"read_only","resource":"service",'
                    '"result_shape":"count","ambiguities":[]}'
                ),
            }
        ],
    ],
)
def test_kubernetes_session_accepts_json_intent_from_compatible_model(
    intent_content: str | list[dict[str, str]],
) -> None:
    model = RecordingToolCallingModel(
        responses=[
            AIMessage(content=intent_content),
            kubernetes_tool_response(
                "services",
                "tool-1",
                tool_name="list_kubernetes_services",
            ),
            AIMessage(content="sample namespace 中有 4 个 Service"),
        ]
    )
    session = create_ops_agent(
        model,
        create_test_service_tools(),
    ).open_session(
        InteractionContext(
            channel=InteractionChannel.TUI,
            scope=CapabilityScope.KUBERNETES,
            environment="test",
            namespace="sample",
        )
    )

    events = list(session.stream("sample现在几个服务"))

    assert events[-1].answer == "sample namespace 中有 4 个 Service"


@pytest.mark.parametrize(
    "intent_content",
    [
        (
            '{"wrapper":{"destination":"kubernetes","execution_mode":"direct",'
            '"operation":"read_only","resource":"service",'
            '"result_shape":"count","ambiguities":[]}}'
        ),
        (
            '{"destination":"kubernetes","execution_mode":"direct",'
            '"operation":"read_only","resource":"service",'
            '"result_shape":"count","ambiguities":[]}'
            '{"destination":"kubernetes","execution_mode":"direct",'
            '"operation":"read_only","resource":"pod",'
            '"result_shape":"count","ambiguities":[]}'
        ),
    ],
)
def test_kubernetes_session_rejects_wrapped_or_multiple_json_intents(
    intent_content: str,
) -> None:
    model = RecordingToolCallingModel(responses=[AIMessage(content=intent_content)])
    session = create_ops_agent(
        model,
        create_test_service_tools(),
    ).open_session(
        InteractionContext(
            channel=InteractionChannel.TUI,
            scope=CapabilityScope.KUBERNETES,
            environment="test",
            namespace="sample",
        )
    )

    answer = session.ask("sample现在几个服务")

    assert answer == "无法安全确定请求所属能力，已拒绝执行。"


def test_intent_interpreter_falls_back_when_tool_binding_is_unsupported() -> None:
    model = JsonOnlyModel(
        responses=[
            AIMessage(
                content=(
                    '{"destination":"kubernetes","execution_mode":"direct",'
                    '"operation":"read_only","resource":"service",'
                    '"result_shape":"count","ambiguities":[]}'
                )
            )
        ]
    )

    proposal = IntentInterpreter(model).suggest(
        [],
        InteractionContext(),
    )

    assert proposal is not None
    assert proposal.resource.value == "service"


def test_kubernetes_scope_rejects_unregistered_capability() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(resource="pod", result_shape="count"),
        ]
    )
    session = create_ops_agent(
        model,
        [create_test_kubernetes_tool("list_kubernetes_pods")],
    ).open_session(
        InteractionContext(
            channel=InteractionChannel.TUI,
            scope=CapabilityScope.KUBERNETES,
            environment="test",
            namespace="sample",
        )
    )

    answer = session.ask("现在有几个服务")

    assert answer == (
        "这是运维问题，但当前尚未接入对应的专业诊断能力，因此无法获取或推测实时状态。"
    )
    assert model.bound_tool_names == [["IntentProposal"]]


@pytest.mark.parametrize(
    "question",
    [
        "今天北京天气怎么样？",
        "帮我发布一篇新闻",
        "介绍 Node.js service",
        "讲个笑话",
        "你有几个朋友？",
    ],
)
def test_kubernetes_scope_rejects_clear_non_operations_requests(
    question: str,
) -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(resource="service"),
        ]
    )
    session = create_ops_agent(
        model,
        create_test_service_tools(),
    ).open_session(
        InteractionContext(
            channel=InteractionChannel.TUI,
            scope=CapabilityScope.KUBERNETES,
            environment="test",
            namespace="sample",
        )
    )

    answer = session.ask(question)

    assert answer == "当前系统只处理 Kubernetes 运维与诊断问题。"
    assert model.bound_tool_names == [["IntentProposal"]]


@pytest.mark.parametrize(
    "question",
    [
        "prod namespace 现在几个服务",
        "namespace prod 现在几个服务",
        "生产环境现在几个服务",
    ],
)
def test_kubernetes_scope_does_not_accept_requested_scope_override(
    question: str,
) -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(resource="pod", result_shape="count"),
            route_response(resource="pod", result_shape="count"),
            kubernetes_tool_response(
                "services",
                "tool-1",
                tool_name="list_kubernetes_services",
            ),
            AIMessage(content="sample namespace 中有 4 个 Service"),
        ]
    )
    session = create_ops_agent(
        model,
        create_test_service_tools(),
    ).open_session(
        InteractionContext(
            channel=InteractionChannel.TUI,
            scope=CapabilityScope.KUBERNETES,
            environment="test",
            namespace="sample",
        )
    )

    answer = session.ask(question)

    assert answer == (
        "当前会话固定为环境 test、namespace sample，不能切换到请求中的其他 scope。"
        "你是否要继续查询当前固定 scope？"
    )
    assert model.bound_tool_names == [["IntentProposal"]]

    confirmed_answer = session.ask("是")

    assert confirmed_answer == "sample namespace 中有 4 个 Service"
    assert model.bound_tool_names == [
        ["IntentProposal"],
        ["list_kubernetes_service_endpoints", "list_kubernetes_services"],
        ["list_kubernetes_service_endpoints", "list_kubernetes_services"],
    ]


def test_kubernetes_scope_accepts_matching_chinese_environment() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(resource="service", result_shape="detail"),
            kubernetes_tool_response(
                "services",
                "tool-1",
                tool_name="list_kubernetes_services",
            ),
            AIMessage(content="生产环境的 Service 均正常"),
        ]
    )
    session = create_ops_agent(
        model,
        create_test_service_tools(),
    ).open_session(
        InteractionContext(
            channel=InteractionChannel.TUI,
            scope=CapabilityScope.KUBERNETES,
            environment="生产",
            namespace="sample",
        )
    )

    answer = session.ask("查看生产环境的服务")

    assert answer == "生产环境的 Service 均正常"


def test_auto_scope_clarifies_ambiguous_service_language() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(resource="pod", result_shape="count"),
        ]
    )

    answer = create_ops_agent(model, []).ask("现在有几个服务")

    assert answer == "你指的是 Kubernetes Service 吗？"


def test_auto_scope_accepts_confirmation_with_conversation_history() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(resource="pod", result_shape="count"),
            route_response(resource="pod", result_shape="count"),
            kubernetes_tool_response(
                "services",
                "tool-1",
                tool_name="list_kubernetes_services",
            ),
            AIMessage(content="当前有 4 个 Kubernetes Service"),
        ]
    )
    session = create_ops_agent(
        model,
        create_test_service_tools(),
    ).open_session(InteractionContext())

    clarification = session.ask("现在有几个服务")
    answer = session.ask("是")

    assert clarification == "你指的是 Kubernetes Service 吗？"
    assert answer == "当前有 4 个 Kubernetes Service"
    specialist_request = model.received_message_contents[-1][1]
    assert "用户：现在有几个服务" in specialist_request
    assert "助手：你指的是 Kubernetes Service 吗？" in specialist_request
    assert "当前用户请求：是" in specialist_request
    assert "可信执行上下文" not in specialist_request


def test_referential_follow_up_keeps_previous_capability() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(resource="service", result_shape="count"),
            kubernetes_tool_response(
                "services",
                "tool-1",
                tool_name="list_kubernetes_services",
            ),
            AIMessage(content="当前有 4 个 Kubernetes Service"),
            route_response(resource="pod", result_shape="diagnosis"),
            kubernetes_tool_response(
                "services",
                "tool-2",
                tool_name="list_kubernetes_services",
            ),
            AIMessage(content="其中 sample-api Service 需要检查"),
        ]
    )
    session = create_ops_agent(
        model,
        create_test_service_tools(),
    ).open_session(
        InteractionContext(
            channel=InteractionChannel.TUI,
            scope=CapabilityScope.KUBERNETES,
            environment="test",
            namespace="sample",
        )
    )

    session.ask("现在有几个服务")
    answer = session.ask("哪个有问题？")

    assert answer == "其中 sample-api Service 需要检查"
    assert model.bound_tool_names == [
        ["IntentProposal"],
        ["list_kubernetes_service_endpoints", "list_kubernetes_services"],
        ["list_kubernetes_service_endpoints", "list_kubernetes_services"],
        ["list_kubernetes_service_endpoints", "list_kubernetes_services"],
        ["list_kubernetes_service_endpoints", "list_kubernetes_services"],
    ]


def test_auto_scope_referential_follow_up_uses_previous_capability() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(resource="service", result_shape="detail"),
            kubernetes_tool_response(
                "services",
                "tool-1",
                tool_name="list_kubernetes_services",
            ),
            AIMessage(content="Kubernetes Service 状态已获取"),
            route_response(resource="pod", result_shape="diagnosis"),
            kubernetes_tool_response(
                "services",
                "tool-2",
                tool_name="list_kubernetes_services",
            ),
            AIMessage(content="sample-api Service 需要检查"),
        ]
    )
    session = create_ops_agent(
        model,
        create_test_service_tools(),
    ).open_session(InteractionContext())

    session.ask("查看 Kubernetes Service 状态")
    answer = session.ask("哪个有问题？")

    assert answer == "sample-api Service 需要检查"


@pytest.mark.parametrize(
    "question",
    [
        "Kubernetes 现在有几个 Service",
        "查看 Kubernetes Service",
    ],
)
def test_simple_service_request_cannot_expand_to_plan_capability(
    question: str,
) -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(
                execution_mode="plan",
                resource="service",
                result_shape="diagnosis",
            ),
            kubernetes_tool_response(
                "services",
                "tool-1",
                tool_name="list_kubernetes_services",
            ),
            AIMessage(content="当前有 4 个 Kubernetes Service"),
        ]
    )

    answer = create_ops_agent(
        model,
        create_test_kubernetes_tools(),
    ).ask(question)

    assert answer == "当前有 4 个 Kubernetes Service"
    assert model.bound_tool_names == [
        ["IntentProposal"],
        ["list_kubernetes_service_endpoints", "list_kubernetes_services"],
        ["list_kubernetes_service_endpoints", "list_kubernetes_services"],
    ]


def test_diagnostic_follow_up_reuses_complete_diagnostic_capability() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(execution_mode="plan"),
            plan_response("workload_health"),
            kubernetes_tool_response("workloads", "tool-1"),
            AIMessage(content="发现一个异常工作负载"),
            route_response(resource="pod", result_shape="diagnosis"),
            kubernetes_tool_response("workloads", "tool-2"),
            AIMessage(content="checkout Deployment 有问题"),
        ]
    )
    session = create_ops_agent(
        model,
        create_test_kubernetes_tools(),
    ).open_session(
        InteractionContext(
            channel=InteractionChannel.TUI,
            scope=CapabilityScope.KUBERNETES,
            environment="test",
            namespace="sample",
        )
    )

    session.ask("完整分析 Kubernetes 工作负载异常")
    answer = session.ask("哪个有问题？")

    assert answer == "checkout Deployment 有问题"
    diagnostic_tool_names = [tool.name for tool in create_test_kubernetes_tools()]
    assert model.bound_tool_names[-2:] == [
        diagnostic_tool_names,
        diagnostic_tool_names,
    ]


def test_kubernetes_scope_clarifies_unknown_resource() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(resource="unknown", result_shape="unknown"),
        ]
    )
    session = create_ops_agent(model, []).open_session(
        InteractionContext(
            channel=InteractionChannel.TUI,
            scope=CapabilityScope.KUBERNETES,
            environment="test",
            namespace="sample",
        )
    )

    answer = session.ask("帮我看看现在怎么样")

    assert answer == (
        "当前环境是 test，namespace 是 sample。"
        "你想查询 Pod、Deployment、Service、Event 还是日志？"
    )


def test_kubernetes_scope_still_rejects_write_request() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(resource="service", result_shape="detail"),
        ]
    )
    session = create_ops_agent(model, []).open_session(
        InteractionContext(
            channel=InteractionChannel.TUI,
            scope=CapabilityScope.KUBERNETES,
            environment="test",
            namespace="sample",
        )
    )

    answer = session.ask("删除这个服务")

    assert answer == (
        "这是运维问题，但当前尚未接入对应的专业诊断能力，因此无法获取或推测实时状态。"
    )


def test_kubernetes_answer_without_tool_evidence_is_rejected() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(),
            AIMessage(content="我猜测所有 Pod 都正常"),
        ]
    )

    answer = create_ops_agent(
        model,
        create_test_pod_tools(),
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
    assert model.bound_tool_names == [["IntentProposal"]]


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
            kubernetes_tool_response(
                "pods",
                "tool-1",
                tool_name="list_kubernetes_pods",
            ),
            AIMessage(content="Pod 曾经重启 1 次"),
        ]
    )

    answer = create_ops_agent(
        model,
        create_test_pod_tools(),
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
        create_test_kubernetes_tools(),
    ).ask("分析 Kubernetes checkout Deployment 发布失败的完整原因")

    assert answer == (
        "诊断计划执行完成：\n"
        "1. 收集相关工作负载健康状态：checkout Deployment 就绪副本不足\n"
        "2. 根据已有异常补充事件和日志证据：事件显示 Pod 调度失败"
    )
    diagnostic_tool_names = [tool.name for tool in create_test_kubernetes_tools()]
    assert model.bound_tool_names == [
        ["IntentProposal"],
        ["ExecutionPlan"],
        diagnostic_tool_names,
        diagnostic_tool_names,
        diagnostic_tool_names,
        diagnostic_tool_names,
    ]


def test_plan_collects_required_pod_evidence_without_model_tool_choice() -> None:
    evidence_calls: list[tuple[str, object]] = []

    def diagnose_workloads() -> dict[str, object]:
        evidence_calls.append(("diagnostics", None))
        return {
            "namespace": "sample",
            "findings": [
                {
                    "severity": "warning",
                    "code": "pod_crash_loop",
                    "resource_kind": "Pod",
                    "resource_name": "checkout-api",
                    "summary": "display text can change independently",
                    "container_name": "api",
                    "evidence": [],
                }
            ],
        }

    def list_events(
        pod_name: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        evidence_calls.append(("events", (pod_name, limit)))
        return [{"reason": "BackOff", "object_name": pod_name}]

    def get_logs(
        pod_name: str,
        container: str | None = None,
        tail_lines: int = 200,
        previous: bool = False,
    ) -> dict[str, object]:
        evidence_calls.append(("logs", (pod_name, container, tail_lines, previous)))
        return {"logs": "previous instance failed", "previous": previous}

    tools = [
        StructuredTool.from_function(
            diagnose_workloads,
            name="diagnose_kubernetes_workloads",
            description="diagnose",
        ),
        StructuredTool.from_function(
            get_logs,
            name="get_kubernetes_pod_logs",
            description="logs",
        ),
        StructuredTool.from_function(
            list_events,
            name="list_kubernetes_events",
            description="events",
        ),
        *[
            create_test_kubernetes_tool(name)
            for name in (
                "get_kubernetes_pod_details",
                "list_kubernetes_deployments",
                "list_kubernetes_pods",
                "list_kubernetes_service_endpoints",
                "list_kubernetes_services",
            )
        ],
    ]
    model = RecordingToolCallingModel(
        responses=[
            route_response(execution_mode="plan"),
            plan_response("workload_health"),
            AIMessage(content="checkout-api 正在 CrashLoopBackOff"),
        ]
    )

    answer = create_ops_agent(model, tools).ask(
        "完整分析 Kubernetes checkout 发布失败原因"
    )

    assert answer == (
        "诊断计划执行完成：\n"
        "1. 收集相关工作负载健康状态：checkout-api 正在 CrashLoopBackOff"
    )
    assert evidence_calls == [
        ("diagnostics", None),
        ("events", ("checkout-api", 100)),
        ("logs", ("checkout-api", "api", 200, True)),
    ]
    assert any(
        message_type == "ToolMessage" and "previous instance failed" in content
        for message_batch in model.received_message_records
        for message_type, content in message_batch
    )
    assert all(
        "CONTROLLED_EVIDENCE" not in content
        for message_batch in model.received_message_records
        for message_type, content in message_batch
        if message_type == "HumanMessage"
    )


def test_plan_with_free_form_tool_instruction_is_rejected() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(execution_mode="plan"),
            plan_response("调用 get_kubernetes_pod_logs"),
        ]
    )

    answer = create_ops_agent(
        model,
        create_test_kubernetes_tools(),
    ).ask("完整分析 Kubernetes 发布失败原因")

    assert answer == "无法生成满足当前只读能力约束的诊断计划。"
    assert model.bound_tool_names == [["IntentProposal"], ["ExecutionPlan"]]


def test_plan_that_analyzes_root_cause_before_evidence_is_rejected() -> None:
    model = RecordingToolCallingModel(
        responses=[
            route_response(execution_mode="plan"),
            plan_response("root_cause", "workload_health"),
        ]
    )

    answer = create_ops_agent(
        model,
        create_test_kubernetes_tools(),
    ).ask("完整分析 Kubernetes 发布失败原因")

    assert answer == "无法生成满足当前只读能力约束的诊断计划。"
