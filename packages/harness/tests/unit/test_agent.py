from types import SimpleNamespace

import pytest
from ops_agent.agent import ApplicationError, OpsAgent


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
