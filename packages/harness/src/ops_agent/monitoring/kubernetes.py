from collections.abc import Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol

from ops_agent.kubernetes import (
    DeploymentSummary,
    PodSummary,
    ServiceSummary,
)


class KubernetesMonitoringSource(Protocol):
    def list_pods(self, namespace: str) -> Sequence[PodSummary]: ...

    def list_deployments(
        self,
        namespace: str,
    ) -> Sequence[DeploymentSummary]: ...

    def list_services(
        self,
        namespace: str,
    ) -> Sequence[ServiceSummary]: ...


@dataclass(frozen=True)
class KubernetesMonitorSnapshot:
    namespace: str
    observed_at: datetime
    pods: tuple[PodSummary, ...]
    deployments: tuple[DeploymentSummary, ...]
    services: tuple[ServiceSummary, ...]


class KubernetesMonitor:
    """读取固定 namespace 的只读资源快照。"""

    def __init__(
        self,
        source: KubernetesMonitoringSource,
        *,
        namespace: str,
    ) -> None:
        self._source = source
        self._namespace = namespace

    def snapshot(self) -> KubernetesMonitorSnapshot:
        return KubernetesMonitorSnapshot(
            namespace=self._namespace,
            observed_at=datetime.now(UTC),
            pods=tuple(self._source.list_pods(self._namespace)),
            deployments=tuple(self._source.list_deployments(self._namespace)),
            services=tuple(self._source.list_services(self._namespace)),
        )
