import hashlib
import io
import shutil
import subprocess
from pathlib import Path
from urllib.parse import urlencode

import pytest
from ops_agent_cli.configuration import (
    DownloadSettings,
    InteractiveExecSettings,
    KubernetesSettings,
    PodTransferSettings,
    PodTransferStrategy,
)
from ops_agent_cli.manual_access.kubectl import (
    _DOWNLOAD_HELPER,
    _INTERACTIVE_SHELL,
    _READ_POD_FILE,
    _STAGE_INTERACTIVE_FILES,
    KubectlPodAccess,
    PodAccessError,
    PodTransferBackend,
)
from ops_agent_cli.manual_access.terminal import InteractiveTerminalError


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

    def wait(self, timeout: float | None = None) -> int:
        return self.returncode

    def terminate(self) -> None:
        self.returncode = -15

    def kill(self) -> None:
        self.returncode = -9


def create_settings(
    download_directory: Path,
    *,
    interactive_exec: bool = False,
    transfer_strategy: PodTransferStrategy = PodTransferStrategy.AUTO,
    max_file_size_mb: int = 512,
) -> KubernetesSettings:
    return KubernetesSettings(
        environment="test",
        namespace="sample",
        kubeconfig_path="/tmp/sample.kubeconfig",
        request_timeout_seconds=17,
        proxy_url="http://127.0.0.1:7897",
        interactive_exec=InteractiveExecSettings(enabled=interactive_exec),
        downloads=DownloadSettings(directory=download_directory),
        pod_transfer=PodTransferSettings(
            strategy=transfer_strategy,
            max_file_size_mb=max_file_size_mb,
        ),
    )


def stub_pod_transfer_tools(
    monkeypatch: pytest.MonkeyPatch,
    *tools: str,
) -> None:
    output = "".join(f"{tool}\n" for tool in tools).encode()
    monkeypatch.setattr(
        "ops_agent_cli.manual_access.kubectl.subprocess.run",
        lambda command, **kwargs: subprocess.CompletedProcess(
            command,
            0,
            stdout=output,
            stderr=b"",
        ),
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

    monkeypatch.setattr(
        "ops_agent_cli.manual_access.kubectl.subprocess.Popen", fake_popen
    )
    stub_pod_transfer_tools(monkeypatch, "cat", "dd")
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
    assert result.transfer_backend is PodTransferBackend.EXEC_CAT
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


def test_download_pod_file_auto_selects_dd_when_cat_is_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=b"dd\n", stderr=b"")

    def fake_popen(command, **kwargs):
        return FakeProcess(b"read with dd")

    monkeypatch.setattr("ops_agent_cli.manual_access.kubectl.subprocess.run", fake_run)
    monkeypatch.setattr(
        "ops_agent_cli.manual_access.kubectl.subprocess.Popen", fake_popen
    )
    access = KubectlPodAccess(create_settings(tmp_path))

    result = access.download_pod_file(
        pod_name="minimal-7f8",
        container_name="app",
        remote_path="/workspace/report.log",
    )

    assert result.transfer_backend is PodTransferBackend.EXEC_DD
    assert result.path.read_bytes() == b"read with dd"


def test_download_pod_file_stops_at_configured_size_limit(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def fake_run(command, **kwargs):
        return subprocess.CompletedProcess(command, 0, stdout=b"cat\n", stderr=b"")

    monkeypatch.setattr("ops_agent_cli.manual_access.kubectl.subprocess.run", fake_run)
    monkeypatch.setattr(
        "ops_agent_cli.manual_access.kubectl.subprocess.Popen",
        lambda command, **kwargs: FakeProcess(b"x" * (1024 * 1024 + 1)),
    )
    access = KubectlPodAccess(
        create_settings(
            tmp_path,
            max_file_size_mb=1,
        )
    )

    with pytest.raises(PodAccessError, match="1 MiB"):
        access.download_pod_file(
            pod_name="large-file-7f8",
            container_name="app",
            remote_path="/workspace/report.log",
        )

    assert not list(tmp_path.rglob("*.part"))
    assert not list(tmp_path.rglob("report.log"))


def test_download_pod_file_reports_missing_tool_for_explicit_strategy(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    stub_pod_transfer_tools(monkeypatch, "dd")
    access = KubectlPodAccess(
        create_settings(
            tmp_path,
            transfer_strategy=PodTransferStrategy.EXEC_CAT,
        )
    )

    with pytest.raises(
        PodAccessError,
        match=r"缺少 cat.*当前策略 exec-cat",
    ):
        access.download_pod_file(
            pod_name="minimal-7f8",
            container_name="app",
            remote_path="/workspace/report.log",
        )


def test_download_pvc_file_uses_mount_scoped_remote_reader(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    commands: list[list[str]] = []

    def fake_popen(command, **kwargs):
        commands.append(command)
        return FakeProcess(b"backup")

    monkeypatch.setattr(
        "ops_agent_cli.manual_access.kubectl.subprocess.Popen", fake_popen
    )
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

    monkeypatch.setattr(
        "ops_agent_cli.manual_access.kubectl.subprocess.Popen", fake_popen
    )
    stub_pod_transfer_tools(monkeypatch, "cat")
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
        "ops_agent_cli.manual_access.kubectl.subprocess.Popen",
        lambda command, **kwargs: FakeProcess(b"new"),
    )
    stub_pod_transfer_tools(monkeypatch, "cat")
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
        "ops_agent_cli.manual_access.kubectl.subprocess.Popen",
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
        "ops_agent_cli.manual_access.kubectl.subprocess.Popen",
        fake_popen,
    )
    stub_pod_transfer_tools(monkeypatch, "cat")
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
    stage_commands: list[list[str]] = []
    stage_payloads: list[bytes] = []
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
        "ops_agent_cli.manual_access.kubectl.run_interactive_terminal",
        fake_terminal,
        raising=False,
    )

    def fake_run(command, **kwargs):
        if kwargs.get("input") is not None:
            stage_commands.append(command)
            stage_payloads.append(kwargs["input"])
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=b"",
                stderr=b"",
            )
        if kwargs.get("capture_output"):
            return subprocess.CompletedProcess(
                command,
                0,
                stdout=b"cat\ndd\n",
                stderr=b"",
            )
        cleanup_commands.append(command)
        assert kwargs["stdin"] is subprocess.DEVNULL
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("ops_agent_cli.manual_access.kubectl.subprocess.run", fake_run)
    monkeypatch.setattr(
        "ops_agent_cli.manual_access.kubectl.subprocess.Popen",
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
    assert "传输 exec-cat" in session_messages[0]
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
    assert _INTERACTIVE_SHELL not in session_commands[0]
    assert _DOWNLOAD_HELPER not in session_commands[0]
    assert _STAGE_INTERACTIVE_FILES in stage_commands[0]
    assert _INTERACTIVE_SHELL.encode() in stage_payloads[0]
    assert _DOWNLOAD_HELPER.encode() in stage_payloads[0]
    stage_remote_command = stage_commands[0][stage_commands[0].index("--") + 1 :]
    stage_query = urlencode(
        [("command", value) for value in stage_remote_command],
    ).encode()
    assert len(stage_query) < 4096
    assert download_commands[0][-1] == "/workspace/report.log"
    assert cleanup_commands[0][6:10] == [
        "exec",
        "pod-1",
        "-c",
        "main",
    ]


def _run_fake_interactive_session(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    interactive_exec: InteractiveExecSettings,
    shell_name: str,
    available_locales: tuple[str, ...],
    utf8_locales: tuple[str, ...],
    color_supported: bool,
    sh_env_value: str | None = None,
) -> tuple[str, str]:
    real_run = subprocess.run
    tool_directory = tmp_path / "bin"
    tool_directory.mkdir()
    capture_path = tmp_path / "shell-environment.txt"
    home_directory = tmp_path / "home"
    home_directory.mkdir()
    existing_rc = tmp_path / "existing-rc"
    existing_rc.write_text(
        "export EXISTING_RC=loaded\nexport LANG=C\nexport LC_ALL=C\nexport TERM=dumb\n",
        encoding="utf-8",
    )
    if shell_name == "bash":
        (home_directory / ".bashrc").write_text(
            existing_rc.read_text(encoding="utf-8"),
            encoding="utf-8",
        )

    fake_shell = tool_directory / shell_name
    fake_shell.write_text(
        """#!/bin/sh
rcfile=${ENV:-}
while [ "$#" -gt 0 ]; do
    if [ "$1" = "--rcfile" ]; then
        shift
        rcfile=$1
    fi
    shift
done
if [ -n "$rcfile" ]; then
    . "$rcfile"
fi
{
    printf 'LANG=%s\\n' "$LANG"
    printf 'LC_ALL=%s\\n' "$LC_ALL"
    printf 'TERM=%s\\n' "$TERM"
    printf 'EXISTING_RC=%s\\n' "$EXISTING_RC"
    if alias ls >/dev/null 2>&1; then
        alias ls
    else
        printf '%s\\n' 'NO_LS_ALIAS'
    fi
} >"$CAPTURE_FILE"
exit 0
""",
        encoding="utf-8",
    )
    fake_shell.chmod(0o755)

    for tool in ("chmod", "mkdir", "rm", "tr"):
        tool_path = shutil.which(tool)
        assert tool_path is not None
        (tool_directory / tool).symlink_to(tool_path)

    fake_locale = tool_directory / "locale"
    fake_locale.write_text(
        """#!/bin/sh
[ "$1" = "-a" ] || exit 1
old_ifs=$IFS
IFS=:
for candidate in $AVAILABLE_LOCALES; do
    printf '%s\\n' "$candidate"
done
IFS=$old_ifs
""",
        encoding="utf-8",
    )
    fake_locale.chmod(0o755)
    fake_wc = tool_directory / "wc"
    fake_wc.write_text(
        """#!/bin/sh
case ":$UTF8_LOCALES:" in
    *":$LC_ALL:"*) printf '%s\\n' 1 ;;
    *) printf '%s\\n' 3 ;;
esac
""",
        encoding="utf-8",
    )
    fake_wc.chmod(0o755)
    fake_ls = tool_directory / "ls"
    fake_ls.write_text(
        '#!/bin/sh\n[ "$LS_COLOR_SUPPORTED" = true ]\n',
        encoding="utf-8",
    )
    fake_ls.chmod(0o755)
    terminal_output: list[str] = []

    def fake_terminal(
        command,
        *,
        environment,
        download_token,
        download_file,
    ):
        assert command[-3:] == [
            interactive_exec.locale,
            interactive_exec.terminal_type,
            "true" if interactive_exec.color else "false",
        ]
        remote_command = command[command.index("--") + 1 :]
        remote_command[0] = "/bin/sh"
        remote_environment = {
            "PATH": str(tool_directory),
            "HOME": str(home_directory),
            "CAPTURE_FILE": str(capture_path),
            "LANG": "C",
            "LC_ALL": "C",
            "TERM": "dumb",
            "AVAILABLE_LOCALES": ":".join(available_locales),
            "UTF8_LOCALES": ":".join(utf8_locales),
            "LS_COLOR_SUPPORTED": "true" if color_supported else "false",
        }
        if shell_name == "sh":
            remote_environment["ENV"] = sh_env_value or str(existing_rc)
        completed = real_run(
            remote_command,
            env=remote_environment,
            check=False,
            capture_output=True,
            text=True,
        )
        assert completed.returncode == 0, completed.stderr
        terminal_output.append(completed.stdout + completed.stderr)
        return completed.returncode

    monkeypatch.setattr(
        "ops_agent_cli.manual_access.kubectl.run_interactive_terminal",
        fake_terminal,
    )

    def fake_run(command, **kwargs):
        payload = kwargs.get("input")
        if payload is not None:
            remote_command = command[command.index("--") + 1 :]
            remote_command[0] = "/bin/sh"
            completed = real_run(
                remote_command,
                input=payload,
                env={"PATH": str(tool_directory)},
                check=False,
                capture_output=True,
            )
            assert completed.returncode == 0, completed.stderr.decode()
            return subprocess.CompletedProcess(command, 0)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr("ops_agent_cli.manual_access.kubectl.subprocess.run", fake_run)
    settings = create_settings(
        tmp_path,
        interactive_exec=True,
    ).model_copy(update={"interactive_exec": interactive_exec})
    access = KubectlPodAccess(settings)

    result = access.interactive_session(
        pod_name="pod-1",
        container_name="main",
    )

    assert result.exit_code == 0
    return capture_path.read_text(encoding="utf-8"), "".join(terminal_output)


def test_interactive_staging_rejects_truncated_download_payload(
    tmp_path: Path,
) -> None:
    session_id = hashlib.sha256(str(tmp_path).encode()).hexdigest()[:32]
    session_dir = Path(f"/tmp/.ops-agent-session-{session_id}")
    boundary = f"OPS_AGENT_FILE_BOUNDARY_{session_id}"
    end_boundary = f"OPS_AGENT_FILE_END_{session_id}"
    payload = f"{_INTERACTIVE_SHELL}\n{boundary}\ntruncated helper\n".encode()

    completed = subprocess.run(
        [
            "/bin/sh",
            "-c",
            _STAGE_INTERACTIVE_FILES,
            "ops-agent-stage",
            str(session_dir),
            session_id,
            boundary,
            end_boundary,
        ],
        input=payload,
        capture_output=True,
        check=False,
    )

    assert completed.returncode == 2
    assert not session_dir.exists()


def test_interactive_session_initializes_utf8_locale_term_and_colors(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured, _ = _run_fake_interactive_session(
        tmp_path,
        monkeypatch,
        interactive_exec=InteractiveExecSettings(enabled=True),
        shell_name="bash",
        available_locales=(),
        utf8_locales=("C.UTF-8",),
        color_supported=True,
    )

    assert "LANG=C.UTF-8" in captured
    assert "LC_ALL=C.UTF-8" in captured
    assert "TERM=xterm-256color" in captured
    assert "EXISTING_RC=loaded" in captured
    assert "ls --color=auto" in captured


def test_interactive_session_auto_detects_container_specific_utf8_locale(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured, _ = _run_fake_interactive_session(
        tmp_path,
        monkeypatch,
        interactive_exec=InteractiveExecSettings(enabled=True),
        shell_name="bash",
        available_locales=("C", "en_GB.UTF-8"),
        utf8_locales=("en_GB.UTF-8",),
        color_supported=True,
    )

    assert "LANG=en_GB.UTF-8" in captured
    assert "LC_ALL=en_GB.UTF-8" in captured


def test_interactive_session_sh_preserves_env_and_respects_overrides(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured, _ = _run_fake_interactive_session(
        tmp_path,
        monkeypatch,
        interactive_exec=InteractiveExecSettings(
            enabled=True,
            locale="zh_CN.UTF-8",
            terminal_type="screen-256color",
            color=False,
        ),
        shell_name="sh",
        available_locales=("zh_CN.UTF-8",),
        utf8_locales=("zh_CN.UTF-8",),
        color_supported=True,
    )

    assert "LANG=zh_CN.UTF-8" in captured
    assert "LC_ALL=zh_CN.UTF-8" in captured
    assert "TERM=screen-256color" in captured
    assert "EXISTING_RC=loaded" in captured
    assert "NO_LS_ALIAS" in captured


@pytest.mark.parametrize(
    "sh_env_value",
    ["$HOME/../existing-rc", "${HOME}/../existing-rc"],
)
def test_interactive_session_sh_expands_home_in_original_env(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    sh_env_value: str,
) -> None:
    captured, _ = _run_fake_interactive_session(
        tmp_path,
        monkeypatch,
        interactive_exec=InteractiveExecSettings(enabled=True),
        shell_name="sh",
        available_locales=(),
        utf8_locales=("C.UTF-8",),
        color_supported=True,
        sh_env_value=sh_env_value,
    )

    assert "EXISTING_RC=loaded" in captured
    assert "LANG=C.UTF-8" in captured
    assert "LC_ALL=C.UTF-8" in captured
    assert "TERM=xterm-256color" in captured


def test_interactive_session_warns_when_utf8_and_color_are_unavailable(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured, terminal_output = _run_fake_interactive_session(
        tmp_path,
        monkeypatch,
        interactive_exec=InteractiveExecSettings(enabled=True),
        shell_name="bash",
        available_locales=("C", "POSIX"),
        utf8_locales=(),
        color_supported=False,
    )

    assert "LANG=C" in captured
    assert "LC_ALL=C" in captured
    assert "TERM=xterm-256color" in captured
    assert "NO_LS_ALIAS" in captured
    assert "容器未提供可用的 UTF-8 locale" in terminal_output


def test_download_helper_resolves_relative_path_without_python(
    tmp_path: Path,
) -> None:
    parent = tmp_path / "workspace"
    child = parent / "nested"
    child.mkdir(parents=True)
    artifact = parent / "每日 report.log"
    artifact.write_text("report")
    environment = {"PATH": ""}
    environment["OPS_AGENT_DOWNLOAD_TOKEN"] = "session-token"

    completed = subprocess.run(
        ["/bin/sh", "-c", _DOWNLOAD_HELPER, "download", "../每日 report.log"],
        cwd=child,
        env=environment,
        check=True,
        capture_output=True,
    )

    prefix = b"\x1b]777;ops-agent-download;session-token;"
    assert completed.stdout.startswith(prefix)
    length_payload, remote_path = completed.stdout[len(prefix) :].split(b";", 1)
    assert int(length_payload) == len(remote_path)
    assert remote_path.decode() == str(artifact)


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
    stage_commands: list[list[str]] = []
    cleanup_commands: list[list[str]] = []

    def fail_terminal(*args, **kwargs):
        raise InteractiveTerminalError("terminal disconnected")

    def fake_run(command, **kwargs):
        if kwargs.get("input") is not None:
            stage_commands.append(command)
            return subprocess.CompletedProcess(command, 0)
        cleanup_commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        "ops_agent_cli.manual_access.kubectl.run_interactive_terminal",
        fail_terminal,
    )
    monkeypatch.setattr("ops_agent_cli.manual_access.kubectl.subprocess.run", fake_run)
    access = KubectlPodAccess(create_settings(tmp_path, interactive_exec=True))

    with pytest.raises(PodAccessError, match="terminal disconnected"):
        access.interactive_session(
            pod_name="pod-1",
            container_name="main",
        )

    assert len(stage_commands) == 1
    assert len(cleanup_commands) == 1
    assert ".ops-agent-session-" in cleanup_commands[0][-2]


def test_interactive_session_reports_stage_failure_and_cleans_up(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    terminal_started = False
    cleanup_commands: list[list[str]] = []

    def fail_if_terminal_starts(*args, **kwargs):
        nonlocal terminal_started
        terminal_started = True
        return 0

    def fake_run(command, **kwargs):
        if kwargs.get("input") is not None:
            return subprocess.CompletedProcess(
                command,
                1,
                stdout=b"",
                stderr=b"stage rejected",
            )
        cleanup_commands.append(command)
        return subprocess.CompletedProcess(command, 0)

    monkeypatch.setattr(
        "ops_agent_cli.manual_access.kubectl.run_interactive_terminal",
        fail_if_terminal_starts,
    )
    monkeypatch.setattr("ops_agent_cli.manual_access.kubectl.subprocess.run", fake_run)
    access = KubectlPodAccess(create_settings(tmp_path, interactive_exec=True))

    with pytest.raises(
        PodAccessError,
        match="无法准备 Pod 交互环境: stage rejected",
    ):
        access.interactive_session(
            pod_name="pod-1",
            container_name="main",
        )

    assert not terminal_started
    assert len(cleanup_commands) == 1
    assert ".ops-agent-session-" in cleanup_commands[0][-2]
