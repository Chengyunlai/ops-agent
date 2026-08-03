from enum import StrEnum
from pathlib import Path
from typing import Annotated

from ops_agent.kubernetes import KubernetesConnectionSettings
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    StrictBool,
    StrictFloat,
    StrictInt,
    StrictStr,
)
from pydantic.types import StringConstraints

_NonEmptyString = Annotated[
    StrictStr,
    StringConstraints(strip_whitespace=True, min_length=1),
]
_PositiveInteger = Annotated[
    StrictInt,
    Field(gt=0),
]
_PositiveNumber = Annotated[
    StrictFloat,
    Field(gt=0, allow_inf_nan=False),
]
_HexColor = Annotated[
    StrictStr,
    StringConstraints(pattern=r"^#[0-9A-Fa-f]{6}$"),
]
_ShellEnvironmentValue = Annotated[
    StrictStr,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        pattern=r"^[A-Za-z0-9_.@+-]+$",
    ),
]


def _validate_path_input(value: object) -> object:
    if isinstance(value, Path):
        return value
    if isinstance(value, str) and value.strip():
        return value
    raise ValueError("路径必须是非空字符串")


_ConfigPath = Annotated[
    Path,
    BeforeValidator(_validate_path_input),
]


class _ConfigModel(BaseModel):
    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_by_alias=True,
        validate_by_name=True,
    )


class InteractiveExecSettings(_ConfigModel):
    enabled: StrictBool = Field(
        default=False,
        description="是否允许用户从资源监盘启动交互式 Pod Shell",
    )
    locale: _ShellEnvironmentValue = Field(
        default="auto",
        description="Pod Shell UTF-8 locale；auto 自动探测容器支持项",
    )
    terminal_type: _ShellEnvironmentValue = Field(
        default="xterm-256color",
        description="Pod Shell TERM 值",
    )
    color: StrictBool = Field(
        default=True,
        description="是否为支持 GNU/BusyBox color 参数的 ls 启用颜色",
    )


class DownloadSettings(_ConfigModel):
    directory: _ConfigPath = Field(
        default=Path("~/Downloads/ops-agent"),
        description="Artifact Download 本机保存根目录",
    )


class PodTransferStrategy(StrEnum):
    AUTO = "auto"
    EXEC_CAT = "exec-cat"
    EXEC_DD = "exec-dd"


class PodTransferSettings(_ConfigModel):
    strategy: PodTransferStrategy = Field(
        default=PodTransferStrategy.AUTO,
        description="Pod 文件传输后端选择策略",
    )
    max_file_size_mb: _PositiveInteger = Field(
        default=512,
        description="单个 Pod 文件允许下载的最大 MiB",
    )


class KubernetesWatchSettings(_ConfigModel):
    enabled: StrictBool = Field(
        default=True,
        description="是否使用只读 Kubernetes Watch 加速监盘刷新",
    )
    timeout_seconds: _PositiveInteger = Field(
        default=10,
        description="单次 Kubernetes Watch 的服务端超时秒数",
    )
    poll_interval_seconds: _PositiveNumber = Field(
        default=5.0,
        description="Watch 不可用时及一致性兜底的完整轮询间隔秒数",
    )


class KubernetesSettings(KubernetesConnectionSettings):
    watch: KubernetesWatchSettings = Field(
        default_factory=KubernetesWatchSettings,
        description="Kubernetes Watch 增量刷新与轮询兜底配置",
    )
    interactive_exec: InteractiveExecSettings = Field(
        default_factory=InteractiveExecSettings,
        description="人工 Pod 交互式终端配置",
    )
    downloads: DownloadSettings = Field(
        default_factory=DownloadSettings,
        description="Pod/PVC 文件下载配置",
    )
    pod_transfer: PodTransferSettings = Field(
        default_factory=PodTransferSettings,
        description="Pod 文件传输策略",
    )


class ModelSettings(_ConfigModel):
    provider: _NonEmptyString = Field(description="LangChain 模型适配器")
    name: _NonEmptyString = Field(
        alias="model",
        description="支持工具调用的模型名称",
    )
    base_url: _NonEmptyString | None = Field(
        default=None,
        description="可选的模型接口地址",
    )
    api_key_env: _NonEmptyString | None = Field(
        default=None,
        description="保存模型密钥的环境变量名称",
    )


class ThemeName(StrEnum):
    OPS_DARK = "ops-dark"
    LIGHT = "light"
    HIGH_CONTRAST = "high-contrast"


class ProjectSettings(_ConfigModel):
    name: _NonEmptyString = Field(
        default="Ops Project",
        description="Project Profile 显示名称",
    )


class TuiColorSettings(_ConfigModel):
    primary: _HexColor | None = Field(default=None, description="主题主色")
    accent: _HexColor | None = Field(default=None, description="主题强调色")
    background: _HexColor | None = Field(default=None, description="主题背景色")
    foreground: _HexColor | None = Field(default=None, description="主题文字色")
    warning: _HexColor | None = Field(default=None, description="主题警告色")


class TuiSettings(_ConfigModel):
    theme: ThemeName = Field(
        default=ThemeName.OPS_DARK,
        description="TUI 预设主题",
    )
    colors: TuiColorSettings = Field(default_factory=TuiColorSettings)


class Settings(_ConfigModel):
    project: ProjectSettings = Field(default_factory=ProjectSettings)
    kubernetes: KubernetesSettings
    model: ModelSettings
    tui: TuiSettings = Field(default_factory=TuiSettings)
