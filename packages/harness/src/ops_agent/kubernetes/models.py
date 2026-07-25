from dataclasses import dataclass


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
