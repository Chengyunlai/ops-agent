"""Kubernetes 专业 Agent 子图。"""

from collections.abc import Sequence

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.messages import AIMessage, ToolMessage
from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, Field

KUBERNETES_AGENT_PROMPT = """\
你是 Kubernetes 只读诊断子 Agent。

规则：
- 只处理 Kubernetes 查询和诊断；其他问题不要回答。
- 涉及集群当前状态的问题，必须先调用工具获取真实数据，不能凭空猜测。
- Kubernetes 环境和 namespace 已由应用配置固定，不要要求用户重复提供。
- 清楚区分工具返回的事实和你的推断。
- 宽泛的工作负载健康检查必须先调用 diagnose_kubernetes_workloads 获取确定性诊断。
- 根据诊断 finding，再按需查询 Pod、Deployment、Event、Pod 详情和日志补充证据。
- 优先指出未就绪、非 Running 或发生过重启的 Pod，但不要把所有重启都直接判定为故障。
- 日志只用于验证具体问题，不要无目的地读取大量日志。
- 当前只能查询，不能声称已经执行重启、删除、扩缩容或其他修改操作。
- 返回简洁中文诊断结果。
"""


class KubernetesResult(BaseModel):
    model_config = ConfigDict(frozen=True)

    answer: str | None = None
    evidence_count: int = Field(ge=0)

    @property
    def is_grounded(self) -> bool:
        return bool(self.answer) and self.evidence_count > 0


class KubernetesAgent:
    """隐藏 Kubernetes ReAct 子图、响应解析和工具证据提取。"""

    def __init__(
        self,
        model: BaseChatModel,
        tools: Sequence[BaseTool],
    ) -> None:
        self._runner = create_agent(
            model=model,
            tools=list(tools),
            system_prompt=KUBERNETES_AGENT_PROMPT,
            name="kubernetes_diagnostics_agent",
        )

    def diagnose(self, request: str) -> KubernetesResult:
        try:
            result = self._runner.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": request,
                        }
                    ]
                }
            )
        except Exception:  # noqa: BLE001 - 专业 Agent 失败按无证据处理
            return KubernetesResult(evidence_count=0)

        messages = result.get("messages")
        if not isinstance(messages, list):
            return KubernetesResult(evidence_count=0)
        return KubernetesResult(
            answer=_last_text_answer(messages),
            evidence_count=sum(
                isinstance(message, ToolMessage) and message.status == "success"
                for message in messages
            ),
        )


def _last_text_answer(messages: list[object]) -> str | None:
    for message in reversed(messages):
        content = getattr(message, "content", None)
        if isinstance(message, AIMessage) and isinstance(content, str) and content:
            return content
    return None
