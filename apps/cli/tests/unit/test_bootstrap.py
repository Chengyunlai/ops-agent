from pathlib import Path

import pytest
from ops_agent_cli import bootstrap as bootstrap_module
from ops_agent_cli.bootstrap import BootstrapError, create_application


def write_config(config_path: Path, *, model: str) -> None:
    config_path.write_text(
        f"""
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops-agent-kubeconfig"
        request_timeout_seconds = 10

        [model]
        provider = "openai"
        model = "{model}"
        base_url = "https://api.deepseek.com"
        api_key_env = "DEEPSEEK_API_KEY"
        """,
        encoding="utf-8",
    )


def test_create_application_wires_configured_namespace(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "test.toml"
    write_config(config_path, model="test-model")
    reader = object()
    tools = [object()]
    model = object()
    agent = object()
    monitor = object()
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

    def fake_create_monitor(received_reader, *, namespace):
        calls["monitor_reader"] = received_reader
        calls["monitor_namespace"] = namespace
        return monitor

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
    monkeypatch.setattr(
        bootstrap_module,
        "KubernetesMonitor",
        fake_create_monitor,
    )
    monkeypatch.setenv("DEEPSEEK_API_KEY", "test-api-key")

    application = create_application(config_path)

    assert application is agent
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
    assert calls["monitor_reader"] is reader
    assert calls["monitor_namespace"] == "sample"


def test_create_application_requires_configured_api_key(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "test.toml"
    write_config(config_path, model="deepseek-v4-pro")
    monkeypatch.delenv("DEEPSEEK_API_KEY", raising=False)

    with pytest.raises(
        BootstrapError,
        match="DEEPSEEK_API_KEY",
    ):
        create_application(config_path)
