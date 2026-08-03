from pathlib import Path
from typing import Annotated

from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    HttpUrl,
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


class KubernetesConnectionSettings(BaseModel):
    """Core Reader 实际消费的 Kubernetes 连接约束。"""

    model_config = ConfigDict(
        extra="forbid",
        frozen=True,
        validate_by_alias=True,
        validate_by_name=True,
    )

    environment: _NonEmptyString = Field(description="运行环境标识")
    namespace: _NonEmptyString = Field(description="固定 Kubernetes namespace")
    kubeconfig_path: _ConfigPath = Field(description="kubeconfig 文件路径")
    request_timeout_seconds: _PositiveInteger = Field(
        description="Kubernetes 请求超时秒数"
    )
    proxy_url: HttpUrl | None = Field(
        default=None,
        description="可选的 Kubernetes API HTTP(S) 代理地址",
    )
