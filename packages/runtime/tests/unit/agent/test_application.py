from dataclasses import FrozenInstanceError
from types import SimpleNamespace

import pytest
from ops_agent.agent import (
    AgentEvent,
    AgentStage,
    ApplicationError,
    InteractionContext,
    OpsAgent,
)
from pydantic import ValidationError


class FakeAgentRunner:
    def __init__(self, answer: str) -> None:
        self.answer = answer
        self.inputs: list[dict[str, object]] = []

    def invoke(self, input: dict[str, object]) -> dict[str, object]:
        self.inputs.append(input)
        return {
            "messages": [
                SimpleNamespace(content=self.answer),
            ]
        }


class FailingAgentRunner:
    def invoke(self, input: dict[str, object]) -> dict[str, object]:
        raise RuntimeError("provider unavailable")


class InvalidAgentRunner:
    def __init__(self, result: dict[str, object]) -> None:
        self.result = result

    def invoke(self, input: dict[str, object]) -> dict[str, object]:
        return self.result


class FakeStreamingRunner:
    def __init__(self) -> None:
        self.inputs: list[dict[str, object]] = []

    def stream(
        self,
        input: dict[str, object],
        *,
        stream_mode: str,
    ):
        self.inputs.append(input)
        assert stream_mode == "updates"
        yield {
            "interpret_request": {
                "progress_event": AgentEvent(
                    stage=AgentStage.INTENT_INTERPRETED,
                    message="已理解查询意图",
                )
            }
        }
        yield {
            "execute_kubernetes": {
                "progress_event": AgentEvent(
                    stage=AgentStage.QUERYING,
                    message="正在查询 Kubernetes Service",
                )
            }
        }
        yield {
            "validate_evidence": {
                "messages": [
                    SimpleNamespace(content="sample 中有 4 个 Service"),
                ],
                "progress_event": AgentEvent(
                    stage=AgentStage.EVIDENCE_VALIDATED,
                    message="已验证实时证据",
                ),
            }
        }


class ConversationalRunner:
    def __init__(self, answers: list[str]) -> None:
        self.answers = iter(answers)
        self.inputs: list[dict[str, object]] = []

    def invoke(self, input: dict[str, object]) -> dict[str, object]:
        self.inputs.append(input)
        messages = input["messages"]
        assert isinstance(messages, list)
        return {
            "messages": [
                *messages,
                SimpleNamespace(content=next(self.answers)),
            ]
        }


class ConversationalStreamingRunner:
    def __init__(self, answers: list[str]) -> None:
        self.answers = iter(answers)
        self.inputs: list[dict[str, object]] = []

    def stream(
        self,
        input: dict[str, object],
        *,
        stream_mode: str,
    ):
        self.inputs.append(input)
        assert stream_mode == "updates"
        yield {
            "validate_evidence": {
                "messages": [
                    SimpleNamespace(content=next(self.answers)),
                ],
            }
        }


def test_agent_answers_question() -> None:
    runner = FakeAgentRunner("sample 中有 3 个 Running Pod")
    agent = OpsAgent(runner)

    answer = agent.ask("检查所有 Pod")

    assert answer == "sample 中有 3 个 Running Pod"
    assert runner.inputs == [
        {
            "messages": [
                {
                    "role": "user",
                    "content": "检查所有 Pod",
                }
            ]
        }
    ]


def test_agent_rejects_empty_question() -> None:
    runner = FakeAgentRunner("unused")
    agent = OpsAgent(runner)

    with pytest.raises(ApplicationError, match="问题不能为空"):
        agent.ask("   ")

    assert runner.inputs == []


def test_conversation_streams_stable_progress_and_answer_events() -> None:
    runner = FakeStreamingRunner()
    session = OpsAgent(runner).open_session(InteractionContext())

    events = list(session.stream("查询 Kubernetes Service 数量"))

    assert [event.stage for event in events] == [
        AgentStage.UNDERSTANDING,
        AgentStage.INTENT_INTERPRETED,
        AgentStage.QUERYING,
        AgentStage.EVIDENCE_VALIDATED,
        AgentStage.COMPLETED,
    ]
    assert events[-1].answer == "sample 中有 4 个 Service"


def test_conversation_carries_previous_turns() -> None:
    runner = ConversationalRunner(
        [
            "sample 中有 4 个 Service",
            "其中 sample-api 需要进一步检查",
        ]
    )
    session = OpsAgent(runner).open_session(InteractionContext())

    session.ask("查询 Kubernetes Service 数量")
    answer = session.ask("哪个有问题？")

    assert answer == "其中 sample-api 需要进一步检查"
    second_messages = runner.inputs[1]["messages"]
    assert isinstance(second_messages, list)
    assert [
        getattr(message, "content", message.get("content"))
        if isinstance(message, dict)
        else message.content
        for message in second_messages
    ] == [
        "查询 Kubernetes Service 数量",
        "sample 中有 4 个 Service",
        "哪个有问题？",
    ]


def test_streaming_conversation_carries_previous_turns() -> None:
    runner = ConversationalStreamingRunner(
        [
            "sample 中有 4 个 Service",
            "其中 sample-api 需要进一步检查",
        ]
    )
    session = OpsAgent(runner).open_session(InteractionContext())

    list(session.stream("查询 Kubernetes Service 数量"))
    events = list(session.stream("哪个有问题？"))

    assert events[-1].answer == "其中 sample-api 需要进一步检查"
    second_messages = runner.inputs[1]["messages"]
    assert isinstance(second_messages, list)
    assert [
        message["content"] if isinstance(message, dict) else message.content
        for message in second_messages
    ] == [
        "查询 Kubernetes Service 数量",
        "sample 中有 4 个 Service",
        "哪个有问题？",
    ]


def test_kubernetes_context_rejects_blank_runtime_fields() -> None:
    with pytest.raises(
        ValidationError,
        match="at least 1 character",
    ):
        InteractionContext(
            scope="kubernetes",
            environment=" ",
            namespace="sample",
        )


def test_conversation_context_cannot_change_between_turns() -> None:
    session = OpsAgent(FakeAgentRunner("unused")).open_session(InteractionContext())

    with pytest.raises(FrozenInstanceError):
        session.context = InteractionContext(scope="auto")


def test_agent_event_requires_answer_only_when_completed() -> None:
    with pytest.raises(ValidationError, match="完成事件必须包含回答"):
        AgentEvent(
            stage=AgentStage.COMPLETED,
            message="回答已完成",
        )
    with pytest.raises(ValidationError, match="只有完成事件可以包含回答"):
        AgentEvent(
            stage=AgentStage.QUERYING,
            message="正在查询",
            answer="不应出现",
        )


def test_agent_translates_runner_failure() -> None:
    agent = OpsAgent(FailingAgentRunner())

    with pytest.raises(ApplicationError, match="Agent 执行失败"):
        agent.ask("检查所有 Pod")


@pytest.mark.parametrize(
    ("result", "message"),
    [
        ({}, "Agent 未返回消息"),
        ({"messages": []}, "Agent 未返回消息"),
        (
            {"messages": [SimpleNamespace(content=None)]},
            "Agent 未返回文本回答",
        ),
    ],
)
def test_agent_rejects_invalid_runner_response(
    result: dict[str, object],
    message: str,
) -> None:
    agent = OpsAgent(InvalidAgentRunner(result))

    with pytest.raises(ApplicationError, match=message):
        agent.ask("检查所有 Pod")
