from collections.abc import Callable
from pathlib import PurePosixPath

from kubernetes.client import CoreV1Api
from kubernetes.client.exceptions import ApiException
from urllib3.exceptions import HTTPError

from ops_agent.kubernetes.errors import KubernetesError
from ops_agent.kubernetes.models import (
    PersistentVolumeClaimSummary,
    PersistentVolumeMountSummary,
    VolumeDirectory,
    VolumeEntry,
    VolumeEntryKind,
    VolumeFilePreview,
)

_PYTHON_RUNNER_SCRIPT = r"""
if command -v python3 >/dev/null 2>&1; then
    interpreter=python3
elif command -v python >/dev/null 2>&1; then
    interpreter=python
else
    printf 'E\0容器中缺少 Python，无法保证路径不跟随符号链接\0'
    exit 0
fi
script=$1
shift
exec "$interpreter" -c "$script" "$@"
""".strip()

_LIST_DIRECTORY_SCRIPT = r"""
import os
import stat
import sys


def fail(message):
    sys.stdout.buffer.write(b"E\0" + str(message).encode("utf-8", "replace") + b"\0")
    raise SystemExit


root, relative = sys.argv[1:3]
descriptor = None
try:
    flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    descriptor = os.open(root, flags)
    for component in (() if relative == "." else relative.split("/")):
        info = os.stat(component, dir_fd=descriptor, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            fail("为避免越过挂载根目录，不跟随符号链接")
        child = os.open(component, flags, dir_fd=descriptor)
        os.close(descriptor)
        descriptor = child
    records = []
    with os.scandir(descriptor) as entries:
        for entry in entries:
            info = entry.stat(follow_symlinks=False)
            if stat.S_ISDIR(info.st_mode):
                kind = b"d"
                size = b"-"
            elif stat.S_ISREG(info.st_mode):
                kind = b"f"
                size = str(info.st_size).encode()
            elif stat.S_ISLNK(info.st_mode):
                kind = b"l"
                size = b"-"
            else:
                kind = b"o"
                size = b"-"
            records.append((kind, os.fsencode(entry.name), size))
    output = bytearray(b"O\0")
    for record in records:
        output.extend(b"\0".join(record) + b"\0")
    sys.stdout.buffer.write(output)
except OSError as error:
    fail(error)
finally:
    if descriptor is not None:
        os.close(descriptor)
""".strip()

_PREVIEW_FILE_SCRIPT = r"""
import os
import stat
import sys


def fail(message):
    sys.stdout.buffer.write(b"E\0" + str(message).encode("utf-8", "replace") + b"\0")
    raise SystemExit


root, relative, raw_limit = sys.argv[1:4]
limit = int(raw_limit)
directory = None
file_descriptor = None
try:
    directory_flags = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW
    directory = os.open(root, directory_flags)
    components = relative.split("/")
    for component in components[:-1]:
        info = os.stat(component, dir_fd=directory, follow_symlinks=False)
        if stat.S_ISLNK(info.st_mode):
            fail("为避免越过挂载根目录，不跟随符号链接")
        child = os.open(component, directory_flags, dir_fd=directory)
        os.close(directory)
        directory = child
    final_info = os.stat(
        components[-1],
        dir_fd=directory,
        follow_symlinks=False,
    )
    if stat.S_ISLNK(final_info.st_mode):
        fail("为避免越过挂载根目录，不预览符号链接")
    file_descriptor = os.open(
        components[-1],
        os.O_RDONLY | os.O_NOFOLLOW,
        dir_fd=directory,
    )
    info = os.fstat(file_descriptor)
    if not stat.S_ISREG(info.st_mode):
        fail("当前条目不是普通文件")
    chunks = []
    remaining = limit + 1
    while remaining:
        chunk = os.read(file_descriptor, remaining)
        if not chunk:
            break
        chunks.append(chunk)
        remaining -= len(chunk)
    payload = b"".join(chunks)
    truncated = len(payload) > limit
    payload = payload[:limit]
    try:
        text = payload.decode("utf-8")
    except UnicodeDecodeError:
        fail("文件不是 UTF-8 文本，当前不支持预览")
    if any(ord(character) < 32 and character not in "\n\r\t" for character in text):
        fail("文件包含二进制控制字符，当前不支持预览")
    marker = b"1" if truncated else b"0"
    sys.stdout.buffer.write(b"O\0" + marker + b"\0" + payload)
except OSError as error:
    fail(error)
finally:
    if file_descriptor is not None:
        os.close(file_descriptor)
    if directory is not None:
        os.close(directory)
""".strip()


class KubernetesStorageReader:
    """读取 PVC 拓扑，并通过现有挂载 Pod 安全浏览卷内容。"""

    def __init__(
        self,
        *,
        core_api: CoreV1Api,
        request_timeout_seconds: int,
        pod_executor: Callable[..., str] | None,
    ) -> None:
        self._core_api = core_api
        self._request_timeout_seconds = request_timeout_seconds
        self._pod_executor = pod_executor

    def list_claims(self, namespace: str) -> list[PersistentVolumeClaimSummary]:
        response = self._request(
            f"查询 namespace '{namespace}' 的 PersistentVolumeClaim 失败",
            lambda: self._core_api.list_namespaced_persistent_volume_claim(
                namespace=namespace,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        mounts_error = None
        try:
            mounts = self._list_mounts(namespace)
        except KubernetesError as error:
            mounts = ()
            mounts_error = str(error)
        return [
            self._to_claim_summary(
                claim,
                mounts=tuple(
                    mount for mount in mounts if mount.claim_name == claim.metadata.name
                ),
                mounts_error=mounts_error,
            )
            for claim in response.items
        ]

    def browse(
        self,
        namespace: str,
        claim_name: str,
        *,
        path: str,
    ) -> VolumeDirectory:
        relative_path = _validate_volume_path(path)
        failures = []
        for target in self._browse_targets(namespace, claim_name):
            try:
                output = self._execute_pod_command(
                    target,
                    namespace=namespace,
                    command=[
                        "sh",
                        "-c",
                        _PYTHON_RUNNER_SCRIPT,
                        "ops-agent",
                        _LIST_DIRECTORY_SCRIPT,
                        target.mount_path,
                        relative_path,
                    ],
                )
                entries = _parse_directory_entries(output)
            except KubernetesError as error:
                failures.append(f"{target.pod_name}/{target.container_name}: {error}")
                continue
            return VolumeDirectory(
                claim_name=claim_name,
                path=relative_path,
                target=target,
                entries=entries,
            )
        raise KubernetesError(
            f"PVC '{claim_name}' 的可用挂载目标均读取失败: {'; '.join(failures)}"
        )

    def preview(
        self,
        namespace: str,
        claim_name: str,
        *,
        path: str,
        max_bytes: int,
    ) -> VolumeFilePreview:
        if max_bytes <= 0:
            raise ValueError("max_bytes 必须大于 0")
        relative_path = _validate_volume_path(path)
        failures = []
        for target in self._browse_targets(namespace, claim_name):
            try:
                output = self._execute_pod_command(
                    target,
                    namespace=namespace,
                    command=[
                        "sh",
                        "-c",
                        _PYTHON_RUNNER_SCRIPT,
                        "ops-agent",
                        _PREVIEW_FILE_SCRIPT,
                        target.mount_path,
                        relative_path,
                        str(max_bytes),
                    ],
                )
                truncated, content = _parse_file_preview(output)
            except KubernetesError as error:
                failures.append(f"{target.pod_name}/{target.container_name}: {error}")
                continue
            return VolumeFilePreview(
                claim_name=claim_name,
                path=relative_path,
                target=target,
                content=content,
                truncated=truncated,
            )
        raise KubernetesError(
            f"PVC '{claim_name}' 的可用挂载目标均预览失败: {'; '.join(failures)}"
        )

    def _to_claim_summary(
        self,
        claim,
        *,
        mounts: tuple[PersistentVolumeMountSummary, ...],
        mounts_error: str | None,
    ) -> PersistentVolumeClaimSummary:
        volume_name = claim.spec.volume_name
        backend = None
        backend_error = None
        reclaim_policy = None
        if volume_name:
            try:
                volume = self._core_api.read_persistent_volume(
                    name=volume_name,
                    _request_timeout=self._request_timeout_seconds,
                )
            except (ApiException, HTTPError) as error:
                backend = "Unavailable"
                backend_error = str(error)
            else:
                backend = _persistent_volume_backend(volume)
                reclaim_policy = getattr(
                    volume.spec,
                    "persistent_volume_reclaim_policy",
                    None,
                )
        capacity = claim.status.capacity or {}
        return PersistentVolumeClaimSummary(
            name=claim.metadata.name,
            phase=claim.status.phase,
            volume_name=volume_name,
            capacity=capacity.get("storage"),
            access_modes=tuple(
                claim.status.access_modes or claim.spec.access_modes or []
            ),
            storage_class=claim.spec.storage_class_name,
            backend=backend,
            backend_error=backend_error,
            reclaim_policy=reclaim_policy,
            mounts=mounts,
            mounts_error=mounts_error,
        )

    def _list_mounts(
        self,
        namespace: str,
    ) -> tuple[PersistentVolumeMountSummary, ...]:
        response = self._request(
            f"查询 namespace '{namespace}' 的 PVC 挂载关系失败",
            lambda: self._core_api.list_namespaced_pod(
                namespace=namespace,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        return tuple(mount for pod in response.items for mount in _pod_pvc_mounts(pod))

    def _browse_targets(
        self,
        namespace: str,
        claim_name: str,
    ) -> tuple[PersistentVolumeMountSummary, ...]:
        mounts = sorted(
            (
                mount
                for mount in self._list_mounts(namespace)
                if (
                    mount.claim_name == claim_name
                    and mount.pod_phase == "Running"
                    and mount.container_running
                )
            ),
            key=lambda item: (
                item.pod_name,
                item.container_name,
                item.mount_path,
            ),
        )
        if not mounts:
            raise KubernetesError(
                f"PVC '{claim_name}' 当前没有挂载到 Running 容器，无法浏览目录"
            )
        return tuple(mounts)

    def _execute_pod_command(
        self,
        target: PersistentVolumeMountSummary,
        *,
        namespace: str,
        command: list[str],
    ) -> str:
        if self._pod_executor is None:
            raise KubernetesError("当前 Kubernetes 客户端未启用 PVC 目录浏览")
        try:
            output = self._pod_executor(
                name=target.pod_name,
                namespace=namespace,
                container=target.container_name,
                command=command,
                stderr=True,
                stdin=False,
                stdout=True,
                tty=False,
                _request_timeout=self._request_timeout_seconds,
            )
        except Exception as error:
            raise KubernetesError(
                f"通过 Pod '{target.pod_name}' 读取 PVC 失败: {error}"
            ) from error
        if isinstance(output, bytes | bytearray):
            return bytes(output).decode("utf-8", errors="replace")
        return str(output)

    def _request(self, failure_message: str, request: Callable[[], object]):
        try:
            return request()
        except (ApiException, HTTPError) as error:
            raise KubernetesError(f"{failure_message}: {error}") from error


def _pod_pvc_mounts(pod) -> tuple[PersistentVolumeMountSummary, ...]:
    spec = pod.spec
    volume_claims = {
        volume.name: volume.persistent_volume_claim.claim_name
        for volume in (getattr(spec, "volumes", None) or [])
        if getattr(volume, "persistent_volume_claim", None) is not None
    }
    container_running = {
        status.name: (
            getattr(getattr(status, "state", None), "running", None) is not None
        )
        for status in (getattr(pod.status, "container_statuses", None) or [])
    }
    mounts = []
    for container in getattr(spec, "containers", None) or []:
        for mount in getattr(container, "volume_mounts", None) or []:
            claim_name = volume_claims.get(mount.name)
            if claim_name is None:
                continue
            mounts.append(
                PersistentVolumeMountSummary(
                    claim_name=claim_name,
                    pod_name=pod.metadata.name,
                    pod_phase=pod.status.phase,
                    container_name=container.name,
                    mount_path=mount.mount_path,
                    read_only=bool(mount.read_only),
                    container_running=container_running.get(container.name, False),
                )
            )
    return tuple(mounts)


def _persistent_volume_backend(volume) -> str:
    spec = volume.spec
    csi = getattr(spec, "csi", None)
    if csi is not None:
        return f"CSI/{csi.driver}"
    nfs = getattr(spec, "nfs", None)
    if nfs is not None:
        return f"NFS/{nfs.server}:{nfs.path}"
    local = getattr(spec, "local", None)
    if local is not None:
        return f"Local/{local.path}"
    host_path = getattr(spec, "host_path", None)
    if host_path is not None:
        return f"HostPath/{host_path.path}"
    return "Unknown"


def _validate_volume_path(path: str) -> str:
    candidate = PurePosixPath(path)
    if candidate.is_absolute() or ".." in candidate.parts:
        raise KubernetesError("PVC 路径必须位于挂载根目录内")
    parts = tuple(part for part in candidate.parts if part not in {"", "."})
    return str(PurePosixPath(*parts)) if parts else "."


def _parse_directory_entries(output: str) -> tuple[VolumeEntry, ...]:
    tokens = output.split("\0")
    if not tokens or tokens[0] not in {"O", "E"}:
        raise KubernetesError(
            "容器未返回可识别的目录数据；请确认容器包含 POSIX sh 和 Python 3"
        )
    if tokens[0] == "E":
        raise KubernetesError(tokens[1] if len(tokens) > 1 else "PVC 目录读取失败")
    payload = [token for token in tokens[1:] if token != ""]
    if len(payload) % 3:
        raise KubernetesError("容器返回的目录数据不完整")
    kind_by_code = {
        "d": VolumeEntryKind.DIRECTORY,
        "f": VolumeEntryKind.FILE,
        "l": VolumeEntryKind.SYMLINK,
        "o": VolumeEntryKind.OTHER,
    }
    entries = []
    for index in range(0, len(payload), 3):
        kind_code, name, raw_size = payload[index : index + 3]
        kind = kind_by_code.get(kind_code, VolumeEntryKind.OTHER)
        try:
            size = int(raw_size.strip()) if raw_size != "-" else None
        except ValueError:
            size = None
        entries.append(VolumeEntry(name=name, kind=kind, size_bytes=size))
    return tuple(
        sorted(
            entries,
            key=lambda item: (
                item.kind is not VolumeEntryKind.DIRECTORY,
                item.name.casefold(),
            ),
        )
    )


def _parse_file_preview(output: str) -> tuple[bool, str]:
    parts = output.split("\0", 2)
    if not parts or parts[0] not in {"O", "E"}:
        raise KubernetesError(
            "容器未返回可识别的文件数据；请确认容器包含 POSIX sh 和 Python 3"
        )
    if parts[0] == "E":
        raise KubernetesError(parts[1] if len(parts) > 1 else "PVC 文件读取失败")
    if len(parts) < 3:
        raise KubernetesError("容器返回的文件预览数据不完整")
    if "\0" in parts[2]:
        raise KubernetesError("文件包含二进制数据，当前只支持文本预览")
    return parts[1] == "1", parts[2]
