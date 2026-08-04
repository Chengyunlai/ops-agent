"""Kubernetes SDK adapter for an already available read-only Metrics API."""

import re
from collections.abc import Mapping
from datetime import datetime
from decimal import Decimal

from kubernetes.client import ApiClient, CustomObjectsApi
from kubernetes.client.exceptions import ApiException
from kubernetes.utils.quantity import parse_quantity
from urllib3.exceptions import HTTPError

from ops_agent.monitoring.metrics import (
    CachedKubernetesMetricsSource,
    KubernetesMetricsSource,
    KubernetesMetricsUnavailable,
)
from ops_agent.monitoring.models import (
    KubernetesContainerMetrics,
    KubernetesPodMetrics,
    KubernetesPodMetricsSnapshot,
)

_DURATION_PART = re.compile(r"(\d+(?:\.\d+)?)(ns|us|µs|μs|ms|s|m|h)")
_DURATION_MULTIPLIERS = {
    "ns": Decimal("0.000000001"),
    "us": Decimal("0.000001"),
    "µs": Decimal("0.000001"),
    "μs": Decimal("0.000001"),
    "ms": Decimal("0.001"),
    "s": Decimal(1),
    "m": Decimal(60),
    "h": Decimal(3600),
}


class KubernetesMetricsApiSource:
    """Read PodMetrics without installing or modifying any cluster component."""

    def __init__(
        self,
        api: CustomObjectsApi,
        *,
        request_timeout_seconds: int,
    ) -> None:
        self._api = api
        self._request_timeout_seconds = request_timeout_seconds

    def snapshot(self, namespace: str) -> KubernetesPodMetricsSnapshot:
        try:
            payload = self._api.list_namespaced_custom_object(
                group="metrics.k8s.io",
                version="v1beta1",
                namespace=namespace,
                plural="pods",
                _request_timeout=self._request_timeout_seconds,
            )
        except ApiException as error:
            raise KubernetesMetricsUnavailable(_api_failure_message(error)) from error
        except HTTPError as error:
            raise KubernetesMetricsUnavailable(
                f"Metrics API 连接失败: {error}"
            ) from error

        try:
            return _parse_snapshot(payload)
        except (ArithmeticError, KeyError, TypeError, ValueError) as error:
            raise KubernetesMetricsUnavailable(
                f"Metrics API 返回格式无效: {error}"
            ) from error


def create_kubernetes_metrics_source(
    api_client: ApiClient,
    *,
    request_timeout_seconds: int,
    cache_ttl_seconds: float,
) -> KubernetesMetricsSource:
    source = KubernetesMetricsApiSource(
        CustomObjectsApi(api_client),
        request_timeout_seconds=request_timeout_seconds,
    )
    return CachedKubernetesMetricsSource(
        source,
        cache_ttl_seconds=cache_ttl_seconds,
    )


def _parse_snapshot(payload: object) -> KubernetesPodMetricsSnapshot:
    root = _mapping(payload, "response")
    items = root["items"]
    if not isinstance(items, list):
        raise TypeError("items must be a list")
    pods = tuple(_parse_pod(item) for item in items)
    return KubernetesPodMetricsSnapshot(
        observed_at=min((pod.observed_at for pod in pods), default=None),
        pods=pods,
    )


def _parse_pod(value: object) -> KubernetesPodMetrics:
    item = _mapping(value, "item")
    metadata = _mapping(item["metadata"], "metadata")
    name = metadata["name"]
    if not isinstance(name, str) or not name:
        raise ValueError("metadata.name must be a non-empty string")
    containers_value = item["containers"]
    if not isinstance(containers_value, list) or not containers_value:
        raise ValueError("containers must be a non-empty list")
    return KubernetesPodMetrics(
        name=name,
        observed_at=_timestamp(item["timestamp"]),
        window_seconds=_duration_seconds(item["window"]),
        containers=tuple(_parse_container(container) for container in containers_value),
    )


def _parse_container(value: object) -> KubernetesContainerMetrics:
    container = _mapping(value, "container")
    name = container["name"]
    if not isinstance(name, str) or not name:
        raise ValueError("container.name must be a non-empty string")
    usage = _mapping(container["usage"], "usage")
    return KubernetesContainerMetrics(
        name=name,
        cpu_nano_cores=_cpu_nano_cores(usage["cpu"]),
        memory_bytes=_memory_bytes(usage["memory"]),
    )


def _mapping(value: object, label: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{label} must be an object")
    return value


def _cpu_nano_cores(value: object) -> int:
    quantity = _quantity(value, "cpu")
    return int(quantity * Decimal(1_000_000_000))


def _memory_bytes(value: object) -> int:
    return int(_quantity(value, "memory"))


def _quantity(value: object, label: str) -> Decimal:
    if not isinstance(value, str) or not value:
        raise ValueError(f"{label} quantity must be a non-empty string")
    quantity = parse_quantity(value)
    if quantity < 0:
        raise ValueError(f"{label} quantity must not be negative")
    return quantity


def _timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a non-empty string")
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        raise ValueError("timestamp must include a timezone")
    return parsed


def _duration_seconds(value: object) -> float:
    if not isinstance(value, str) or not value:
        raise ValueError("window must be a positive duration")
    position = 0
    total = Decimal(0)
    for match in _DURATION_PART.finditer(value):
        if match.start() != position:
            raise ValueError("window must be a positive duration")
        amount, unit = match.groups()
        total += Decimal(amount) * _DURATION_MULTIPLIERS[unit]
        position = match.end()
    if position != len(value) or total <= 0:
        raise ValueError("window must be a positive duration")
    return float(total)


def _api_failure_message(error: ApiException) -> str:
    if error.status == 403:
        return "Metrics API 没有读取权限（需要 metrics.k8s.io pods list）"
    if error.status == 404:
        return "Metrics API 未安装或未注册"
    if error.status in {429, 500, 502, 503, 504}:
        return f"Metrics API 暂时不可用: {error}"
    return f"Metrics API 查询失败: {error}"
