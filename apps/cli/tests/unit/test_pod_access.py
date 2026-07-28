import base64
import hashlib
import io
import shutil
import subprocess
from pathlib import Path

import pytest
from ops_agent.settings import (
    DownloadSettings,
    InteractiveExecSettings,
    KubernetesSettings,
)
from ops_agent_cli.pod_access import (
    _DOWNLOAD_HELPER,
    _READ_POD_FILE,
    KubectlPodAccess,
    PodAccessError,
)
from ops_agent_cli.terminal_session import InteractiveTerminalError


class FakeProcess:
    def __init__(
        self,
        payload: bytes,
        *,
        returncode: int = 0,
        error: bytes = b"",
    ) -> None:
        self.stdout = io.BytesIO(payload)
        self.returncode = returncode
        self._error = error

    def wait(self) -> int:
        return self.returncode


def create_settings(
    download_directory: Path,
    *,
    interactive_exec: bool = False,
) -> KubernetesSettings:
    return KubernetesSettings(
        environment="test",
        namespace="sample",
        kubeconfig_path="/tmp/sample.kubeconfig",
        request_timeout_seconds=17,
        proxy_url="http://127.0.0.1:7897",
        interactive_exec=InteractiveExecSettings(enabled=interactive_exec),
        downloads=DownloadSettings(directory=download_directory),
    )


def test_download_pod_file_streams_to_scoped_destination(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    payload = b"hello from pod\n"
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        assert kwargs["env"]["HTTPS_PROXY"] == "http://127.0.0.1:7897/"
        kwargs["stderr"].write(b"")
        return FakeProcess(payload)

    monkeypatch.setattr("ops_agent_cli.pod_access.subprocess.Popen", fake_popen)
    access = KubectlPodAccess(create_settings(tmp_path))

    result = access.download_pod_file(
        pod_name="sample-api-7f8",
        container_name="api",
        remote_path="/var/log/sample/app.log",
    )

    assert result.path == (
        tmp_path
        / "test"
        / "sample"
        / "Pod"
        / "sample-api-7f8"
        / "api"
        / "var"
        / "log"
        / "sample"
        / "app.log"
    )
    assert result.path.read_bytes() == payload
    assert result.size_bytes == len(payload)
    assert result.sha256 == hashlib.sha256(payload).hexdigest()
    assert not list(tmp_path.rglob("*.part"))
    assert commands[0][:7] == [
        "kubectl",
        "--kubeconfig",
        "/tmp/sample.kubeconfig",
        "--namespace",
        "sample",
        "--request-timeout=17s",
        "exec",
    ]
    assert commands[0][7:12] == [
        "sample-api-7f8",
        "-c",
        "api",
        "--",
        "sh",
    ]
    assert commands[0][-3] == _READ_POD_FILE
    assert commands[0][-1] == "/var/log/sample/app.log"


def test_download_pvc_file_uses_mount_scoped_remote_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return FakeProcess(b"backup")

    monkeypatch.setattr("ops_agent_cli.pod_access.subprocess.Popen", fake_popen)
    access = KubectlPodAccess(create_settings(tmp_path))

    result = access.download_pvc_file(
        claim_name="mysql-data",
        pod_name="mysql-0",
        container_name="mysql",
        mount_path="/var/lib/mysql",
        relative_path="backups/daily.sql",
    )

    assert result.path == (
        tmp_path / "test" / "sample" / "PVC" / "mysql-data" / "backups" / "daily.sql"
    )
    assert result.path.read_bytes() == b"backup"
    assert commands[0][-2:] == ["/var/lib/mysql", "backups/daily.sql"]


@pytest.mark.parametrize(
    ("operation", "path"),
    [
        ("pod", "var/log/app.log"),
        ("pod", "/var/log/../secret"),
        ("pod", "/var/log/report\r\x1b[2J.log"),
        ("pvc", "../secret"),
        ("pvc", "/absolute"),
    ],
)
def test_download_rejects_paths_outside_its_allowed_scope(
    tmp_path: Path,
    operation: str,
    path: str,
) -> None:
    access = KubectlPodAccess(create_settings(tmp_path))

    with pytest.raises(PodAccessError):
        if operation == "pod":
            access.download_pod_file(
                pod_name="pod-1",
                container_name="main",
                remote_path=path,
            )
        else:
            access.download_pvc_file(
                claim_name="data",
                pod_name="pod-1",
                container_name="main",
                mount_path="/data",
                relative_path=path,
            )


def test_failed_download_removes_partial_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_popen(command, **kwargs):
        kwargs["stderr"].write(b"permission denied")
        return FakeProcess(b"partial", returncode=1)

    monkeypatch.setattr("ops_agent_cli.pod_access.subprocess.Popen", fake_popen)
    access = KubectlPodAccess(create_settings(tmp_path))

    with pytest.raises(PodAccessError, match="permission denied"):
        access.download_pod_file(
            pod_name="pod-1",
            container_name="main",
            remote_path="/tmp/report.txt",
        )

    assert not list(tmp_path.rglob("*.part"))
    assert not list(tmp_path.rglob("report.txt"))


def test_download_does_not_overwrite_existing_file(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(
        "ops_agent_cli.pod_access.subprocess.Popen",
        lambda command, **kwargs: FakeProcess(b"new"),
    )
    destination = tmp_path / "test/sample/Pod/pod-1/main/tmp/report.txt"
    destination.parent.mkdir(parents=True)
    destination.write_bytes(b"old")
    access = KubectlPodAccess(create_settings(tmp_path))

    result = access.download_pod_file(
        pod_name="pod-1",
        container_name="main",
        remote_path="/tmp/report.txt",
    )

    assert destination.read_bytes() == b"old"
    assert result.path != destination
    assert result.path.name.startswith("report-")
    assert result.path.read_bytes() == b"new"


def test_download_rejects_symlinked_directory_below_configured_root(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    download_root = tmp_path / "downloads"
    download_root.mkdir()
    (download_root / "test").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr(
        "ops_agent_cli.pod_access.subprocess.Popen",
        lambda command, **kwargs: pytest.fail("kubectl must not start"),
    )
    access = KubectlPodAccess(create_settings(download_root))

    with pytest.raises(PodAccessError, match="符号链接"):
        access.download_pod_file(
            pod_name="pod-1",
            container_name="main",
            remote_path="/tmp/report.txt",
        )

    assert not list(outside.iterdir())


def test_download_keeps_directory_fd_when_path_is_replaced_mid_transfer(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    download_root = tmp_path / "downloads"
    outside = tmp_path / "outside"
    outside.mkdir()

    def fake_popen(command, **kwargs):
        original_environment = download_root / "test"
        retained_environment = download_root / "retained-test"
        original_environment.rename(retained_environment)
        original_environment.symlink_to(outside, target_is_directory=True)
        return FakeProcess(b"contained")

    monkeypatch.setattr(
        "ops_agent_cli.pod_access.subprocess.Popen",
        fake_popen,
    )
    access = KubectlPodAccess(create_settings(download_root))

    access.download_pod_file(
        pod_name="pod-1",
        container_name="main",
        remote_path="/tmp/report.txt",
    )

    retained_file = (
        download_root
        / "retained-test"
        / "sample"
        / "Pod"
        / "pod-1"
        / "main"
        / "tmp"
        / "report.txt"
    )
    assert retained_file.read_bytes() == b"contained"
    assert not list(outside.rglob("*"))
    assert not list(download_root.rglob("*.part"))


def test_interactive_session_downloads_discovered_file_without_exiting_shell(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    session_commands: list[list[str]] = []
    download_commands: list[list[str]] = []
    cleanup_commands: list[list[str]] = []
    session_messages: list[str] = []
    monkeypatch.setenv("DEBUG", "release")

    def fake_terminal(
        command,
        *,
        environment,
        download_token,
        download_file,
    ):
        session_commands.append(command)
        assert environment["HTTP_PROXY"] == "http://127.0.0.1:7897/"
        assert "DEBUG" not in environment
        assert len(download_token) == 32
        session_messages.append(download_file("/workspace/report.log"))
        return 0

    def fake_popen(command, **kwargs):
        download_commands.append(command)
        return FakeProcess(b"session artifact")

    monkeypatch.setattr(
        "ops_agent_cli.pod_access.run_interactive_terminal",
        fake_terminal,
        raising=False,
    )

    def fake_run(command, **kwargs):
        cleanup_commands.append(command)
        assert kwargs["stdin"] is subprocess.DEVNULL
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("ops_agent_cli.pod_access.subprocess.run", fake_run)
    monkeypatch.setattr(
        "ops_agent_cli.pod_access.subprocess.Popen",
        fake_popen,
    )

    disabled = KubectlPodAccess(create_settings(tmp_path))
    with pytest.raises(PodAccessError, match="未启用"):
        disabled.interactive_session(
            pod_name="pod-1",
            container_name="main",
        )

    enabled = KubectlPodAccess(create_settings(tmp_path, interactive_exec=True))
    result = enabled.interactive_session(
        pod_name="pod-1",
        container_name="main",
    )

    assert result.exit_code == 0
    downloaded = (
        tmp_path
        / "test"
        / "sample"
        / "Pod"
        / "pod-1"
        / "main"
        / "workspace"
        / "report.log"
    )
    assert downloaded.read_bytes() == b"session artifact"
    assert "下载完成" in session_messages[0]
    assert str(downloaded) in session_messages[0]
    banner = capsys.readouterr().out
    assert "INTERACTIVE POD SESSION · MANUAL WRITE ACCESS" in banner
    assert "Environment: test" in banner
    assert "Namespace: sample" in banner
    assert "Pod: pod-1" in banner
    assert "Container: main" in banner
    assert "AI只读保护已暂停" in banner
    assert "download <文件>" in banner
    assert session_commands[0][6:12] == [
        "exec",
        "-it",
        "pod-1",
        "-c",
        "main",
        "--",
    ]
    assert download_commands[0][-1] == "/workspace/report.log"
    assert cleanup_commands[0][6:10] == [
        "exec",
        "pod-1",
        "-c",
        "main",
    ]


def test_download_helper_resolves_relative_path_without_python(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "workspace"
    child = parent / "nested"
    child.mkdir(parents=True)
    artifact = parent / "daily report.log"
    artifact.write_text("report")
    tool_directory = tmp_path / "tools"
    tool_directory.mkdir()
    base64_path = shutil.which("base64")
    assert base64_path is not None
    (tool_directory / "base64").symlink_to(base64_path)
    environment = {"PATH": str(tool_directory)}
    environment["OPS_AGENT_DOWNLOAD_TOKEN"] = "session-token"

    completed = subprocess.run(
        ["/bin/sh", "-c", _DOWNLOAD_HELPER, "download", "../daily report.log"],
        cwd=child,
        env=environment,
        check=True,
        capture_output=True,
    )

    prefix = b"\x1b]777;ops-agent-download;session-token;"
    assert completed.stdout.startswith(prefix)
    assert completed.stdout.endswith(b"\x07")
    encoded_path = completed.stdout[len(prefix) : -1]
    assert base64.b64decode(encoded_path).decode() == str(artifact)


def test_pod_file_reader_does_not_require_python(tmp_path: Path) -> None:
    artifact = tmp_path / "report.log"
    artifact.write_bytes(b"portable reader")
    tool_directory = tmp_path / "bin"
    tool_directory.mkdir()
    cat_path = shutil.which("cat")
    assert cat_path is not None
    (tool_directory / "cat").symlink_to(cat_path)

    completed = subprocess.run(
        ["/bin/sh", "-c", _READ_POD_FILE, "reader", str(artifact)],
        env={"PATH": str(tool_directory)},
        check=True,
        capture_output=True,
    )

    assert completed.stdout == b"portable reader"


def test_interactive_session_runs_remote_cleanup_after_terminal_error(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    cleanup_commands: list[list[str]] = []

    def fail_terminal(*args, **kwargs):
        raise InteractiveTerminalError("terminal disconnected")

    def fake_run(command, **kwargs):
        cleanup_commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        "ops_agent_cli.pod_access.run_interactive_terminal",
        fail_terminal,
    )
    monkeypatch.setattr("ops_agent_cli.pod_access.subprocess.run", fake_run)
    access = KubectlPodAccess(create_settings(tmp_path, interactive_exec=True))

    with pytest.raises(PodAccessError, match="terminal disconnected"):
        access.interactive_session(
            pod_name="pod-1",
            container_name="main",
        )

    assert len(cleanup_commands) == 1
    assert ".ops-agent-session-" in cleanup_commands[0][-2]
