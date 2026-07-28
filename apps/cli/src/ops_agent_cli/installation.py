from __future__ import annotations

import os
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from importlib.resources import files
from pathlib import Path

from ops_agent.kubernetes import KubernetesError, create_kubernetes_reader
from ops_agent.settings import SettingsError, load_settings

CONFIG_ENVIRONMENT_VARIABLE = "OPS_AGENT_CONFIG"


class InstallationError(Exception):
    """Installed CLI configuration cannot be prepared safely."""


class DoctorStatus(StrEnum):
    PASS = "PASS"
    WARN = "WARN"
    FAIL = "FAIL"


@dataclass(frozen=True)
class DoctorCheck:
    status: DoctorStatus
    name: str
    detail: str


@dataclass(frozen=True)
class DoctorReport:
    checks: tuple[DoctorCheck, ...]

    @property
    def passed(self) -> bool:
        return all(check.status is not DoctorStatus.FAIL for check in self.checks)


def resolve_config_path(
    explicit_path: Path | None,
    *,
    environment: Mapping[str, str] | None = None,
) -> Path:
    """Resolve the Project Profile path used by an installed CLI."""
    if explicit_path is not None:
        return explicit_path.expanduser()
    values = os.environ if environment is None else environment
    configured_path = values.get(CONFIG_ENVIRONMENT_VARIABLE)
    if configured_path:
        return Path(configured_path).expanduser()
    config_home = values.get("XDG_CONFIG_HOME")
    root = Path(config_home).expanduser() if config_home else Path.home() / ".config"
    return root / "ops-agent" / "config.toml"


def initialize_config(config_path: Path) -> Path:
    """Create a private starter Project Profile without overwriting user data."""
    destination = config_path.expanduser()
    try:
        destination.parent.mkdir(parents=True, exist_ok=True)
        template = (
            files("ops_agent_cli")
            .joinpath("resources/config.toml")
            .read_text(encoding="utf-8")
        )
        descriptor = os.open(
            destination,
            os.O_WRONLY | os.O_CREAT | os.O_EXCL,
            0o600,
        )
        with os.fdopen(descriptor, "w", encoding="utf-8") as config_file:
            config_file.write(template)
    except FileExistsError as error:
        raise InstallationError(f"配置文件已存在，不会覆盖: {destination}") from error
    except OSError as error:
        raise InstallationError(f"无法创建配置文件: {destination}: {error}") from error
    return destination


def diagnose_installation(
    config_path: Path,
    *,
    environment: Mapping[str, str] | None = None,
) -> DoctorReport:
    """Check whether an installed CLI can reach its configured runtime."""
    checks: list[DoctorCheck] = []
    try:
        settings = load_settings(config_path)
    except SettingsError as error:
        return DoctorReport(
            checks=(
                DoctorCheck(
                    status=DoctorStatus.FAIL,
                    name="配置文件",
                    detail=str(error),
                ),
            )
        )

    checks.append(
        DoctorCheck(
            status=DoctorStatus.PASS,
            name="配置文件",
            detail=str(config_path),
        )
    )
    kubeconfig_path = settings.kubernetes.kubeconfig_path.expanduser()
    kubeconfig_available = kubeconfig_path.is_file() and os.access(
        kubeconfig_path,
        os.R_OK,
    )
    checks.append(
        DoctorCheck(
            status=(DoctorStatus.PASS if kubeconfig_available else DoctorStatus.FAIL),
            name="kubeconfig",
            detail=(
                str(kubeconfig_path)
                if kubeconfig_available
                else f"文件不存在或不可读: {kubeconfig_path}"
            ),
        )
    )

    values = os.environ if environment is None else environment
    api_key_environment = settings.model.api_key_env
    if api_key_environment is None:
        checks.append(
            DoctorCheck(
                status=DoctorStatus.WARN,
                name="模型密钥",
                detail="未声明 api_key_env；由模型 Provider 自行解析",
            )
        )
    else:
        api_key_available = bool(values.get(api_key_environment))
        checks.append(
            DoctorCheck(
                status=DoctorStatus.PASS if api_key_available else DoctorStatus.FAIL,
                name="模型密钥",
                detail=(
                    f"已设置 {api_key_environment}"
                    if api_key_available
                    else f"缺少环境变量 {api_key_environment}"
                ),
            )
        )

    kubectl_path = shutil.which("kubectl")
    kubectl_required = settings.kubernetes.interactive_exec.enabled
    checks.append(
        DoctorCheck(
            status=(
                DoctorStatus.PASS
                if kubectl_path is not None
                else (DoctorStatus.FAIL if kubectl_required else DoctorStatus.WARN)
            ),
            name="kubectl",
            detail=(
                kubectl_path
                if kubectl_path is not None
                else (
                    "Interactive Pod Session 已启用，必须安装 kubectl"
                    if kubectl_required
                    else "未安装；仅影响人工 Pod Shell 和 Pod 文件下载"
                )
            ),
        )
    )

    if not kubeconfig_available:
        checks.append(
            DoctorCheck(
                status=DoctorStatus.FAIL,
                name="Kubernetes Pod 读取",
                detail="未执行：kubeconfig 不可用",
            )
        )
        return DoctorReport(checks=tuple(checks))

    try:
        reader = create_kubernetes_reader(settings.kubernetes)
        reader.list_pods(settings.kubernetes.namespace)
    except KubernetesError as error:
        checks.append(
            DoctorCheck(
                status=DoctorStatus.FAIL,
                name="Kubernetes Pod 读取",
                detail=str(error),
            )
        )
    else:
        checks.append(
            DoctorCheck(
                status=DoctorStatus.PASS,
                name="Kubernetes Pod 读取",
                detail=f"namespace={settings.kubernetes.namespace}",
            )
        )
    return DoctorReport(checks=tuple(checks))
