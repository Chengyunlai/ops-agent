from pathlib import Path

import pytest
from ops_agent_cli.configuration import (
    KubernetesSettings,
    SettingsError,
    ThemeName,
    load_settings,
    save_settings,
)
from pydantic import ValidationError


@pytest.mark.parametrize("environment", ["dev", "test", "prod"])
def test_example_configs_are_valid(environment: str) -> None:
    project_root = Path(__file__).resolve().parents[5]

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
        proxy_url = "http://127.0.0.1:7897"

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
    assert settings.kubernetes.kubeconfig_path == Path("/tmp/ops_agent-kubeconfig")
    assert settings.kubernetes.request_timeout_seconds == 17
    assert str(settings.kubernetes.proxy_url) == "http://127.0.0.1:7897/"
    assert settings.model.provider == "openai"
    assert settings.model.name == "test-model"
    assert settings.model.base_url == "https://api.deepseek.com"
    assert settings.model.api_key_env == "DEEPSEEK_API_KEY"
    assert not settings.kubernetes.interactive_exec.enabled
    assert settings.kubernetes.interactive_exec.locale == "auto"
    assert settings.kubernetes.interactive_exec.terminal_type == "xterm-256color"
    assert settings.kubernetes.interactive_exec.color
    assert settings.kubernetes.downloads.directory == Path("~/Downloads/ops-agent")
    assert settings.kubernetes.pod_transfer.strategy.value == "auto"
    assert settings.kubernetes.pod_transfer.max_file_size_mb == 512


def test_load_settings_parses_manual_pod_access_configuration(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "test.toml"
    config_path.write_text(
        """
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops_agent-kubeconfig"
        request_timeout_seconds = 10

        [kubernetes.interactive_exec]
        enabled = true
        locale = "zh_CN.UTF-8"
        terminal_type = "screen-256color"
        color = false

        [kubernetes.downloads]
        directory = "/tmp/ops-agent-downloads"

        [model]
        provider = "openai"
        model = "test-model"
        """,
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.kubernetes.interactive_exec.enabled
    assert settings.kubernetes.interactive_exec.locale == "zh_CN.UTF-8"
    assert settings.kubernetes.interactive_exec.terminal_type == "screen-256color"
    assert not settings.kubernetes.interactive_exec.color
    assert settings.kubernetes.downloads.directory == Path("/tmp/ops-agent-downloads")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("locale", '"C UTF-8"'),
        ("terminal_type", '"xterm;reset"'),
        ("color", '"true"'),
    ],
)
def test_load_settings_rejects_invalid_interactive_exec_configuration(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    config_path = tmp_path / "invalid-interactive-exec.toml"
    config_path.write_text(
        f"""
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops_agent-kubeconfig"
        request_timeout_seconds = 10

        [kubernetes.interactive_exec]
        {field} = {value}

        [model]
        provider = "openai"
        model = "test-model"
        """,
        encoding="utf-8",
    )

    with pytest.raises(SettingsError, match=field):
        load_settings(config_path)


def test_load_settings_parses_pod_transfer_configuration(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "test.toml"
    config_path.write_text(
        """
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops_agent-kubeconfig"
        request_timeout_seconds = 10

        [kubernetes.pod_transfer]
        strategy = "exec-dd"
        max_file_size_mb = 64

        [model]
        provider = "openai"
        model = "test-model"
        """,
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.kubernetes.pod_transfer.strategy.value == "exec-dd"
    assert settings.kubernetes.pod_transfer.max_file_size_mb == 64


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("strategy", '"unknown"'),
        ("max_file_size_mb", "0"),
    ],
)
def test_load_settings_rejects_invalid_pod_transfer_configuration(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    config_path = tmp_path / "invalid-pod-transfer.toml"
    config_path.write_text(
        f"""
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops_agent-kubeconfig"
        request_timeout_seconds = 10

        [kubernetes.pod_transfer]
        {field} = {value}

        [model]
        provider = "openai"
        model = "test-model"
        """,
        encoding="utf-8",
    )

    with pytest.raises(SettingsError, match=field):
        load_settings(config_path)


def test_load_settings_parses_project_and_tui_preferences(tmp_path: Path) -> None:
    config_path = tmp_path / "test.toml"
    config_path.write_text(
        """
        [project]
        name = "Testing"

        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops_agent-kubeconfig"
        request_timeout_seconds = 10

        [model]
        provider = "openai"
        model = "test-model"

        [tui]
        theme = "light"

        [tui.colors]
        primary = "#005FB8"
        warning = "#A15C00"
        """,
        encoding="utf-8",
    )

    settings = load_settings(config_path)

    assert settings.project.name == "Testing"
    assert settings.tui.theme == "light"
    assert settings.tui.colors.primary == "#005FB8"
    assert settings.tui.colors.warning == "#A15C00"
    assert settings.tui.colors.accent is None


def test_save_settings_persists_validated_preferences_and_runtime_config(
    tmp_path: Path,
) -> None:
    config_path = tmp_path / "test.toml"
    config_path.write_text(
        """
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops_agent-kubeconfig"
        request_timeout_seconds = 10

        [model]
        provider = "openai"
        model = "test-model"
        """,
        encoding="utf-8",
    )
    settings = load_settings(config_path)
    updated = settings.model_copy(
        update={
            "project": settings.project.model_copy(update={"name": "Sample Platform"}),
            "kubernetes": settings.kubernetes.model_copy(
                update={"namespace": "sample-next"}
            ),
            "tui": settings.tui.model_copy(
                update={
                    "theme": ThemeName.HIGH_CONTRAST,
                    "colors": settings.tui.colors.model_copy(
                        update={"accent": "#FFFF00"}
                    ),
                }
            ),
        }
    )

    save_settings(config_path, updated)
    reloaded = load_settings(config_path)

    assert reloaded.project.name == "Sample Platform"
    assert reloaded.kubernetes.namespace == "sample-next"
    assert reloaded.model.name == "test-model"
    assert reloaded.tui.theme is ThemeName.HIGH_CONTRAST
    assert reloaded.tui.colors.accent == "#FFFF00"
    assert 'model = "test-model"' in config_path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("field", "value"),
    [
        ("theme", '"unknown"'),
        ("colors.primary", '"red"'),
        ("colors.accent", '"#12345G"'),
    ],
)
def test_load_settings_rejects_unknown_theme_and_invalid_colors(
    tmp_path: Path,
    field: str,
    value: str,
) -> None:
    config_path = tmp_path / "invalid-tui.toml"
    tui_lines = (
        f"theme = {value}\n"
        if field == "theme"
        else f'theme = "ops-dark"\n\n[tui.colors]\n{field.removeprefix("colors.")} = {value}\n'
    )
    config_path.write_text(
        f"""
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops_agent-kubeconfig"
        request_timeout_seconds = 10

        [model]
        provider = "openai"
        model = "test-model"

        [tui]
        {tui_lines}
        """,
        encoding="utf-8",
    )

    with pytest.raises(SettingsError, match=field.split(".")[-1]):
        load_settings(config_path)


def test_load_settings_rejects_missing_file(tmp_path: Path):
    missing_path = tmp_path / "does-not-exist.toml"

    with pytest.raises(SettingsError, match="配置文件不存在"):
        load_settings(missing_path)


def test_load_settings_rejects_invalid_toml(tmp_path: Path):
    config_path = tmp_path / "invalid.toml"
    config_path.write_text("[kubernetes\n", encoding="utf-8")

    with pytest.raises(SettingsError, match="配置文件格式错误"):
        load_settings(config_path)


def test_load_settings_translates_file_read_errors(tmp_path: Path) -> None:
    with pytest.raises(SettingsError, match="配置文件无法读取"):
        load_settings(tmp_path)


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


@pytest.mark.parametrize("kubeconfig_path", ['""', '"   "'])
def test_load_settings_rejects_empty_kubeconfig_path(
    tmp_path: Path,
    kubeconfig_path: str,
) -> None:
    config_path = tmp_path / "empty-kubeconfig.toml"
    config_path.write_text(
        f"""
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = {kubeconfig_path}
        request_timeout_seconds = 10

        [model]
        provider = "openai"
        model = "deepseek-v4-pro"
        """,
        encoding="utf-8",
    )

    with pytest.raises(SettingsError, match="kubeconfig_path"):
        load_settings(config_path)


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
    "proxy_url",
    [
        '""',
        '"ftp://127.0.0.1:7897"',
        '"not-a-url"',
        "123",
    ],
)
def test_load_settings_rejects_invalid_kubernetes_proxy_url(
    tmp_path: Path,
    proxy_url: str,
) -> None:
    config_path = tmp_path / "invalid-proxy.toml"
    config_path.write_text(
        f"""
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops_agent-kubeconfig"
        request_timeout_seconds = 10
        proxy_url = {proxy_url}

        [model]
        provider = "openai"
        model = "deepseek-v4-pro"
        """,
        encoding="utf-8",
    )

    with pytest.raises(SettingsError, match="proxy_url"):
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


def test_load_settings_rejects_unknown_fields(tmp_path: Path) -> None:
    config_path = tmp_path / "unknown-field.toml"
    config_path.write_text(
        """
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops_agent-kubeconfig"
        request_timeout_seconds = 10
        unexpected = "value"

        [model]
        provider = "openai"
        model = "deepseek-v4-pro"
        """,
        encoding="utf-8",
    )

    with pytest.raises(SettingsError, match="unexpected"):
        load_settings(config_path)


def test_load_settings_does_not_expose_invalid_input_values(tmp_path: Path) -> None:
    secret = "sk-secret-value"
    config_path = tmp_path / "secret-value.toml"
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
        api_key = "{secret}"
        """,
        encoding="utf-8",
    )

    with pytest.raises(SettingsError) as error:
        load_settings(config_path)

    assert secret not in str(error.value)
    assert error.value.__cause__ is None
    assert error.value.__context__ is None


def test_kubernetes_settings_immutable():
    settings = KubernetesSettings(
        environment="test",
        namespace="sample",
        kubeconfig_path=Path("/tmp/ops_agent-kubeconfig"),
        request_timeout_seconds=10,
    )

    with pytest.raises(ValidationError, match="frozen_instance"):
        settings.environment = "new-env"
