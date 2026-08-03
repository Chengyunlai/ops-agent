from __future__ import annotations

import hashlib
import os
import secrets
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from ops_agent_cli.configuration import KubernetesSettings, PodTransferStrategy
from ops_agent_cli.manual_access.terminal import (
    InteractiveTerminalError,
    run_interactive_terminal,
)

_PYTHON_RUNNER = r"""
if command -v python3 >/dev/null 2>&1; then
    interpreter=python3
elif command -v python >/dev/null 2>&1; then
    interpreter=python
else
    printf '%s\n' '容器中缺少 Python，无法安全读取文件' >&2
    exit 127
fi
script=$1
shift
exec "$interpreter" -c "$script" "$@"
""".strip()

_READ_POD_FILE = r"""
path=$1
if [ ! -f "$path" ]; then
    printf '目标不是普通文件: %s\n' "$path" >&2
    exit 1
fi
if [ -L "$path" ]; then
    printf '为避免下载目标在传输前改变，不下载符号链接: %s\n' "$path" >&2
    exit 1
fi
if ! command -v cat >/dev/null 2>&1; then
    printf '%s\n' '容器中缺少 cat，无法读取文件' >&2
    exit 127
fi
exec cat "$path"
""".strip()

_READ_POD_FILE_WITH_DD = r"""
path=$1
if [ ! -f "$path" ]; then
    printf '目标不是普通文件: %s\n' "$path" >&2
    exit 1
fi
if [ -L "$path" ]; then
    printf '为避免下载目标在传输前改变，不下载符号链接: %s\n' "$path" >&2
    exit 1
fi
exec dd if="$path" bs=1048576
""".strip()

_PROBE_POD_TRANSFER = r"""
for tool in cat dd; do
    if command -v "$tool" >/dev/null 2>&1; then
        printf '%s\n' "$tool"
    fi
done
""".strip()

_READ_PVC_FILE = r"""
import os
import stat
import sys

root, relative = sys.argv[1:3]
directory = None
descriptor = None
try:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(root, directory_flags)
    components = relative.split("/")
    for component in components[:-1]:
        info = os.stat(component, dir_fd=directory, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            raise OSError("为避免越过 PVC 挂载根目录，不跟随符号链接")
        child = os.open(component, directory_flags, dir_fd=directory)
        os.close(directory)
        directory = child
    info = os.stat(components[-1], dir_fd=directory, follow_symlinks=False)
    if stat.S_ISLNK(info.st_mode):
        raise OSError("为避免越过 PVC 挂载根目录，不下载符号链接")
    descriptor = os.open(
        components[-1],
        os.O_RDONLY | os.O_NOFOLLOW,
        dir_fd=directory,
    )
    if not stat.S_ISREG(os.fstat(descriptor).st_mode):
        raise OSError("目标不是普通文件")
    while chunk := os.read(descriptor, 1024 * 1024):
        sys.stdout.buffer.write(chunk)
except OSError as error:
    print(error, file=sys.stderr)
    raise SystemExit(1)
finally:
    if descriptor is not None:
        os.close(descriptor)
    if directory is not None:
        os.close(directory)
""".strip()

_DOWNLOAD_HELPER = r"""#!/bin/sh
if [ "$1" = "--" ]; then
    shift
fi
if [ "$#" -ne 1 ]; then
    printf '%s\n' 'usage: download <file>' >&2
    exit 2
fi
case "$1" in
    /*) candidate=$1 ;;
    *) candidate=$PWD/$1 ;;
esac
if command -v realpath >/dev/null 2>&1; then
    remote_path=$(realpath "$candidate") || exit 1
elif command -v readlink >/dev/null 2>&1; then
    remote_path=$(readlink -f "$candidate") || exit 1
else
    directory=${candidate%/*}
    filename=${candidate##*/}
    if [ -z "$directory" ]; then
        directory=/
    fi
    canonical_directory=$(
        CDPATH= cd "$directory" 2>/dev/null && pwd -P
    ) || exit 1
    remote_path=$canonical_directory/$filename
fi
if [ ! -f "$remote_path" ]; then
    printf 'download: not a regular file: %s\n' "$remote_path" >&2
    exit 1
fi
path_length=$(LC_ALL=C; printf '%s' "${#remote_path}")
printf '\033]777;ops-agent-download;%s;%s;' \
    "$OPS_AGENT_DOWNLOAD_TOKEN" "$path_length"
printf '%s' "$remote_path"
""".strip()

_CLEANUP_INTERACTIVE_SESSION = r"""
session_dir=$1
session_id=$2
case "$session_id" in
    ""|*[!0-9a-f]*) exit 2 ;;
esac
expected_dir="/tmp/.ops-agent-session-$session_id"
if [ "$session_dir" != "$expected_dir" ]; then
    exit 2
fi

pid=
if [ -r "$session_dir/pid" ]; then
    IFS= read -r pid <"$session_dir/pid"
fi

matches_session_process() {
    case "$pid" in
        ""|*[!0-9]*) return 1 ;;
    esac
    [ -r "/proc/$pid/cmdline" ] || return 1
    [ -r "/proc/$pid/stat" ] || return 1
    tr '\000' '\n' <"/proc/$pid/cmdline" \
        | grep -F -x "$session_id" >/dev/null 2>&1 \
        || return 1
    IFS= read -r process_stat <"/proc/$pid/stat" || return 1
    stat_fields=${process_stat#*) }
    set -- $stat_fields
    [ "$3" = "$pid" ]
}

if matches_session_process; then
    kill -HUP "-$pid" >/dev/null 2>&1 || true
    sleep 1
    if matches_session_process; then
        kill -KILL "-$pid" >/dev/null 2>&1 || true
    fi
fi
rm -rf "$session_dir"
""".strip()

_STAGE_INTERACTIVE_FILES = r"""
session_dir=$1
session_id=$2
boundary=$3
end_boundary=$4
case "$session_id" in
    ""|*[!0-9a-f]*) exit 2 ;;
esac
expected_dir="/tmp/.ops-agent-session-$session_id"
expected_boundary="OPS_AGENT_FILE_BOUNDARY_$session_id"
expected_end_boundary="OPS_AGENT_FILE_END_$session_id"
if [ "$session_dir" != "$expected_dir" ] \
    || [ "$boundary" != "$expected_boundary" ] \
    || [ "$end_boundary" != "$expected_end_boundary" ]
then
    exit 2
fi

cleanup() {
    rm -rf "$session_dir"
}

trap cleanup 0
trap 'cleanup; exit 129' HUP
trap 'cleanup; exit 130' INT
trap 'cleanup; exit 143' TERM
umask 077
mkdir "$session_dir" || exit 1
bootstrap_file="$session_dir/bootstrap"
download_file="$session_dir/download"
: >"$bootstrap_file" || exit 1
: >"$download_file" || exit 1
target_file=$bootstrap_file
boundary_found=false
end_boundary_found=false
while IFS= read -r payload_line || [ -n "$payload_line" ]; do
    [ "$end_boundary_found" = false ] || exit 2
    if [ "$payload_line" = "$boundary" ]; then
        [ "$boundary_found" = false ] || exit 2
        boundary_found=true
        target_file=$download_file
        continue
    fi
    if [ "$payload_line" = "$end_boundary" ]; then
        [ "$boundary_found" = true ] || exit 2
        end_boundary_found=true
        continue
    fi
    printf '%s\n' "$payload_line" >>"$target_file" || exit 1
done
[ "$boundary_found" = true ] || exit 2
[ "$end_boundary_found" = true ] || exit 2
chmod 700 "$bootstrap_file" "$download_file" || exit 1
trap - 0 HUP INT TERM
""".strip()

_INTERACTIVE_SHELL = r"""
download_token=$1
session_id=$2
requested_locale=$3
terminal_type=$4
color_enabled=$5
case "$session_id" in
    ""|*[!0-9a-f]*) exit 2 ;;
esac
case "$color_enabled" in
    true|false) ;;
    *) exit 2 ;;
esac
session_dir="/tmp/.ops-agent-session-$session_id"

cleanup() {
    rm -rf "$session_dir"
}

trap cleanup EXIT
trap 'cleanup; exit 129' HUP
trap 'cleanup; exit 143' TERM
[ -d "$session_dir" ] || exit 1
rm -f "$session_dir/bootstrap" || exit 1
printf '%s\n' "$$" >"$session_dir/pid" || exit 1
chmod 700 "$session_dir/download" || exit 1
export OPS_AGENT_DOWNLOAD_TOKEN="$download_token"
export PATH="$session_dir:$PATH"
export TERM="$terminal_type"

supports_utf8_locale() {
    candidate_locale=$1
    character_count=$(
        printf '\344\273\277' \
            | LC_ALL="$candidate_locale" LANG="$candidate_locale" \
                wc -m 2>/dev/null
    ) || return 1
    character_count=$(printf '%s' "$character_count" | tr -d '[:space:]')
    [ "$character_count" = 1 ]
}

selected_locale=
if [ "$requested_locale" = auto ]; then
    for candidate_locale in "${LC_ALL:-}" "${LC_CTYPE:-}" "${LANG:-}"; do
        [ -n "$candidate_locale" ] || continue
        if supports_utf8_locale "$candidate_locale"; then
            selected_locale=$candidate_locale
            break
        fi
    done
    if [ -z "$selected_locale" ] && command -v locale >/dev/null 2>&1; then
        locale_list="$session_dir/locales"
        locale -a >"$locale_list" 2>/dev/null || : >"$locale_list"
        while IFS= read -r candidate_locale; do
            case "$candidate_locale" in
                *[Uu][Tt][Ff]*8*) ;;
                *) continue ;;
            esac
            if supports_utf8_locale "$candidate_locale"; then
                selected_locale=$candidate_locale
                break
            fi
        done <"$locale_list"
    fi
    if [ -z "$selected_locale" ]; then
        for candidate_locale in \
            C.UTF-8 C.utf8 \
            en_US.UTF-8 en_US.utf8 \
            zh_CN.UTF-8 zh_CN.utf8
        do
            if supports_utf8_locale "$candidate_locale"; then
                selected_locale=$candidate_locale
                break
            fi
        done
    fi
elif supports_utf8_locale "$requested_locale"; then
    selected_locale=$requested_locale
fi

if [ -n "$selected_locale" ]; then
    export LANG="$selected_locale"
    export LC_ALL="$selected_locale"
else
    printf '%s\n' \
        '[OPS AGENT] 容器未提供可用的 UTF-8 locale；中文文件名可能显示为转义序列。' \
        >&2
fi

bash_rc="$session_dir/bashrc"
sh_rc="$session_dir/shrc"
export OPS_AGENT_SHELL_LOCALE="$selected_locale"
export OPS_AGENT_SHELL_TERM="$terminal_type"
export OPS_AGENT_ORIGINAL_ENV="${ENV:-}"

append_session_environment() {
    rc_file=$1
    printf '%s\n' \
        'if [ -n "$OPS_AGENT_SHELL_LOCALE" ]; then' \
        '    export LANG="$OPS_AGENT_SHELL_LOCALE"' \
        '    export LC_ALL="$OPS_AGENT_SHELL_LOCALE"' \
        'fi' \
        'export TERM="$OPS_AGENT_SHELL_TERM"' \
        'unset OPS_AGENT_SHELL_LOCALE OPS_AGENT_SHELL_TERM OPS_AGENT_ORIGINAL_ENV' \
        >>"$rc_file"
}

printf '%s\n' '[ -f "$HOME/.bashrc" ] && . "$HOME/.bashrc"' >"$bash_rc"
printf '%s\n' \
    'original_env_path=$OPS_AGENT_ORIGINAL_ENV' \
    'case "$original_env_path" in' \
    '    "\$HOME") original_env_path=$HOME ;;' \
    '    "\$HOME/"*) original_env_path=$HOME/${original_env_path#"\$HOME/"} ;;' \
    '    "\${HOME}") original_env_path=$HOME ;;' \
    '    "\${HOME}/"*) original_env_path=$HOME/${original_env_path#"\${HOME}/"} ;;' \
    'esac' \
    '[ -n "$original_env_path" ] && [ -f "$original_env_path" ] && . "$original_env_path"' \
    'unset original_env_path' \
    >"$sh_rc"
append_session_environment "$bash_rc"
append_session_environment "$sh_rc"
if [ "$color_enabled" = true ] \
    && command -v ls >/dev/null 2>&1 \
    && ls --color=auto -d . >/dev/null 2>&1
then
    printf "%s\n" \
        "alias ls >/dev/null 2>&1 || alias ls='ls --color=auto'" \
        >>"$bash_rc"
    printf "%s\n" \
        "alias ls >/dev/null 2>&1 || alias ls='ls --color=auto'" \
        >>"$sh_rc"
fi

printf '%s\n' \
    '[OPS AGENT] 使用 download <文件> 下载当前容器中的文件到本机。'
if command -v bash >/dev/null 2>&1; then
    bash --rcfile "$bash_rc" -i
elif command -v sh >/dev/null 2>&1; then
    export ENV="$sh_rc"
    sh -i
else
    printf '%s\n' '容器中没有可用的 bash 或 sh' >&2
    exit 127
fi
session_status=$?
cleanup
trap - EXIT
exit "$session_status"
""".strip()


class PodAccessError(Exception):
    """人工 Pod 访问操作失败。"""


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    size_bytes: int
    sha256: str
    transfer_backend: PodTransferBackend | None = None


@dataclass(frozen=True)
class InteractiveSessionResult:
    exit_code: int


class PodTransferBackend(StrEnum):
    EXEC_CAT = "exec-cat"
    EXEC_DD = "exec-dd"


@dataclass(frozen=True)
class _DownloadTarget:
    root: Path
    directory_parts: tuple[str, ...]
    filename: str


@dataclass(frozen=True)
class _PodTransferAdapter:
    backend: PodTransferBackend
    required_tool: str
    reader_script: str


_POD_TRANSFER_ADAPTERS = (
    _PodTransferAdapter(
        backend=PodTransferBackend.EXEC_CAT,
        required_tool="cat",
        reader_script=_READ_POD_FILE,
    ),
    _PodTransferAdapter(
        backend=PodTransferBackend.EXEC_DD,
        required_tool="dd",
        reader_script=_READ_POD_FILE_WITH_DD,
    ),
)


class KubectlPodAccess:
    """隐藏 kubectl 文件传输和交互式终端的 CLI 侧深模块。"""

    def __init__(self, settings: KubernetesSettings) -> None:
        self._settings = settings

    def download_pod_file(
        self,
        *,
        pod_name: str,
        container_name: str,
        remote_path: str,
    ) -> DownloadResult:
        remote_parts = _validate_absolute_pod_path(remote_path)
        destination = self._destination(
            "Pod",
            pod_name,
            container_name,
            *remote_parts,
        )
        self._validate_download_target(destination)
        adapter = self._select_pod_transfer_adapter(
            pod_name=pod_name,
            container_name=container_name,
        )
        command = self._exec_command(
            pod_name=pod_name,
            container_name=container_name,
            remote_command=[
                "sh",
                "-c",
                adapter.reader_script,
                "ops-agent",
                remote_path,
            ],
        )
        return self._download(
            command,
            destination,
            transfer_backend=adapter.backend,
            max_bytes=(self._settings.pod_transfer.max_file_size_mb * 1024 * 1024),
        )

    def download_pvc_file(
        self,
        *,
        claim_name: str,
        pod_name: str,
        container_name: str,
        mount_path: str,
        relative_path: str,
    ) -> DownloadResult:
        relative_parts = _validate_pvc_path(relative_path)
        destination = self._destination(
            "PVC",
            claim_name,
            *relative_parts,
        )
        command = self._exec_command(
            pod_name=pod_name,
            container_name=container_name,
            remote_command=[
                "sh",
                "-c",
                _PYTHON_RUNNER,
                "ops-agent",
                _READ_PVC_FILE,
                mount_path,
                "/".join(relative_parts),
            ],
        )
        return self._download(command, destination)

    def interactive_session(
        self,
        *,
        pod_name: str,
        container_name: str,
    ) -> InteractiveSessionResult:
        if not self._settings.interactive_exec.enabled:
            raise PodAccessError(
                "Interactive Pod Session 未启用；请先在 Settings 中启用"
            )
        download_token = secrets.token_hex(16)
        session_id = secrets.token_hex(16)
        session_dir = f"/tmp/.ops-agent-session-{session_id}"
        interactive_exec = self._settings.interactive_exec
        command = [
            *self._kubectl_base(),
            "exec",
            "-it",
            _local_component(pod_name, "Pod"),
            "-c",
            _local_component(container_name, "Container"),
            "--",
            "sh",
            f"{session_dir}/bootstrap",
            download_token,
            session_id,
            interactive_exec.locale,
            interactive_exec.terminal_type,
            "true" if interactive_exec.color else "false",
        ]
        print(
            _interactive_session_banner(
                environment=self._settings.environment,
                namespace=self._settings.namespace,
                pod_name=pod_name,
                container_name=container_name,
            ),
            flush=True,
        )
        try:
            self._stage_interactive_session(
                pod_name=pod_name,
                container_name=container_name,
                session_id=session_id,
            )
            try:
                exit_code = run_interactive_terminal(
                    command,
                    environment=self._kubectl_environment(),
                    download_token=download_token,
                    download_file=lambda remote_path: self._session_download(
                        pod_name=pod_name,
                        container_name=container_name,
                        remote_path=remote_path,
                    ),
                )
            except InteractiveTerminalError as error:
                raise PodAccessError(str(error)) from error
        finally:
            self._cleanup_interactive_session(
                pod_name=pod_name,
                container_name=container_name,
                session_id=session_id,
            )
        return InteractiveSessionResult(exit_code=exit_code)

    def _stage_interactive_session(
        self,
        *,
        pod_name: str,
        container_name: str,
        session_id: str,
    ) -> None:
        session_dir = f"/tmp/.ops-agent-session-{session_id}"
        boundary = f"OPS_AGENT_FILE_BOUNDARY_{session_id}"
        end_boundary = f"OPS_AGENT_FILE_END_{session_id}"
        command = [
            *self._kubectl_base(),
            "exec",
            "-i",
            _local_component(pod_name, "Pod"),
            "-c",
            _local_component(container_name, "Container"),
            "--",
            "sh",
            "-c",
            _STAGE_INTERACTIVE_FILES,
            "ops-agent-stage",
            session_dir,
            session_id,
            boundary,
            end_boundary,
        ]
        payload = (
            f"{_INTERACTIVE_SHELL}\n{boundary}\n{_DOWNLOAD_HELPER}\n{end_boundary}\n"
        ).encode()
        try:
            completed = subprocess.run(
                command,
                input=payload,
                capture_output=True,
                env=self._kubectl_environment(),
                check=False,
                timeout=self._settings.request_timeout_seconds,
            )
        except OSError as error:
            raise PodAccessError(f"无法准备 Pod 交互环境: {error}") from error
        except subprocess.TimeoutExpired as error:
            raise PodAccessError("准备 Pod 交互环境超时") from error
        if completed.returncode:
            message = completed.stderr.decode(
                "utf-8",
                errors="replace",
            ).strip()
            raise PodAccessError(
                "无法准备 Pod 交互环境" + (f": {message}" if message else "")
            )

    def _session_download(
        self,
        *,
        pod_name: str,
        container_name: str,
        remote_path: str,
    ) -> str:
        result = self.download_pod_file(
            pod_name=pod_name,
            container_name=container_name,
            remote_path=remote_path,
        )
        transfer = (
            f"传输 {result.transfer_backend.value} · "
            if result.transfer_backend is not None
            else ""
        )
        return (
            f"下载完成 · {result.size_bytes} bytes · "
            f"SHA-256 {result.sha256} · {transfer}{result.path}"
        )

    def _select_pod_transfer_adapter(
        self,
        *,
        pod_name: str,
        container_name: str,
    ) -> _PodTransferAdapter:
        available_tools = self._probe_pod_transfer_tools(
            pod_name=pod_name,
            container_name=container_name,
        )
        strategy = self._settings.pod_transfer.strategy
        candidates = (
            _POD_TRANSFER_ADAPTERS
            if strategy is PodTransferStrategy.AUTO
            else tuple(
                adapter
                for adapter in _POD_TRANSFER_ADAPTERS
                if adapter.backend.value == strategy.value
            )
        )
        for adapter in candidates:
            if adapter.required_tool in available_tools:
                return adapter
        required = (
            "cat 或 dd"
            if strategy is PodTransferStrategy.AUTO
            else candidates[0].required_tool
        )
        raise PodAccessError(
            f"Pod 文件下载不可用：容器缺少 {required}；当前策略 {strategy.value}"
        )

    def _probe_pod_transfer_tools(
        self,
        *,
        pod_name: str,
        container_name: str,
    ) -> frozenset[str]:
        command = self._exec_command(
            pod_name=pod_name,
            container_name=container_name,
            remote_command=["sh", "-c", _PROBE_POD_TRANSFER],
        )
        try:
            completed = subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                capture_output=True,
                env=self._kubectl_environment(),
                check=False,
                timeout=self._settings.request_timeout_seconds,
            )
        except OSError as error:
            raise PodAccessError(f"无法探测 Pod 文件传输能力: {error}") from error
        except subprocess.TimeoutExpired as error:
            raise PodAccessError("探测 Pod 文件传输能力超时") from error
        if completed.returncode:
            message = completed.stderr.decode(
                "utf-8",
                errors="replace",
            ).strip()
            raise PodAccessError(
                "无法探测 Pod 文件传输能力" + (f": {message}" if message else "")
            )
        return frozenset(completed.stdout.decode().splitlines())

    @staticmethod
    def _validate_download_target(target: _DownloadTarget) -> None:
        with _open_scoped_directory(
            target.root,
            target.directory_parts,
        ):
            return

    def _cleanup_interactive_session(
        self,
        *,
        pod_name: str,
        container_name: str,
        session_id: str,
    ) -> None:
        session_dir = f"/tmp/.ops-agent-session-{session_id}"
        command = self._exec_command(
            pod_name=pod_name,
            container_name=container_name,
            remote_command=[
                "sh",
                "-c",
                _CLEANUP_INTERACTIVE_SESSION,
                "ops-agent-cleanup",
                session_dir,
                session_id,
            ],
        )
        try:
            subprocess.run(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                env=self._kubectl_environment(),
                check=False,
                timeout=self._settings.request_timeout_seconds,
            )
        except (OSError, subprocess.TimeoutExpired):
            return

    def _download(
        self,
        command: list[str],
        target: _DownloadTarget,
        *,
        transfer_backend: PodTransferBackend | None = None,
        max_bytes: int | None = None,
    ) -> DownloadResult:
        try:
            with _open_scoped_directory(
                target.root,
                target.directory_parts,
            ) as (directory_fd, display_directory):
                part_name: str | None = None
                try:
                    destination_name = _available_filename(
                        directory_fd,
                        target.filename,
                    )
                    part_name, part_fd = _create_partial_file(
                        directory_fd,
                        destination_name,
                    )
                    with os.fdopen(part_fd, "wb") as output:
                        size_bytes, digest = _stream_process(
                            command,
                            output,
                            environment=self._kubectl_environment(),
                            max_bytes=max_bytes,
                        )
                    destination_name = _publish_without_overwrite(
                        directory_fd,
                        part_name,
                        destination_name,
                    )
                    part_name = None
                    destination = display_directory / destination_name
                finally:
                    if part_name is not None:
                        try:
                            os.unlink(part_name, dir_fd=directory_fd)
                        except OSError:
                            pass
        except PodAccessError:
            raise
        except OSError as error:
            raise PodAccessError(f"下载文件写入失败: {error}") from error
        return DownloadResult(
            path=destination,
            size_bytes=size_bytes,
            sha256=digest,
            transfer_backend=transfer_backend,
        )

    def _destination(self, *parts: str) -> _DownloadTarget:
        root = self._settings.downloads.directory.expanduser().resolve()
        scoped_parts = (
            _local_component(self._settings.environment, "Environment"),
            _local_component(self._settings.namespace, "Namespace"),
            *(_local_component(part, "下载路径") for part in parts),
        )
        return _DownloadTarget(
            root=root,
            directory_parts=scoped_parts[:-1],
            filename=scoped_parts[-1],
        )

    def _exec_command(
        self,
        *,
        pod_name: str,
        container_name: str,
        remote_command: list[str],
    ) -> list[str]:
        return [
            *self._kubectl_base(),
            "exec",
            _local_component(pod_name, "Pod"),
            "-c",
            _local_component(container_name, "Container"),
            "--",
            *remote_command,
        ]

    def _kubectl_base(self) -> list[str]:
        settings = self._settings
        command = [
            "kubectl",
            "--kubeconfig",
            str(settings.kubeconfig_path.expanduser()),
            "--namespace",
            settings.namespace,
            f"--request-timeout={settings.request_timeout_seconds}s",
        ]
        return command

    def _kubectl_environment(self) -> dict[str, str]:
        environment = os.environ.copy()
        environment.pop("DEBUG", None)
        if self._settings.proxy_url is not None:
            proxy_url = str(self._settings.proxy_url)
            environment["HTTP_PROXY"] = proxy_url
            environment["HTTPS_PROXY"] = proxy_url
        return environment


def _stream_process(
    command: list[str],
    output: BinaryIO,
    *,
    environment: dict[str, str],
    max_bytes: int | None = None,
) -> tuple[int, str]:
    digest = hashlib.sha256()
    size_bytes = 0
    with tempfile.TemporaryFile(mode="w+b") as error_output:
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.PIPE,
                stderr=error_output,
                env=environment,
            )
        except OSError as error:
            raise PodAccessError(f"无法启动 kubectl: {error}") from error
        if process.stdout is None:
            raise PodAccessError("kubectl 未提供下载数据流")
        while chunk := process.stdout.read(1024 * 1024):
            if max_bytes is not None and size_bytes + len(chunk) > max_bytes:
                _stop_download_process(process)
                raise PodAccessError(
                    f"Pod 文件超过配置的下载上限 {max_bytes // (1024 * 1024)} MiB"
                )
            output.write(chunk)
            digest.update(chunk)
            size_bytes += len(chunk)
        exit_code = process.wait()
        if exit_code:
            error_output.seek(0)
            message = error_output.read().decode("utf-8", errors="replace").strip()
            raise PodAccessError(
                f"kubectl 下载失败（exit {exit_code}）"
                + (f": {message}" if message else "")
            )
    return size_bytes, digest.hexdigest()


def _stop_download_process(process: subprocess.Popen[bytes]) -> None:
    try:
        process.terminate()
        process.wait(timeout=2)
    except OSError:
        return
    except subprocess.TimeoutExpired:
        try:
            process.kill()
            process.wait(timeout=2)
        except (OSError, subprocess.TimeoutExpired):
            return


def _publish_without_overwrite(
    directory_fd: int,
    part_name: str,
    destination_name: str,
) -> str:
    candidate = destination_name
    while True:
        try:
            os.link(
                part_name,
                candidate,
                src_dir_fd=directory_fd,
                dst_dir_fd=directory_fd,
                follow_symlinks=False,
            )
        except FileExistsError:
            candidate = _timestamped_filename(destination_name)
            continue
        os.unlink(part_name, dir_fd=directory_fd)
        return candidate


def _available_filename(directory_fd: int, filename: str) -> str:
    try:
        os.stat(filename, dir_fd=directory_fd, follow_symlinks=False)
    except FileNotFoundError:
        return filename
    return _timestamped_filename(filename)


def _timestamped_filename(filename: str) -> str:
    path = Path(filename)
    timestamp = datetime.now(UTC).strftime("%Y%m%d-%H%M%S-%f")
    return f"{path.stem}-{timestamp}{path.suffix}"


@contextmanager
def _open_scoped_directory(
    root: Path,
    directory_parts: tuple[str, ...],
) -> Iterator[tuple[int, Path]]:
    directory_fd: int | None = None
    try:
        directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
        directory_fd = os.open(root.anchor, directory_flags)
        path_parts = (*root.parts[1:], *directory_parts)
        for part in path_parts:
            try:
                os.mkdir(part, dir_fd=directory_fd)
            except FileExistsError:
                pass
            child_fd = os.open(
                part,
                directory_flags,
                dir_fd=directory_fd,
            )
            os.close(directory_fd)
            directory_fd = child_fd
    except OSError as error:
        if directory_fd is not None:
            os.close(directory_fd)
        raise PodAccessError(
            f"无法安全准备下载目录（拒绝符号链接）: {error}"
        ) from error
    try:
        yield directory_fd, root.joinpath(*directory_parts)
    finally:
        os.close(directory_fd)


def _create_partial_file(
    directory_fd: int,
    destination_name: str,
) -> tuple[str, int]:
    flags = os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW
    while True:
        part_name = f".{destination_name}.{secrets.token_hex(8)}.part"
        try:
            descriptor = os.open(
                part_name,
                flags,
                0o600,
                dir_fd=directory_fd,
            )
        except FileExistsError:
            continue
        return part_name, descriptor


def _interactive_session_banner(
    *,
    environment: str,
    namespace: str,
    pod_name: str,
    container_name: str,
) -> str:
    return (
        "\n"
        "=== INTERACTIVE POD SESSION · MANUAL WRITE ACCESS ===\n"
        f"Environment: {environment}\n"
        f"Namespace: {namespace}\n"
        f"Pod: {pod_name}\n"
        f"Container: {container_name}\n"
        "AI只读保护已暂停；以下命令由用户直接执行，不经过 AI。\n"
        "在容器中执行 download <文件> 可下载相对或绝对路径文件。\n"
        "输入 exit 或按 Ctrl+D 返回 Ops Agent TUI。\n"
    )


def _validate_absolute_pod_path(path: str) -> tuple[str, ...]:
    if _contains_control_character(path):
        raise PodAccessError("Pod 下载路径不能包含终端控制字符")
    candidate = PurePosixPath(path)
    if not candidate.is_absolute() or ".." in candidate.parts:
        raise PodAccessError("Pod 下载路径必须是容器内不含 '..' 的绝对路径")
    parts = tuple(part for part in candidate.parts if part not in {"/", "", "."})
    if not parts:
        raise PodAccessError("Pod 下载路径必须指向文件")
    return parts


def _validate_pvc_path(path: str) -> tuple[str, ...]:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise PodAccessError("PVC 下载路径必须位于挂载根目录内")
    parts = tuple(part for part in candidate.parts if part not in {"", "."})
    if not parts:
        raise PodAccessError("PVC 下载路径必须指向文件")
    return parts


def _local_component(value: str, label: str) -> str:
    if (
        not value
        or value in {".", ".."}
        or "/" in value
        or "\\" in value
        or "\0" in value
        or _contains_control_character(value)
    ):
        raise PodAccessError(f"{label} 名称不能用于本机下载路径")
    return value


def _contains_control_character(value: str) -> bool:
    return any(not character.isprintable() for character in value)
