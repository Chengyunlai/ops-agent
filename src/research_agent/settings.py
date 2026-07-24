# 导入 dataclass 和 Path
import tomllib
from dataclasses import dataclass
from pathlib import Path


# 定义 KubernetesSettings
@dataclass(frozen=True)
class KubernetesSettings:
    environment: str
    namespace: str
    kubeconfig_path: Path
    request_timeout_seconds: int


class SettingsError(Exception):
    """应用配置无效。"""


def load_settings(config_path: Path) -> KubernetesSettings:
    try:
        with config_path.open("rb") as config_file:
            data = tomllib.load(config_file)
    except FileNotFoundError as error:
        raise SettingsError(f"配置文件不存在: {config_path}") from error
    except tomllib.TOMLDecodeError as error:
        raise SettingsError(f"配置文件格式错误: {config_path}") from error

    try:
        kubernetes = data["kubernetes"]
    except KeyError as error:
        raise SettingsError("缺少 [kubernetes] 配置区块") from error

    required_fields = (
        "environment",
        "namespace",
        "kubeconfig_path",
        "request_timeout_seconds",
    )
    missing_fields = [
        field_name
        for field_name in required_fields
        if field_name not in kubernetes
    ]
    if missing_fields:
        raise SettingsError(f"缺少必填配置项: {', '.join(missing_fields)}")

    return KubernetesSettings(
        environment=kubernetes["environment"],
        namespace=kubernetes["namespace"],
        kubeconfig_path=Path(kubernetes["kubeconfig_path"]),
        request_timeout_seconds=kubernetes["request_timeout_seconds"],
    )
