from langchain_core.language_models.fake_chat_models import (
    FakeMessagesListChatModel,
)
from langchain_core.messages import AIMessage, BaseMessage
from langchain_core.tools import BaseTool, StructuredTool
from ops_agent.graph import create_ops_agent
from pydantic import Field


class RecordingToolCallingModel(FakeMessagesListChatModel):
    bound_tool_names: list[list[str]] = Field(default_factory=list)
    received_messages: list[list[BaseMessage]] = Field(default_factory=list)

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

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        self.received_messages.append(messages)
        return super()._generate(messages, stop, run_manager, **kwargs)


def test_supervisor_delegates_kubernetes_request_to_sub_agent() -> None:
    model = RecordingToolCallingModel(
        responses=[
            AIMessage(
                content="",
                tool_calls=[
                    {
                        "name": "kubernetes_diagnostics",
                        "args": {"request": "检查 sample namespace 的 Pod"},
                        "id": "delegate-1",
                        "type": "tool_call",
                    }
                ],
            ),
            AIMessage(content="3 个 Pod 均为 Running"),
            AIMessage(content="集群检查完成：3 个 Pod 均为 Running"),
        ]
    )

    kubernetes_tool = StructuredTool.from_function(
        lambda: "unused",
        name="inspect_kubernetes",
        description="读取 Kubernetes 状态",
    )

    agent = create_ops_agent(model, [kubernetes_tool])

    answer = agent.ask("检查集群")

    assert answer == "集群检查完成：3 个 Pod 均为 Running"
    assert model.received_messages[1][-1].content == "检查 sample namespace 的 Pod"
    assert model.bound_tool_names == [
        ["kubernetes_diagnostics"],
        ["inspect_kubernetes"],
        ["kubernetes_diagnostics"],
    ]
