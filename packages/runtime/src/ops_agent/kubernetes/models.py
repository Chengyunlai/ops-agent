from dataclasses import dataclass
from datetime import datetime
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


class ContainerResourceType(StrEnum):
    APP = "app"
    INIT = "init"


@dataclass(frozen=True)
class ControllerReferenceSummary:
    kind: str
    name: str


@dataclass(frozen=True)
class ContainerStatusSummary:
    name: str
    ready: bool
    restart_count: int
    state: str
    reason: str | None = None
    exit_code: int | None = None
    previous_reason: str | None = None
    previous_exit_code: int | None = None


@dataclass(frozen=True)
class ContainerResourceSummary:
    """Pod spec 中一个容器声明的原生 Kubernetes 资源约束。"""

    name: str
    container_type: ContainerResourceType = ContainerResourceType.APP
    cpu_request: str | None = None
    cpu_limit: str | None = None
    memory_request: str | None = None
    memory_limit: str | None = None
    ephemeral_storage_request: str | None = None
    ephemeral_storage_limit: str | None = None


@dataclass(frozen=True)
class PodConditionSummary:
    type: str
    status: str
    reason: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class PodSummary:
    name: str
    phase: str
    restart_count: int
    ready_containers: int = 0
    total_containers: int = 0
    created_at: datetime | None = None
    container_statuses: tuple[ContainerStatusSummary, ...] = ()
    conditions: tuple[PodConditionSummary, ...] = ()
    controller: ControllerReferenceSummary | None = None
    status_reason: str | None = None
    status_message: str | None = None
    qos_class: str | None = None
    resources: tuple[ContainerResourceSummary, ...] = ()


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
class DeploymentConditionSummary:
    type: str
    status: str
    reason: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class DeploymentSummary:
    name: str
    desired_replicas: int
    ready_replicas: int
    available_replicas: int
    updated_replicas: int
    generation: int | None = None
    observed_generation: int | None = None
    revision: str | None = None
    conditions: tuple[DeploymentConditionSummary, ...] = ()


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
    revision: str | None = None
    controller: ControllerReferenceSummary | None = None


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
class PersistentVolumeMountSummary:
    claim_name: str
    pod_name: str
    pod_phase: str
    container_name: str
    mount_path: str
    read_only: bool
    container_running: bool = False


@dataclass(frozen=True)
class PersistentVolumeClaimSummary:
    name: str
    phase: str
    volume_name: str | None
    capacity: str | None
    access_modes: tuple[str, ...]
    storage_class: str | None
    backend: str | None = None
    backend_error: str | None = None
    reclaim_policy: str | None = None
    mounts: tuple[PersistentVolumeMountSummary, ...] = ()
    mounts_error: str | None = None


class VolumeEntryKind(StrEnum):
    DIRECTORY = "directory"
    FILE = "file"
    SYMLINK = "symlink"
    OTHER = "other"


@dataclass(frozen=True)
class VolumeEntry:
    name: str
    kind: VolumeEntryKind
    size_bytes: int | None


@dataclass(frozen=True)
class VolumeDirectory:
    claim_name: str
    path: str
    target: PersistentVolumeMountSummary
    entries: tuple[VolumeEntry, ...]


@dataclass(frozen=True)
class VolumeFilePreview:
    claim_name: str
    path: str
    target: PersistentVolumeMountSummary
    content: str
    truncated: bool


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


class ServiceEndpointSource(StrEnum):
    ENDPOINT_SLICE = "EndpointSlice"
    ENDPOINTS = "Endpoints"


@dataclass(frozen=True)
class ServiceEndpointTargetSummary:
    address: str
    ready: bool
    target_kind: str | None = None
    target_name: str | None = None


@dataclass(frozen=True)
class ServiceEndpointSummary:
    service_name: str
    ready_addresses: int
    not_ready_addresses: int
    endpoint_slice_count: int
    source: ServiceEndpointSource = ServiceEndpointSource.ENDPOINT_SLICE
    targets: tuple[ServiceEndpointTargetSummary, ...] = ()
