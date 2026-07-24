from pathlib import Path

from ops_agent_cli.main import main


def test_main_starts_with_valid_config(tmp_path: Path, capsys) -> None:
    config_path = tmp_path / "test.toml"
    config_path.write_text(
        """
        [kubernetes]
        environment = "local-test"
        namespace = "sample"
        kubeconfig_path = "/tmp/ops_agent-kubeconfig"
        request_timeout_seconds = 10
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
