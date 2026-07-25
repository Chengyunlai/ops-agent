import tomllib
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class KubernetesSettings:
    environment: str
    namespace: str
    kubeconfig_path: Path
    request_timeout_seconds: int


@dataclass(frozen=True)
class ModelSettings:
    provider: str
    name: str


@dataclass(frozen=True)
class Settings:
    kubernetes: KubernetesSettings
    model: ModelSettings


class SettingsError(Exception):
    """应用配置无效。"""


def load_settings(config_path: Path) -> Settings:
    try:
        with config_path.open("rb") as config_file:
            data = tomllib.load(config_file)
    except FileNotFoundError as error:
        raise SettingsError(f"配置文件不存在: {config_path}") from error
    except tomllib.TOMLDecodeError as error:
        raise SettingsError(f"配置文件格式错误: {config_path}") from error

    kubernetes = _require_section(data, "kubernetes")
    kubernetes_fields = (
        "environment",
        "namespace",
        "kubeconfig_path",
        "request_timeout_seconds",
    )
    _require_values(kubernetes, kubernetes_fields, section="kubernetes")

    model = _require_section(data, "model")
    model_fields = ("provider", "model")
    _require_values(model, model_fields, section="model")

    return Settings(
        kubernetes=KubernetesSettings(
            environment=kubernetes["environment"],
            namespace=kubernetes["namespace"],
            kubeconfig_path=Path(kubernetes["kubeconfig_path"]),
            request_timeout_seconds=kubernetes["request_timeout_seconds"],
        ),
        model=ModelSettings(
            provider=model["provider"],
            name=model["model"],
        ),
    )


def _require_section(
    data: dict[str, object],
    section: str,
) -> dict[str, object]:
    value = data.get(section)
    if not isinstance(value, dict):
        raise SettingsError(f"缺少 [{section}] 配置区块")
    return value


def _require_values(
    section_data: dict[str, object],
    field_names: tuple[str, ...],
    *,
    section: str,
) -> None:
    missing_fields = [
        field_name
        for field_name in field_names
        if field_name not in section_data
        or section_data[field_name] is None
        or (
            isinstance(section_data[field_name], str)
            and not section_data[field_name].strip()
        )
    ]
    if missing_fields:
        raise SettingsError(
            f"[{section}] 缺少必填配置项或值为空: "
            f"{', '.join(missing_fields)}"
        )
