from pathlib import Path
from types import SimpleNamespace

import pytest
from ops_agent_cli import installation as installation_module
from ops_agent_cli import main as main_module
from ops_agent_cli.main import main


def test_main_uses_installed_config_path_by_default(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    config_home = tmp_path / "config-home"
    monkeypatch.setenv("XDG_CONFIG_HOME", str(config_home))
    monkeypatch.delenv("OPS_AGENT_CONFIG", raising=False)
    settings = SimpleNamespace(
        kubernetes=SimpleNamespace(
            environment="test",
            namespace="sample",
        )
    )

    def fake_load_settings(config_path: Path):
        assert config_path == config_home / "ops-agent/config.toml"
        return settings

    monkeypatch.setattr(
        main_module,
        "load_settings",
        fake_load_settings,
    )

    exit_code = main([])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Kubernetes 环境: test" in captured.out
    assert "命名空间: sample" in captured.out
    assert captured.err == ""


def test_main_uses_config_environment_variable_before_installed_default(
    tmp_path,
    monkeypatch,
    capsys,
) -> None:
    environment_config = tmp_path / "configured.toml"
    monkeypatch.setenv("OPS_AGENT_CONFIG", str(environment_config))
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "ignored"))
    received_paths: list[Path] = []
    settings = SimpleNamespace(
        kubernetes=SimpleNamespace(
            environment="test",
            namespace="sample",
        )
    )

    def fake_load_settings(config_path: Path):
        received_paths.append(config_path)
        return settings

    monkeypatch.setattr(main_module, "load_settings", fake_load_settings)

    assert main([]) == 0
    assert received_paths == [environment_config]
    assert capsys.readouterr().err == ""


def test_main_starts_with_valid_config(tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "test.toml"
    config_path.write_text(
        """
        [kubernetes]
        environment = "local-test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops_agent-kubeconfig"
        request_timeout_seconds = 10

        [model]
        provider = "openai"
        model = "test-model"
        """,
        encoding="utf-8",
    )

    exit_code = main(["--config", str(config_path)])

    captured = capsys.readouterr()
    assert exit_code == 0
    assert "Kubernetes 环境: local-test" in captured.out
    assert "命名空间: sample" in captured.out
    assert captured.err == ""


def test_main_reports_missing_config(tmp_path: Path, capsys) -> None:
    missing_path = tmp_path / "does-not-exist.toml"

    exit_code = main(["--config", str(missing_path)])

    captured = capsys.readouterr()
    assert exit_code == 1
    assert "启动失败" in captured.err
    assert "配置文件不存在" in captured.err
    assert captured.out == ""


def test_main_initializes_config_without_overwriting_existing_file(
    tmp_path: Path,
    capsys,
) -> None:
    config_path = tmp_path / "nested/config.toml"

    assert main(["--config", str(config_path), "init"]) == 0

    initial_content = config_path.read_text(encoding="utf-8")
    assert "[kubernetes]" in initial_content
    assert 'kubeconfig_path = "~/.kube/config"' in initial_content
    assert "[model]" in initial_content
    assert "api_key_env" in initial_content
    assert "sk-" not in initial_content
    assert str(config_path) in capsys.readouterr().out

    assert main(["--config", str(config_path), "init"]) == 1
    assert config_path.read_text(encoding="utf-8") == initial_content
    assert "已存在" in capsys.readouterr().err


def test_main_prints_resolved_config_path(
    tmp_path: Path,
    capsys,
) -> None:
    config_path = tmp_path / "profile.toml"

    assert main(["--config", str(config_path), "config", "path"]) == 0
    assert capsys.readouterr().out == f"{config_path}\n"


def test_main_reports_application_version(capsys) -> None:
    with pytest.raises(SystemExit) as exit_info:
        main(["--version"])

    assert exit_info.value.code == 0
    assert capsys.readouterr().out == "ops-agent 0.1.0\n"


def test_main_doctor_reports_missing_runtime_requirements(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    config_path = tmp_path / "profile.toml"
    config_path.write_text(
        f"""
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "{tmp_path / "missing-kubeconfig"}"
        request_timeout_seconds = 10

        [model]
        provider = "openai"
        model = "test-model"
        api_key_env = "OPS_AGENT_TEST_MISSING_KEY"
        """,
        encoding="utf-8",
    )
    monkeypatch.delenv("OPS_AGENT_TEST_MISSING_KEY", raising=False)

    assert main(["--config", str(config_path), "doctor"]) == 1

    output = capsys.readouterr().out
    assert "PASS  配置文件" in output
    assert "FAIL  kubeconfig" in output
    assert "FAIL  模型密钥" in output
    assert "诊断未通过" in output


def test_main_doctor_verifies_cluster_and_manual_pod_access_dependencies(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    config_path = tmp_path / "profile.toml"
    kubeconfig_path = tmp_path / "kubeconfig"
    kubeconfig_path.write_text("test", encoding="utf-8")
    config_path.write_text(
        f"""
        [kubernetes]
        environment = "test"
        namespace = "sample"
        kubeconfig_path = "{kubeconfig_path}"
        request_timeout_seconds = 10

        [kubernetes.interactive_exec]
        enabled = true

        [model]
        provider = "openai"
        model = "test-model"
        api_key_env = "OPS_AGENT_TEST_API_KEY"
        """,
        encoding="utf-8",
    )
    observed_namespaces: list[str] = []

    class FakeReader:
        def list_pods(self, namespace: str):
            observed_namespaces.append(namespace)
            return []

    monkeypatch.setenv("OPS_AGENT_TEST_API_KEY", "secret")
    monkeypatch.setattr(
        installation_module,
        "create_kubernetes_reader",
        lambda settings: FakeReader(),
        raising=False,
    )
    monkeypatch.setattr(
        installation_module.shutil,
        "which",
        lambda command: "/usr/local/bin/kubectl",
        raising=False,
    )

    assert main(["--config", str(config_path), "doctor"]) == 0

    output = capsys.readouterr().out
    assert "PASS  Kubernetes Pod 读取" in output
    assert "PASS  kubectl" in output
    assert "诊断通过" in output
    assert observed_namespaces == ["sample"]


def test_main_asks_application(
    tmp_path: Path,
    capsys,
    monkeypatch,
) -> None:
    config_path = tmp_path / "test.toml"
    questions: list[str] = []

    class FakeApplication:
        def ask(self, question: str) -> str:
            questions.append(question)
            return "sample-api 正在运行"

    def fake_create_application(received_path: Path) -> FakeApplication:
        assert received_path == config_path
        return FakeApplication()

    monkeypatch.setattr(
        main_module,
        "create_application",
        fake_create_application,
        raising=False,
    )

    exit_code = main(
        [
            "--config",
            str(config_path),
            "ask",
            "检查所有 Pod",
        ]
    )

    captured = capsys.readouterr()
    assert exit_code == 0
    assert questions == ["检查所有 Pod"]
    assert captured.out == "sample-api 正在运行\n"
    assert captured.err == ""


def test_main_starts_tui_with_configured_path(
    tmp_path: Path,
    monkeypatch,
) -> None:
    config_path = tmp_path / "test.toml"
    received_paths: list[Path] = []

    def fake_run_tui(received_path: Path) -> None:
        received_paths.append(received_path)

    monkeypatch.setattr(
        main_module,
        "run_tui",
        fake_run_tui,
        raising=False,
    )

    exit_code = main(
        [
            "--config",
            str(config_path),
            "tui",
        ]
    )

    assert exit_code == 0
    assert received_paths == [config_path]
