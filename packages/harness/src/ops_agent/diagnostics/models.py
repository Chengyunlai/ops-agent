from dataclasses import dataclass
from enum import StrEnum

from ops_agent.kubernetes import (
    DeploymentSummary,
    PodSummary,
    ReplicaSetSummary,
    ServiceEndpointSummary,
    ServiceSummary,
)


class FindingSeverity(StrEnum):
    WARNING = "warning"


@dataclass(frozen=True)
class Evidence:
    source: str
    message: str


@dataclass(frozen=True)
class Finding:
    severity: FindingSeverity
    resource_kind: str
    resource_name: str
    summary: str
    evidence: tuple[Evidence, ...]


@dataclass(frozen=True)
class KubernetesSnapshot:
    namespace: str
    pods: tuple[PodSummary, ...]
    deployments: tuple[DeploymentSummary, ...]
    replica_sets: tuple[ReplicaSetSummary, ...] = ()
    services: tuple[ServiceSummary, ...] = ()
    service_endpoints: tuple[ServiceEndpointSummary, ...] = ()


@dataclass(frozen=True)
class DiagnosisReport:
    namespace: str
    findings: tuple[Finding, ...]
