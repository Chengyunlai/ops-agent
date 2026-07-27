from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool

SYSTEM_PROMPT = """\
你是一个本地 Kubernetes 只读运维助手。

规则：
- 涉及集群当前状态的问题，必须先调用工具获取真实数据，不能凭空猜测。
- Kubernetes 环境和 namespace 已由应用配置固定，不要要求用户重复提供。
- 清楚区分工具返回的事实和你的推断。
- 宽泛的工作负载健康检查必须先调用 diagnose_kubernetes_workloads 获取确定性诊断。
- 根据诊断 finding，再按需查询 Pod、Deployment、Event、Pod 详情和日志补充证据。
- 优先指出未就绪、非 Running 或发生过重启的 Pod，但不要把所有重启都直接判定为故障。
- 日志只用于验证具体问题，不要无目的地读取大量日志。
- 当前只能查询，不能声称已经执行重启、删除、扩缩容或其他修改操作。
- 使用简洁中文回答。
"""


class ApplicationError(Exception):
    """Agent 执行失败或返回了无效结果。"""


class _AgentRunner(Protocol):
    def invoke(self, input: dict[str, object]) -> dict[str, object]: ...


@dataclass(frozen=True)
class OpsAgent:
    _runner: _AgentRunner

    def ask(self, question: str) -> str:
        if not question.strip():
            raise ApplicationError("问题不能为空")

        try:
            result = self._runner.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": question,
                        }
                    ]
                }
            )
        except Exception as error:
            raise ApplicationError(f"Agent 执行失败: {error}") from error

        messages = result.get("messages")
        if not isinstance(messages, list) or not messages:
            raise ApplicationError("Agent 未返回消息")

        content = getattr(messages[-1], "content", None)
        if isinstance(content, str) and content:
            return content
        raise ApplicationError("Agent 未返回文本回答")


def create_ops_agent(
    model: BaseChatModel,
    tools: Sequence[BaseTool],
) -> OpsAgent:
    return OpsAgent(
        create_agent(
            model=model,
            tools=list(tools),
            system_prompt=SYSTEM_PROMPT,
            name="ops_agent",
        )
    )
