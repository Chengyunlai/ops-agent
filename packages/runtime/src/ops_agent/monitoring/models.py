"""Kubernetes Monitoring module 的稳定 interface 模型。"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import StrEnum

from ops_agent.diagnostics import FindingCode, FindingSeverity
from ops_agent.kubernetes import KubernetesResourceKind, ServiceEndpointSummary


@dataclass(frozen=True)
class KubernetesResourceRef:
    kind: KubernetesResourceKind
    name: str


@dataclass(frozen=True)
class KubernetesResourceRow:
    ref: KubernetesResourceRef
    values: tuple[str, ...]
    healthy: bool | None
    health_reasons: tuple[str, ...] = ()


@dataclass(frozen=True)
class KubernetesResourceDiagnostic:
    ref: KubernetesResourceRef
    severity: FindingSeverity
    summary: str
    evidence: tuple[str, ...]
    code: FindingCode | None = None


@dataclass(frozen=True)
class KubernetesPodTopology:
    name: str
    owner_name: str
    phase: str


@dataclass(frozen=True)
class KubernetesReplicaSetTopology:
    name: str
    desired_replicas: int
    ready_replicas: int
    revision: str | None
    pods: tuple[KubernetesPodTopology, ...]


@dataclass(frozen=True)
class KubernetesDeploymentTopology:
    ref: KubernetesResourceRef
    generation: int | None
    observed_generation: int | None
    revision: str | None
    conditions: tuple[str, ...]
    replica_sets: tuple[KubernetesReplicaSetTopology, ...]


@dataclass(frozen=True)
class KubernetesResourceCollection:
    kind: KubernetesResourceKind
    label: str
    shortcut: str | None
    columns: tuple[str, ...]
    rows: tuple[KubernetesResourceRow, ...]
    error: str | None = None


@dataclass(frozen=True)
class KubernetesResourceContent:
    title: str
    content: str


class KubernetesLogLevel(StrEnum):
    DEBUG = "debug"
    INFO = "info"
    WARNING = "warning"
    ERROR = "error"
    UNKNOWN = "unknown"


@dataclass(frozen=True)
class KubernetesLogQuery:
    container: str | None = None
    tail_lines: int | None = 200
    since_seconds: int | None = None

    def __post_init__(self) -> None:
        selected_ranges = sum(
            value is not None for value in (self.tail_lines, self.since_seconds)
        )
        if selected_ranges != 1:
            raise ValueError("日志范围必须且只能设置 tail_lines 或 since_seconds")
        if self.tail_lines is not None and self.tail_lines <= 0:
            raise ValueError("tail_lines 必须大于 0")
        if self.since_seconds is not None and self.since_seconds <= 0:
            raise ValueError("since_seconds 必须大于 0")

    @property
    def range_label(self) -> str:
        if self.tail_lines is not None:
            return f"last {self.tail_lines} lines"
        if self.since_seconds is None:
            raise AssertionError("validated log query has no range")
        minutes, seconds = divmod(self.since_seconds, 60)
        if seconds == 0 and minutes < 60:
            return f"last {minutes}m"
        hours, remaining_minutes = divmod(minutes, 60)
        if seconds == 0 and remaining_minutes == 0:
            return f"last {hours}h"
        return f"last {self.since_seconds}s"


@dataclass(frozen=True)
class KubernetesLogRecord:
    container: str | None
    timestamp: datetime | None
    message: str
    raw: str
    level: KubernetesLogLevel


@dataclass(frozen=True)
class KubernetesLogSourceSnapshot:
    container: str | None
    raw_content: str
    records: tuple[KubernetesLogRecord, ...]
    error: str | None = None


@dataclass(frozen=True)
class KubernetesLogSnapshot:
    namespace: str
    pod_name: str
    observed_at: datetime
    query: KubernetesLogQuery
    sources: tuple[KubernetesLogSourceSnapshot, ...]

    @property
    def records(self) -> tuple[KubernetesLogRecord, ...]:
        return tuple(record for source in self.sources for record in source.records)


@dataclass(frozen=True)
class KubernetesLogFocus:
    hide_info: bool = False
    hide_debug: bool = False
    hide_health_checks: bool = False
    hide_access_logs: bool = False
    hidden_text: tuple[str, ...] = ()


@dataclass(frozen=True)
class KubernetesLogFocusResult:
    records: tuple[KubernetesLogRecord, ...]
    hidden_count: int


@dataclass(frozen=True)
class KubernetesLogSearch:
    text: str = ""
    regex: bool = False
    case_sensitive: bool = False


@dataclass(frozen=True)
class KubernetesLogSearchMatch:
    record_index: int
    spans: tuple[tuple[int, int], ...]


@dataclass(frozen=True)
class KubernetesLogSearchResult:
    matches: tuple[KubernetesLogSearchMatch, ...]
    error: str | None = None


class KubernetesMetricsAvailability(StrEnum):
    DISABLED = "disabled"
    AVAILABLE = "available"
    UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class KubernetesContainerMetrics:
    name: str
    cpu_nano_cores: int
    memory_bytes: int


@dataclass(frozen=True)
class KubernetesPodMetrics:
    name: str
    observed_at: datetime
    window_seconds: float
    containers: tuple[KubernetesContainerMetrics, ...]

    @property
    def cpu_nano_cores(self) -> int:
        return sum(container.cpu_nano_cores for container in self.containers)

    @property
    def memory_bytes(self) -> int:
        return sum(container.memory_bytes for container in self.containers)


@dataclass(frozen=True)
class KubernetesPodMetricsSnapshot:
    observed_at: datetime | None
    pods: tuple[KubernetesPodMetrics, ...]


@dataclass(frozen=True)
class KubernetesMetricsStatus:
    availability: KubernetesMetricsAvailability
    observed_at: datetime | None = None
    error: str | None = None


@dataclass(frozen=True)
class KubernetesMonitorSnapshot:
    namespace: str
    observed_at: datetime
    resources: tuple[KubernetesResourceCollection, ...]
    diagnostics: tuple[KubernetesResourceDiagnostic, ...] = ()
    deployment_topologies: tuple[KubernetesDeploymentTopology, ...] = ()
    service_endpoints: tuple[ServiceEndpointSummary, ...] = ()
    diagnostic_errors: tuple[str, ...] = ()
    metrics: KubernetesMetricsStatus = field(
        default_factory=lambda: KubernetesMetricsStatus(
            availability=KubernetesMetricsAvailability.DISABLED,
        )
    )

    @property
    def finding_count(self) -> int:
        return len(self.diagnostics)

    def collection(
        self,
        kind: KubernetesResourceKind,
    ) -> KubernetesResourceCollection | None:
        return next(
            (resource for resource in self.resources if resource.kind is kind),
            None,
        )

    def diagnostics_for(
        self,
        resource: KubernetesResourceRef,
    ) -> tuple[KubernetesResourceDiagnostic, ...]:
        return tuple(
            diagnostic for diagnostic in self.diagnostics if diagnostic.ref == resource
        )

    def deployment_topology(
        self,
        resource: KubernetesResourceRef,
    ) -> KubernetesDeploymentTopology | None:
        return next(
            (
                topology
                for topology in self.deployment_topologies
                if topology.ref == resource
            ),
            None,
        )

    def service_endpoint(
        self,
        resource: KubernetesResourceRef,
    ) -> ServiceEndpointSummary | None:
        if resource.kind is not KubernetesResourceKind.SERVICE:
            return None
        return next(
            (
                endpoint
                for endpoint in self.service_endpoints
                if endpoint.service_name == resource.name
            ),
            None,
        )
