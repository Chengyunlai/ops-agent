from dataclasses import dataclass
from enum import StrEnum


class KubernetesResourceKind(StrEnum):
    POD = "Pod"
    DEPLOYMENT = "Deployment"
    STATEFUL_SET = "StatefulSet"
    DAEMON_SET = "DaemonSet"
    REPLICA_SET = "ReplicaSet"
    SERVICE = "Service"
    JOB = "Job"
    CRON_JOB = "CronJob"
    INGRESS = "Ingress"
    PERSISTENT_VOLUME_CLAIM = "PersistentVolumeClaim"


@dataclass(frozen=True)
class PodSummary:
    name: str
    phase: str
    restart_count: int
    ready_containers: int = 0
    total_containers: int = 0


@dataclass(frozen=True)
class ContainerSummary:
    name: str
    image: str
    ready: bool
    restart_count: int
    state: str


@dataclass(frozen=True)
class PodDetails:
    name: str
    phase: str
    pod_ip: str | None
    node_name: str | None
    containers: list[ContainerSummary]


@dataclass(frozen=True)
class KubernetesEventSummary:
    type: str
    reason: str
    message: str
    object_kind: str
    object_name: str
    count: int
    last_seen: str | None


@dataclass(frozen=True)
class DeploymentSummary:
    name: str
    desired_replicas: int
    ready_replicas: int
    available_replicas: int
    updated_replicas: int


@dataclass(frozen=True)
class StatefulSetSummary:
    name: str
    desired_replicas: int
    ready_replicas: int
    current_replicas: int
    updated_replicas: int


@dataclass(frozen=True)
class DaemonSetSummary:
    name: str
    desired_scheduled: int
    current_scheduled: int
    ready_scheduled: int
    available_scheduled: int


@dataclass(frozen=True)
class ReplicaSetSummary:
    name: str
    desired_replicas: int
    current_replicas: int
    ready_replicas: int


@dataclass(frozen=True)
class JobSummary:
    name: str
    completions: int
    succeeded: int
    active: int
    failed: int


@dataclass(frozen=True)
class CronJobSummary:
    name: str
    schedule: str
    suspended: bool
    active: int
    last_schedule_time: str | None


@dataclass(frozen=True)
class IngressSummary:
    name: str
    ingress_class: str | None
    hosts: tuple[str, ...]
    addresses: tuple[str, ...]


@dataclass(frozen=True)
class PersistentVolumeClaimSummary:
    name: str
    phase: str
    volume_name: str | None
    capacity: str | None
    access_modes: tuple[str, ...]
    storage_class: str | None


@dataclass(frozen=True)
class ServicePortSummary:
    name: str | None
    port: int
    protocol: str
    target_port: str


@dataclass(frozen=True)
class ServiceSummary:
    name: str
    type: str
    cluster_ip: str | None
    ports: list[ServicePortSummary]
