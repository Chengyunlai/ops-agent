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


class FindingCode(StrEnum):
    POD_CRASH_LOOP = "pod_crash_loop"
    POD_OOM_KILLED = "pod_oom_killed"


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
    container_name: str | None = None
    code: FindingCode | None = None


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
