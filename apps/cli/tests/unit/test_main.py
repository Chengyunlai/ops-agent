from pathlib import Path
from types import SimpleNamespace

from ops_agent_cli import main as main_module
from ops_agent_cli.main import main


def test_main_uses_local_test_config_by_default(
    monkeypatch,
    capsys,
) -> None:
    settings = SimpleNamespace(
        kubernetes=SimpleNamespace(
            environment="test",
            namespace="sample",
        )
    )

    def fake_load_settings(config_path: Path):
        assert config_path == Path("config/local/test.toml")
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
