from dataclasses import FrozenInstanceError
from pathlib import Path

import pytest

from ops_agent.settings import KubernetesSettings, SettingsError, load_settings


@pytest.mark.parametrize("environment", ["dev", "test", "prod"])
def test_example_configs_are_valid(environment: str) -> None:
    project_root = Path(__file__).resolve().parents[4]

    settings = load_settings(
        project_root / "config" / "examples" / f"{environment}.toml"
    )

    assert settings.kubernetes.environment == environment


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
        base_url = "https://api.deepseek.com"
        api_key_env = "DEEPSEEK_API_KEY"
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
    assert settings.model.base_url == "https://api.deepseek.com"
    assert settings.model.api_key_env == "DEEPSEEK_API_KEY"


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


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("environment", "123"),
        ("namespace", "123"),
        ("kubeconfig_path", "123"),
        ("provider", "123"),
        ("model", "123"),
    ],
)
def test_load_settings_rejects_non_string_required_fields(
    tmp_path: Path,
    field_name: str,
    field_value: str,
) -> None:
    kubernetes_fields = {
        "environment": '"test"',
        "namespace": '"sample"',
        "kubeconfig_path": '"/tmp/ops_agent-kubeconfig"',
    }
    model_fields = {
        "provider": '"openai"',
        "model": '"deepseek-v4-pro"',
    }
    if field_name in kubernetes_fields:
        kubernetes_fields[field_name] = field_value
    else:
        model_fields[field_name] = field_value

    config_path = tmp_path / "invalid-required-string.toml"
    config_path.write_text(
        f"""
        [kubernetes]
        environment = {kubernetes_fields["environment"]}
        namespace = {kubernetes_fields["namespace"]}
        kubeconfig_path = {kubernetes_fields["kubeconfig_path"]}
        request_timeout_seconds = 10

        [model]
        provider = {model_fields["provider"]}
        model = {model_fields["model"]}
        """,
        encoding="utf-8",
    )

    with pytest.raises(SettingsError, match=field_name):
        load_settings(config_path)


@pytest.mark.parametrize("timeout_value", ["0", "-1", '"10"', "true"])
def test_load_settings_rejects_invalid_request_timeout(
    tmp_path: Path,
    timeout_value: str,
) -> None:
    config_path = tmp_path / "invalid-timeout.toml"
    config_path.write_text(
        f"""
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops_agent-kubeconfig"
        request_timeout_seconds = {timeout_value}

        [model]
        provider = "openai"
        model = "deepseek-v4-pro"
        """,
        encoding="utf-8",
    )

    with pytest.raises(
        SettingsError,
        match="request_timeout_seconds",
    ):
        load_settings(config_path)


@pytest.mark.parametrize(
    ("field_name", "field_value"),
    [
        ("base_url", '""'),
        ("api_key_env", '"   "'),
        ("base_url", "123"),
    ],
)
def test_load_settings_rejects_invalid_optional_model_strings(
    tmp_path: Path,
    field_name: str,
    field_value: str,
) -> None:
    config_path = tmp_path / "invalid-model-option.toml"
    config_path.write_text(
        f"""
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops_agent-kubeconfig"
        request_timeout_seconds = 10

        [model]
        provider = "openai"
        model = "deepseek-v4-pro"
        {field_name} = {field_value}
        """,
        encoding="utf-8",
    )

    with pytest.raises(SettingsError, match=field_name):
        load_settings(config_path)


def test_kubernetes_settings_immutable():
    settings = KubernetesSettings(
        environment="test",
        namespace="sample",
        kubeconfig_path=Path("/tmp/ops_agent-kubeconfig"),
        request_timeout_seconds=10,
    )

    with pytest.raises(FrozenInstanceError):
        settings.environment = "new-env"
