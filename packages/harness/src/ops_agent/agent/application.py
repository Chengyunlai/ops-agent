"""运维 Agent 的应用门面。"""

from dataclasses import dataclass
from typing import Protocol


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
