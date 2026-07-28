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
from pathlib import Path, PurePosixPath
from typing import BinaryIO

from ops_agent.settings import KubernetesSettings

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
import os
import stat
import sys

path = sys.argv[1]
descriptor = None
try:
    descriptor = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
    info = os.fstat(descriptor)
    if not stat.S_ISREG(info.st_mode):
        raise OSError("目标不是普通文件")
    while chunk := os.read(descriptor, 1024 * 1024):
        sys.stdout.buffer.write(chunk)
except OSError as error:
    print(error, file=sys.stderr)
    raise SystemExit(1)
finally:
    if descriptor is not None:
        os.close(descriptor)
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

_INTERACTIVE_SHELL = r"""
if command -v bash >/dev/null 2>&1; then
    exec bash
elif command -v sh >/dev/null 2>&1; then
    exec sh
else
    printf '%s\n' '容器中没有可用的 bash 或 sh' >&2
    exit 127
fi
""".strip()


class PodAccessError(Exception):
    """人工 Pod 访问操作失败。"""


@dataclass(frozen=True)
class DownloadResult:
    path: Path
    size_bytes: int
    sha256: str


@dataclass(frozen=True)
class InteractiveSessionResult:
    exit_code: int


@dataclass(frozen=True)
class _DownloadTarget:
    root: Path
    directory_parts: tuple[str, ...]
    filename: str


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
        command = self._exec_command(
            pod_name=pod_name,
            container_name=container_name,
            remote_command=[
                "sh",
                "-c",
                _PYTHON_RUNNER,
                "ops-agent",
                _READ_POD_FILE,
                remote_path,
            ],
        )
        return self._download(command, destination)

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
        command = [
            *self._kubectl_base(),
            "exec",
            "-it",
            _local_component(pod_name, "Pod"),
            "-c",
            _local_component(container_name, "Container"),
            "--",
            "sh",
            "-c",
            _INTERACTIVE_SHELL,
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
            completed = subprocess.run(
                command,
                check=False,
                env=self._kubectl_environment(),
            )
        except OSError as error:
            raise PodAccessError(f"无法启动 kubectl: {error}") from error
        return InteractiveSessionResult(exit_code=completed.returncode)

    def _download(
        self,
        command: list[str],
        target: _DownloadTarget,
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
        "输入 exit 或按 Ctrl+D 返回 Ops Agent TUI。\n"
    )


def _validate_absolute_pod_path(path: str) -> tuple[str, ...]:
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
    ):
        raise PodAccessError(f"{label} 名称不能用于本机下载路径")
    return value
