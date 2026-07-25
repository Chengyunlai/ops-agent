from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from ops_agent.settings import KubernetesSettings, SettingsError, load_settings


def test_load_settings_from_toml(tmp_path: Path):
    config_path = tmp_path / "test.toml"
    config_path.write_text(
        """
        [kubernetes]
        environment = "local-test"
        namespace = "operations"
        kubeconfig_path = "/tmp/ops_agent-kubeconfig"
        request_timeout_seconds = 17

        [model]
        provider = "openai"
        model = "test-model"
        """,
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.kubernetes.environment == "local-test"
    assert settings.kubernetes.namespace == "operations"
    assert settings.kubernetes.kubeconfig_path == Path(
        "/tmp/ops_agent-kubeconfig"
    )
    assert settings.kubernetes.request_timeout_seconds == 17
    assert settings.model.provider == "openai"
    assert settings.model.name == "test-model"


def test_load_settings_rejects_missing_file(tmp_path: Path):
    missing_path = tmp_path / "does-not-exist.toml"

    with pytest.raises(SettingsError, match="配置文件不存在"):
        load_settings(missing_path)


def test_load_settings_rejects_invalid_toml(tmp_path: Path):
    config_path = tmp_path / "invalid.toml"
    config_path.write_text("[kubernetes\n", encoding="utf-8")

    with pytest.raises(SettingsError, match="配置文件格式错误"):
        load_settings(config_path)


def test_load_settings_rejects_missing_kubernetes_section(tmp_path: Path):
    config_path = tmp_path / "missing-section.toml"
    config_path.write_text('environment = "test"\n', encoding="utf-8")

    with pytest.raises(SettingsError, match=r"\[kubernetes\]"):
        load_settings(config_path)


def test_load_settings_reports_all_missing_fields(tmp_path: Path):
    config_path = tmp_path / "missing-fields.toml"
    config_path.write_text(
        """
        [kubernetes]
        environment = "test"
        request_timeout_seconds = 10
        """,
        encoding="utf-8",
    )

    with pytest.raises(SettingsError) as error:
        load_settings(config_path)

    message = str(error.value)
    assert "namespace" in message
    assert "kubeconfig_path" in message


def test_load_settings_rejects_missing_model_section(tmp_path: Path):
    config_path = tmp_path / "missing-model.toml"
    config_path.write_text(
        """
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops_agent-kubeconfig"
        request_timeout_seconds = 10
        """,
        encoding="utf-8",
    )

    with pytest.raises(SettingsError, match=r"\[model\]"):
        load_settings(config_path)


def test_load_settings_rejects_empty_model_values(tmp_path: Path):
    config_path = tmp_path / "empty-model.toml"
    config_path.write_text(
        """
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops_agent-kubeconfig"
        request_timeout_seconds = 10

        [model]
        provider = ""
        model = ""
        """,
        encoding="utf-8",
    )

    with pytest.raises(SettingsError) as error:
        load_settings(config_path)

    message = str(error.value)
    assert "provider" in message
    assert "model" in message


def test_kubernetes_settings_immutable():
    settings = KubernetesSettings(
        environment="test",
        namespace="sample",
        kubeconfig_path=Path("/tmp/ops_agent-kubeconfig"),
        request_timeout_seconds=10,
    )

    with pytest.raises(FrozenInstanceError):
        settings.environment = "new-env"
