"""运维 Agent 的应用门面。"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Protocol

from ops_agent.agent.models import (
    AgentEvent,
    AgentStage,
    InteractionContext,
)


class ApplicationError(Exception):
    """Agent 执行失败或返回了无效结果。"""


class _AgentRunner(Protocol):
    def invoke(self, input: dict[str, object]) -> dict[str, object]: ...

    def stream(
        self,
        input: dict[str, object],
        *,
        stream_mode: str,
    ) -> Iterator[dict[str, object]]: ...


def _question_message(question: str) -> dict[str, str]:
    return {
        "role": "user",
        "content": question,
    }


def _answer_from_result(result: dict[str, object]) -> tuple[str, list[object]]:
    messages = result.get("messages")
    if not isinstance(messages, list) or not messages:
        raise ApplicationError("Agent 未返回消息")

    content = getattr(messages[-1], "content", None)
    if isinstance(content, str) and content:
        return content, messages
    raise ApplicationError("Agent 未返回文本回答")


@dataclass(frozen=True)
class OpsAgent:
    _runner: _AgentRunner

    def ask(self, question: str) -> str:
        if not question.strip():
            raise ApplicationError("问题不能为空")

        try:
            result = self._runner.invoke(
                {
                    "messages": [_question_message(question)],
                }
            )
        except Exception as error:
            raise ApplicationError(f"Agent 执行失败: {error}") from error

        answer, _ = _answer_from_result(result)
        return answer

    def open_session(self, context: InteractionContext) -> ConversationSession:
        return ConversationSession(self._runner, context)


@dataclass(frozen=True)
class ConversationSession:
    _runner: _AgentRunner
    context: InteractionContext
    _messages: list[object] = field(default_factory=list)

    def ask(self, question: str) -> str:
        if not question.strip():
            raise ApplicationError("问题不能为空")

        input_messages = [*self._messages, _question_message(question)]
        try:
            result = self._runner.invoke(
                {
                    "messages": input_messages,
                    "interaction_context": self.context,
                }
            )
        except Exception as error:
            raise ApplicationError(f"Agent 执行失败: {error}") from error

        answer, messages = _answer_from_result(result)
        self._messages[:] = messages
        return answer

    def stream(self, question: str) -> Iterator[AgentEvent]:
        if not question.strip():
            raise ApplicationError("问题不能为空")

        input_messages = [*self._messages, _question_message(question)]
        yield AgentEvent(
            stage=AgentStage.UNDERSTANDING,
            message="正在理解问题",
        )

        response_messages: list[object] | None = None
        try:
            updates = self._runner.stream(
                {
                    "messages": input_messages,
                    "interaction_context": self.context,
                },
                stream_mode="updates",
            )
            for update in updates:
                for node_update in update.values():
                    if not isinstance(node_update, dict):
                        continue
                    progress_event = node_update.get("progress_event")
                    if isinstance(progress_event, AgentEvent):
                        yield progress_event
                    messages = node_update.get("messages")
                    if isinstance(messages, list) and messages:
                        response_messages = messages
        except Exception as error:
            raise ApplicationError(f"Agent 执行失败: {error}") from error

        if response_messages is None:
            raise ApplicationError("Agent 未返回消息")
        answer, messages = _answer_from_result({"messages": response_messages})
        self._messages[:] = [*input_messages, *messages]
        yield AgentEvent(
            stage=AgentStage.COMPLETED,
            message="回答已完成",
            answer=answer,
        )
