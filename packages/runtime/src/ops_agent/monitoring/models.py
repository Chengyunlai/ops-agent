"""Kubernetes Monitoring module 的稳定 interface 模型。"""

from dataclasses import dataclass
from datetime import datetime

from ops_agent.diagnostics import FindingSeverity
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


@dataclass(frozen=True)
class KubernetesMonitorSnapshot:
    namespace: str
    observed_at: datetime
    resources: tuple[KubernetesResourceCollection, ...]
    diagnostics: tuple[KubernetesResourceDiagnostic, ...] = ()
    deployment_topologies: tuple[KubernetesDeploymentTopology, ...] = ()
    service_endpoints: tuple[ServiceEndpointSummary, ...] = ()
    diagnostic_errors: tuple[str, ...] = ()

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
