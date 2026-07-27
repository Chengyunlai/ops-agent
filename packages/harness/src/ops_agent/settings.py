import tomllib
from dataclasses import dataclass
from pathlib import Path
from typing import Self


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
    base_url: str | None
    api_key_env: str | None


@dataclass(frozen=True)
class Settings:
    kubernetes: KubernetesSettings
    model: ModelSettings


class SettingsError(Exception):
    """应用配置无效。"""


def load_settings(config_path: Path) -> Settings:
    data = _read_toml(config_path)

    return Settings(
        kubernetes=_parse_kubernetes_settings(data),
        model=_parse_model_settings(data),
    )


def _read_toml(config_path: Path) -> dict[str, object]:
    try:
        with config_path.open("rb") as config_file:
            return tomllib.load(config_file)
    except FileNotFoundError as error:
        raise SettingsError(f"配置文件不存在: {config_path}") from error
    except tomllib.TOMLDecodeError as error:
        raise SettingsError(f"配置文件格式错误: {config_path}") from error


def _parse_kubernetes_settings(
    data: dict[str, object],
) -> KubernetesSettings:
    section = _SettingsSection.from_data(data, "kubernetes")
    section.require_fields(
        "environment",
        "namespace",
        "kubeconfig_path",
        "request_timeout_seconds",
    )

    return KubernetesSettings(
        environment=section.string("environment"),
        namespace=section.string("namespace"),
        kubeconfig_path=Path(section.string("kubeconfig_path")),
        request_timeout_seconds=section.positive_integer("request_timeout_seconds"),
    )


def _parse_model_settings(
    data: dict[str, object],
) -> ModelSettings:
    section = _SettingsSection.from_data(data, "model")
    section.require_fields(
        "provider",
        "model",
    )

    return ModelSettings(
        provider=section.string("provider"),
        name=section.string("model"),
        base_url=section.optional_string("base_url"),
        api_key_env=section.optional_string("api_key_env"),
    )


@dataclass(frozen=True)
class _SettingsSection:
    name: str
    values: dict[str, object]

    @classmethod
    def from_data(
        cls,
        data: dict[str, object],
        name: str,
    ) -> Self:
        values = data.get(name)
        if not isinstance(values, dict):
            raise SettingsError(f"缺少 [{name}] 配置区块")
        return cls(name=name, values=values)

    def require_fields(self, *field_names: str) -> None:
        missing_fields = [
            field_name
            for field_name in field_names
            if field_name not in self.values
            or self.values[field_name] is None
            or (
                isinstance(self.values[field_name], str)
                and not self.values[field_name].strip()
            )
        ]
        if missing_fields:
            raise SettingsError(
                f"[{self.name}] 缺少必填配置项或值为空: {', '.join(missing_fields)}"
            )

    def string(self, field_name: str) -> str:
        value = self.values[field_name]
        if not isinstance(value, str):
            raise SettingsError(f"[{self.name}] 配置项必须是字符串: {field_name}")
        return value

    def optional_string(self, field_name: str) -> str | None:
        value = self.values.get(field_name)
        if value is None:
            return None
        if not isinstance(value, str) or not value.strip():
            raise SettingsError(f"[{self.name}] 配置项必须是非空字符串: {field_name}")
        return value

    def positive_integer(self, field_name: str) -> int:
        value = self.values[field_name]
        if not isinstance(value, int) or isinstance(value, bool) or value <= 0:
            raise SettingsError(f"[{self.name}] 配置项必须是正整数: {field_name}")
        return value
