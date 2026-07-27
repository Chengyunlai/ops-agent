from pathlib import Path
from types import SimpleNamespace

import pytest
from ops_agent import bootstrap as bootstrap_module
from ops_agent.bootstrap import (
    BootstrapError,
    OpsApplication,
    create_application,
)


class FakeAgent:
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


def test_application_asks_agent() -> None:
    agent = FakeAgent("sample 中有 3 个 Running Pod")
    application = OpsApplication(
        settings=SimpleNamespace(),
        agent=agent,
    )

    answer = application.ask("检查所有 Pod")

    assert answer == "sample 中有 3 个 Running Pod"
    assert agent.inputs == [
        {
            "messages": [
                {
                    "role": "user",
                    "content": "检查所有 Pod",
                }
            ]
        }
    ]


def test_create_application_wires_configured_namespace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "test.toml"
    config_path.write_text(
        """
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops-agent-kubeconfig"
        request_timeout_seconds = 10

        [model]
        provider = "openai"
        model = "test-model"
        base_url = "https://api.deepseek.com"
        api_key_env = "DEEPSEEK_API_KEY"
        """,
        encoding="utf-8",
    )
    reader = object()
    tools = [object()]
    model = object()
    agent = FakeAgent("answer")
    calls: dict[str, object] = {}

    def fake_create_reader(settings):
        calls["kubernetes_settings"] = settings
        return reader

    def fake_create_tools(received_reader, *, namespace):
        calls["reader"] = received_reader
        calls["namespace"] = namespace
        return tools

    def fake_init_chat_model(**kwargs):
        calls["model_kwargs"] = kwargs
        return model

    def fake_create_agent(received_model, received_tools):
        calls["model"] = received_model
        calls["tools"] = received_tools
        return agent

    monkeypatch.setattr(
        bootstrap_module,
        "create_kubernetes_reader",
        fake_create_reader,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "create_kubernetes_tools",
        fake_create_tools,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "init_chat_model",
        fake_init_chat_model,
    )
    monkeypatch.setattr(
        bootstrap_module,
        "create_ops_agent",
        fake_create_agent,
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-api-key")

    application = create_application(config_path)

    assert application.agent is agent
    assert calls["namespace"] == "sample"
    assert calls["reader"] is reader
    assert calls["model_kwargs"] == {
        "model": "test-model",
        "model_provider": "openai",
        "temperature": 0,
        "base_url": "https://api.deepseek.com",
        "api_key": "test-api-key",
    }
    assert calls["model"] is model
    assert calls["tools"] == tools


def test_create_application_requires_configured_api_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "test.toml"
    config_path.write_text(
        """
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops-agent-kubeconfig"
        request_timeout_seconds = 10

        [model]
        provider = "openai"
        model = "deepseek-v4-pro"
        base_url = "https://api.deepseek.com"
        api_key_env = "DEEPSEEK_API_KEY"
        """,
        encoding="utf-8",
    )
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(
        BootstrapError,
        match="DEEPSEEK_API_KEY",
    ):
        create_application(config_path)
