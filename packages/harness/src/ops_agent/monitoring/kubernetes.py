from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Protocol, TypeVar

from ops_agent.diagnostics import (
    Finding,
    KubernetesSnapshot,
    diagnose_kubernetes_snapshot,
)
from ops_agent.kubernetes import (
    CronJobSummary,
    DaemonSetSummary,
    DeploymentSummary,
    IngressSummary,
    JobSummary,
    KubernetesResourceKind,
    PersistentVolumeClaimSummary,
    PersistentVolumeMountSummary,
    PodDetails,
    PodSummary,
    ReplicaSetSummary,
    ServiceEndpointSummary,
    ServiceSummary,
    StatefulSetSummary,
    VolumeDirectory,
    VolumeFilePreview,
)


class KubernetesMonitoringSource(Protocol):
    def list_pods(self, namespace: str) -> Sequence[PodSummary]: ...

    def list_deployments(
        self,
        namespace: str,
    ) -> Sequence[DeploymentSummary]: ...

    def list_stateful_sets(
        self,
        namespace: str,
    ) -> Sequence[StatefulSetSummary]: ...

    def list_daemon_sets(
        self,
        namespace: str,
    ) -> Sequence[DaemonSetSummary]: ...

    def list_replica_sets(
        self,
        namespace: str,
    ) -> Sequence[ReplicaSetSummary]: ...

    def list_services(
        self,
        namespace: str,
    ) -> Sequence[ServiceSummary]: ...

    def list_service_endpoints(
        self,
        namespace: str,
    ) -> Sequence[ServiceEndpointSummary]: ...

    def list_jobs(self, namespace: str) -> Sequence[JobSummary]: ...

    def list_cron_jobs(self, namespace: str) -> Sequence[CronJobSummary]: ...

    def list_ingresses(self, namespace: str) -> Sequence[IngressSummary]: ...

    def list_persistent_volume_claims(
        self,
        namespace: str,
    ) -> Sequence[PersistentVolumeClaimSummary]: ...

    def describe_resource(
        self,
        namespace: str,
        kind: KubernetesResourceKind,
        name: str,
    ) -> str: ...

    def get_pod_details(
        self,
        namespace: str,
        pod_name: str,
    ) -> PodDetails: ...

    def get_pod_logs(
        self,
        namespace: str,
        pod_name: str,
        *,
        container: str | None,
        tail_lines: int,
    ) -> str: ...

    def browse_persistent_volume_claim(
        self,
        namespace: str,
        claim_name: str,
        *,
        path: str,
    ) -> VolumeDirectory: ...

    def preview_persistent_volume_claim_file(
        self,
        namespace: str,
        claim_name: str,
        *,
        path: str,
        max_bytes: int,
    ) -> VolumeFilePreview: ...


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
    severity: str
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


Summary = TypeVar("Summary")


class KubernetesMonitor:
    """读取固定 namespace 的只读资源目录、详情与日志。"""

    def __init__(
        self,
        source: KubernetesMonitoringSource,
        *,
        namespace: str,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._source = source
        self._namespace = namespace
        self._clock = clock or _utc_now
        self._latest_snapshot: KubernetesMonitorSnapshot | None = None

    def snapshot(self) -> KubernetesMonitorSnapshot:
        observed_at = self._clock()
        pods, pod_items = self._capture_observations(
            kind=KubernetesResourceKind.POD,
            label="Pods",
            shortcut="1",
            columns=("NAME", "READY", "STATUS", "RESTARTS", "AGE"),
            request=self._source.list_pods,
            to_row=lambda pod: _pod_row(pod, observed_at=observed_at),
        )
        deployments, deployment_items = self._capture_observations(
            kind=KubernetesResourceKind.DEPLOYMENT,
            label="Deployments",
            shortcut="2",
            columns=("NAME", "READY", "AVAILABLE", "UPDATED"),
            request=self._source.list_deployments,
            to_row=_deployment_row,
        )
        services, service_items = self._capture_observations(
            kind=KubernetesResourceKind.SERVICE,
            label="Services",
            shortcut="5",
            columns=("NAME", "TYPE", "CLUSTER-IP", "PORTS"),
            request=self._source.list_services,
            to_row=_service_row,
        )
        replica_sets, replica_set_items = self._capture_observations(
            kind=KubernetesResourceKind.REPLICA_SET,
            label="ReplicaSets",
            shortcut="6",
            columns=("NAME", "READY", "CURRENT", "DESIRED"),
            request=self._source.list_replica_sets,
            to_row=_replica_set_row,
        )
        endpoint_items, endpoint_error = self._read_service_endpoints()
        report = diagnose_kubernetes_snapshot(
            KubernetesSnapshot(
                namespace=self._namespace,
                pods=pod_items,
                deployments=deployment_items,
                replica_sets=replica_set_items,
                services=service_items if endpoint_error is None else (),
                service_endpoints=endpoint_items,
            )
        )
        diagnostics = tuple(_to_monitor_diagnostic(item) for item in report.findings)
        reasons = _health_reasons_by_resource(diagnostics)
        snapshot = KubernetesMonitorSnapshot(
            namespace=self._namespace,
            observed_at=observed_at,
            resources=(
                _with_health_reasons(pods, reasons),
                _with_health_reasons(deployments, reasons),
                self._capture(
                    kind=KubernetesResourceKind.STATEFUL_SET,
                    label="StatefulSets",
                    shortcut="3",
                    columns=("NAME", "READY", "CURRENT", "UPDATED"),
                    request=self._source.list_stateful_sets,
                    to_row=_stateful_set_row,
                ),
                self._capture(
                    kind=KubernetesResourceKind.DAEMON_SET,
                    label="DaemonSets",
                    shortcut="4",
                    columns=("NAME", "READY", "CURRENT", "AVAILABLE"),
                    request=self._source.list_daemon_sets,
                    to_row=_daemon_set_row,
                ),
                _with_health_reasons(services, reasons),
                _with_health_reasons(replica_sets, reasons),
                self._capture(
                    kind=KubernetesResourceKind.JOB,
                    label="Jobs",
                    shortcut=None,
                    columns=("NAME", "STATUS", "SUCCEEDED", "ACTIVE", "FAILED"),
                    request=self._source.list_jobs,
                    to_row=_job_row,
                ),
                self._capture(
                    kind=KubernetesResourceKind.CRON_JOB,
                    label="CronJobs",
                    shortcut=None,
                    columns=("NAME", "SCHEDULE", "SUSPEND", "ACTIVE", "LAST"),
                    request=self._source.list_cron_jobs,
                    to_row=_cron_job_row,
                ),
                self._capture(
                    kind=KubernetesResourceKind.INGRESS,
                    label="Ingresses",
                    shortcut=None,
                    columns=("NAME", "CLASS", "HOSTS", "ADDRESS"),
                    request=self._source.list_ingresses,
                    to_row=_ingress_row,
                ),
                self._capture(
                    kind=KubernetesResourceKind.PERSISTENT_VOLUME_CLAIM,
                    label="PVCs",
                    shortcut="7",
                    columns=(
                        "NAME",
                        "STATUS",
                        "VOLUME",
                        "CAPACITY",
                        "STORAGECLASS",
                        "BACKEND",
                        "MOUNTED BY",
                        "MOUNT PATHS",
                    ),
                    request=self._source.list_persistent_volume_claims,
                    to_row=_pvc_row,
                ),
            ),
            diagnostics=diagnostics,
            deployment_topologies=_deployment_topologies(
                deployments=deployment_items,
                replica_sets=replica_set_items,
                pods=pod_items,
            ),
            diagnostic_errors=(
                (f"Service Endpoint 诊断不可用：{endpoint_error}",)
                if endpoint_error is not None
                else ()
            ),
        )
        self._latest_snapshot = snapshot
        return snapshot

    def diagnostics(
        self,
        resource: KubernetesResourceRef,
    ) -> KubernetesResourceContent:
        snapshot = self._latest_snapshot
        if snapshot is None:
            raise RuntimeError("尚未读取 Kubernetes 资源快照")
        return KubernetesResourceContent(
            title=f"Health · {resource.kind}/{resource.name}",
            content=_format_resource_diagnostics(snapshot, resource),
        )

    def describe(
        self,
        resource: KubernetesResourceRef,
    ) -> KubernetesResourceContent:
        return KubernetesResourceContent(
            title=f"Describe · {resource.kind}/{resource.name}",
            content=self._source.describe_resource(
                self._namespace,
                resource.kind,
                resource.name,
            ),
        )

    def pod_logs(
        self,
        resource: KubernetesResourceRef,
        *,
        tail_lines: int = 200,
    ) -> KubernetesResourceContent:
        if resource.kind is not KubernetesResourceKind.POD:
            raise ValueError("日志仅支持 Pod")
        details = self._source.get_pod_details(self._namespace, resource.name)
        containers = [container.name for container in details.containers]
        if not containers:
            containers = [None]
        logs = [
            (
                container,
                self._read_container_logs(
                    resource.name,
                    container=container,
                    tail_lines=tail_lines,
                ),
            )
            for container in containers
        ]
        if len(logs) == 1:
            content = logs[0][1]
            container_label = f" · {logs[0][0]}" if logs[0][0] is not None else ""
        else:
            content = "\n\n".join(
                f"===== container: {container} =====\n{container_logs}"
                for container, container_logs in logs
            )
            container_label = f" · all {len(logs)} containers"
        return KubernetesResourceContent(
            title=(
                f"Logs · Pod/{resource.name}{container_label}"
                f" · last {tail_lines} lines/container"
            ),
            content=content,
        )

    def pod_containers(
        self,
        resource: KubernetesResourceRef,
    ) -> tuple[str, ...]:
        if resource.kind is not KubernetesResourceKind.POD:
            raise ValueError("容器选择仅支持 Pod")
        details = self._source.get_pod_details(
            self._namespace,
            resource.name,
        )
        return tuple(container.name for container in details.containers)

    def browse_pvc(
        self,
        resource: KubernetesResourceRef,
        *,
        path: str = ".",
    ) -> VolumeDirectory:
        _require_pvc(resource)
        return self._source.browse_persistent_volume_claim(
            self._namespace,
            resource.name,
            path=path,
        )

    def preview_pvc_file(
        self,
        resource: KubernetesResourceRef,
        *,
        path: str,
        max_bytes: int = 64 * 1024,
    ) -> KubernetesResourceContent:
        _require_pvc(resource)
        preview = self._source.preview_persistent_volume_claim_file(
            self._namespace,
            resource.name,
            path=path,
            max_bytes=max_bytes,
        )
        target = preview.target
        truncation_note = (
            f"\n\n[预览已截断：最多 {max_bytes} bytes]" if preview.truncated else ""
        )
        return KubernetesResourceContent(
            title=f"PVC/{resource.name} · {preview.path}",
            content=(
                f"Pod: {target.pod_name} · Container: {target.container_name}"
                f" · Mount: {target.mount_path}\n\n"
                f"{preview.content}{truncation_note}"
            ),
        )

    def _read_container_logs(
        self,
        pod_name: str,
        *,
        container: str | None,
        tail_lines: int,
    ) -> str:
        try:
            return self._source.get_pod_logs(
                self._namespace,
                pod_name,
                container=container,
                tail_lines=tail_lines,
            )
        except Exception as error:  # noqa: BLE001 - 其他容器日志仍应可读
            return f"[读取失败] {error}"

    def _capture(
        self,
        *,
        kind: KubernetesResourceKind,
        label: str,
        shortcut: str | None,
        columns: tuple[str, ...],
        request: Callable[[str], Sequence[Summary]],
        to_row: Callable[[Summary], KubernetesResourceRow],
    ) -> KubernetesResourceCollection:
        collection, _ = self._capture_observations(
            kind=kind,
            label=label,
            shortcut=shortcut,
            columns=columns,
            request=request,
            to_row=to_row,
        )
        return collection

    def _capture_observations(
        self,
        *,
        kind: KubernetesResourceKind,
        label: str,
        shortcut: str | None,
        columns: tuple[str, ...],
        request: Callable[[str], Sequence[Summary]],
        to_row: Callable[[Summary], KubernetesResourceRow],
    ) -> tuple[KubernetesResourceCollection, tuple[Summary, ...]]:
        try:
            items = tuple(request(self._namespace))
            rows = tuple(to_row(item) for item in items)
        except Exception as error:  # noqa: BLE001 - 每类资源必须能独立降级
            return (
                KubernetesResourceCollection(
                    kind=kind,
                    label=label,
                    shortcut=shortcut,
                    columns=columns,
                    rows=(),
                    error=str(error),
                ),
                (),
            )
        return (
            KubernetesResourceCollection(
                kind=kind,
                label=label,
                shortcut=shortcut,
                columns=columns,
                rows=rows,
            ),
            items,
        )

    def _read_service_endpoints(
        self,
    ) -> tuple[tuple[ServiceEndpointSummary, ...], str | None]:
        try:
            return tuple(self._source.list_service_endpoints(self._namespace)), None
        except Exception as error:  # noqa: BLE001 - 清单仍可展示，诊断明确降级
            return (), str(error)


def _to_monitor_diagnostic(finding: Finding) -> KubernetesResourceDiagnostic:
    resource_kind = KubernetesResourceKind(finding.resource_kind)
    return KubernetesResourceDiagnostic(
        ref=KubernetesResourceRef(
            kind=resource_kind,
            name=finding.resource_name,
        ),
        severity=str(finding.severity),
        summary=finding.summary,
        evidence=tuple(f"{item.source}: {item.message}" for item in finding.evidence),
    )


def _health_reasons_by_resource(
    diagnostics: tuple[KubernetesResourceDiagnostic, ...],
) -> dict[KubernetesResourceRef, tuple[str, ...]]:
    reasons: dict[KubernetesResourceRef, list[str]] = {}
    for diagnostic in diagnostics:
        summaries = reasons.setdefault(diagnostic.ref, [])
        if diagnostic.summary not in summaries:
            summaries.append(diagnostic.summary)
    return {resource: tuple(items) for resource, items in reasons.items()}


def _with_health_reasons(
    collection: KubernetesResourceCollection,
    reasons: dict[KubernetesResourceRef, tuple[str, ...]],
) -> KubernetesResourceCollection:
    return KubernetesResourceCollection(
        kind=collection.kind,
        label=collection.label,
        shortcut=collection.shortcut,
        columns=collection.columns,
        rows=tuple(
            KubernetesResourceRow(
                ref=row.ref,
                values=row.values,
                healthy=False if reasons.get(row.ref) else row.healthy,
                health_reasons=reasons.get(row.ref, ()),
            )
            for row in collection.rows
        ),
        error=collection.error,
    )


def _deployment_topologies(
    *,
    deployments: tuple[DeploymentSummary, ...],
    replica_sets: tuple[ReplicaSetSummary, ...],
    pods: tuple[PodSummary, ...],
) -> tuple[KubernetesDeploymentTopology, ...]:
    return tuple(
        _deployment_topology(
            deployment,
            replica_sets=replica_sets,
            pods=pods,
        )
        for deployment in deployments
    )


def _deployment_topology(
    deployment: DeploymentSummary,
    *,
    replica_sets: tuple[ReplicaSetSummary, ...],
    pods: tuple[PodSummary, ...],
) -> KubernetesDeploymentTopology:
    owned_replica_sets = sorted(
        (
            replica_set
            for replica_set in replica_sets
            if replica_set.controller is not None
            and replica_set.controller.kind == "Deployment"
            and replica_set.controller.name == deployment.name
        ),
        key=lambda item: item.name,
    )
    return KubernetesDeploymentTopology(
        ref=_ref(KubernetesResourceKind.DEPLOYMENT, deployment.name),
        generation=deployment.generation,
        observed_generation=deployment.observed_generation,
        revision=deployment.revision,
        conditions=tuple(
            (
                f"{condition.type}={condition.status}"
                f" · {condition.reason or '-'}"
                f" · {condition.message or '-'}"
            )
            for condition in deployment.conditions
        ),
        replica_sets=tuple(
            KubernetesReplicaSetTopology(
                name=replica_set.name,
                desired_replicas=replica_set.desired_replicas,
                ready_replicas=replica_set.ready_replicas,
                revision=replica_set.revision,
                pods=tuple(
                    KubernetesPodTopology(
                        name=pod.name,
                        owner_name=replica_set.name,
                        phase=pod.phase,
                    )
                    for pod in sorted(pods, key=lambda item: item.name)
                    if pod.controller is not None
                    and pod.controller.kind == "ReplicaSet"
                    and pod.controller.name == replica_set.name
                ),
            )
            for replica_set in owned_replica_sets
        ),
    )


def _format_resource_diagnostics(
    snapshot: KubernetesMonitorSnapshot,
    resource: KubernetesResourceRef,
) -> str:
    diagnostics = snapshot.diagnostics_for(resource)
    lines = [f"Resource: {resource.kind}/{resource.name}", ""]
    if diagnostics:
        lines.append(f"Findings ({len(diagnostics)}):")
        for diagnostic in diagnostics:
            lines.append(f"  ! {diagnostic.summary}")
            lines.extend(f"      {evidence}" for evidence in diagnostic.evidence)
    else:
        lines.append("Findings: No deterministic warning in the latest snapshot")

    topology = snapshot.deployment_topology(resource)
    if topology is not None:
        lines.extend(
            (
                "",
                "Rollout:",
                "  Generation: {} · Observed: {} · Revision: {}".format(
                    topology.generation or "-",
                    topology.observed_generation or "-",
                    topology.revision or "-",
                ),
                "  Conditions:",
            )
        )
        lines.extend(
            (f"    {condition}" for condition in topology.conditions)
            if topology.conditions
            else ("    none",)
        )
        lines.append("  Topology:")
        if not topology.replica_sets:
            lines.append("    no controller-owned ReplicaSet observed")
        for replica_set in topology.replica_sets:
            lines.append(
                f"    ReplicaSet/{replica_set.name}"
                f" · desired {replica_set.desired_replicas}"
                f" · ready {replica_set.ready_replicas}"
                f" · revision {replica_set.revision or '-'}"
            )
            lines.extend(
                (
                    f"      Pod/{pod.name} · owner {pod.owner_name} · phase {pod.phase}"
                    for pod in replica_set.pods
                )
                if replica_set.pods
                else ("      no controller-owned Pod observed",)
            )

    if snapshot.diagnostic_errors:
        lines.extend(("", "Partial diagnostics:"))
        lines.extend(f"  {error}" for error in snapshot.diagnostic_errors)
    return "\n".join(lines)


def _ref(kind: KubernetesResourceKind, name: str) -> KubernetesResourceRef:
    return KubernetesResourceRef(kind=kind, name=name)


def _pod_row(
    pod: PodSummary,
    *,
    observed_at: datetime,
) -> KubernetesResourceRow:
    healthy = pod.phase == "Running" and pod.ready_containers == pod.total_containers
    return KubernetesResourceRow(
        ref=_ref(KubernetesResourceKind.POD, pod.name),
        values=(
            pod.name,
            f"{pod.ready_containers}/{pod.total_containers}",
            pod.phase,
            str(pod.restart_count),
            _format_age(pod.created_at, observed_at=observed_at),
        ),
        healthy=healthy,
    )


def _format_age(
    created_at: datetime | None,
    *,
    observed_at: datetime,
) -> str:
    if created_at is None:
        return "-"

    age_seconds = max(0, int((observed_at - created_at).total_seconds()))
    if age_seconds < 60:
        return f"{age_seconds}s"
    age_minutes = age_seconds // 60
    if age_minutes < 60:
        return f"{age_minutes}m"
    age_hours = age_minutes // 60
    if age_hours < 24:
        return f"{age_hours}h"
    age_days = age_hours // 24
    if age_days < 365:
        return f"{age_days}d"
    return f"{age_days // 365}y"


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _deployment_row(item: DeploymentSummary) -> KubernetesResourceRow:
    return KubernetesResourceRow(
        ref=_ref(KubernetesResourceKind.DEPLOYMENT, item.name),
        values=(
            item.name,
            f"{item.ready_replicas}/{item.desired_replicas}",
            str(item.available_replicas),
            str(item.updated_replicas),
        ),
        healthy=item.ready_replicas == item.desired_replicas,
    )


def _stateful_set_row(item: StatefulSetSummary) -> KubernetesResourceRow:
    return KubernetesResourceRow(
        ref=_ref(KubernetesResourceKind.STATEFUL_SET, item.name),
        values=(
            item.name,
            f"{item.ready_replicas}/{item.desired_replicas}",
            str(item.current_replicas),
            str(item.updated_replicas),
        ),
        healthy=item.ready_replicas == item.desired_replicas,
    )


def _daemon_set_row(item: DaemonSetSummary) -> KubernetesResourceRow:
    return KubernetesResourceRow(
        ref=_ref(KubernetesResourceKind.DAEMON_SET, item.name),
        values=(
            item.name,
            f"{item.ready_scheduled}/{item.desired_scheduled}",
            str(item.current_scheduled),
            str(item.available_scheduled),
        ),
        healthy=item.ready_scheduled == item.desired_scheduled,
    )


def _service_row(item: ServiceSummary) -> KubernetesResourceRow:
    ports = ", ".join(f"{port.port}/{port.protocol}" for port in item.ports)
    return KubernetesResourceRow(
        ref=_ref(KubernetesResourceKind.SERVICE, item.name),
        values=(item.name, item.type, item.cluster_ip or "-", ports or "-"),
        healthy=None,
    )


def _replica_set_row(item: ReplicaSetSummary) -> KubernetesResourceRow:
    return KubernetesResourceRow(
        ref=_ref(KubernetesResourceKind.REPLICA_SET, item.name),
        values=(
            item.name,
            str(item.ready_replicas),
            str(item.current_replicas),
            str(item.desired_replicas),
        ),
        healthy=item.ready_replicas == item.desired_replicas,
    )


def _job_row(item: JobSummary) -> KubernetesResourceRow:
    if item.failed:
        status = "Failed"
    elif item.completions and item.succeeded >= item.completions:
        status = "Complete"
    else:
        status = "Running"
    return KubernetesResourceRow(
        ref=_ref(KubernetesResourceKind.JOB, item.name),
        values=(
            item.name,
            status,
            f"{item.succeeded}/{item.completions}",
            str(item.active),
            str(item.failed),
        ),
        healthy=item.failed == 0,
    )


def _cron_job_row(item: CronJobSummary) -> KubernetesResourceRow:
    return KubernetesResourceRow(
        ref=_ref(KubernetesResourceKind.CRON_JOB, item.name),
        values=(
            item.name,
            item.schedule,
            str(item.suspended),
            str(item.active),
            item.last_schedule_time or "-",
        ),
        healthy=None,
    )


def _ingress_row(item: IngressSummary) -> KubernetesResourceRow:
    return KubernetesResourceRow(
        ref=_ref(KubernetesResourceKind.INGRESS, item.name),
        values=(
            item.name,
            item.ingress_class or "-",
            ",".join(item.hosts) or "-",
            ",".join(item.addresses) or "-",
        ),
        healthy=bool(item.addresses),
    )


def _pvc_row(item: PersistentVolumeClaimSummary) -> KubernetesResourceRow:
    mounted_by = (
        _unavailable_cell(item.mounts_error)
        if item.mounts_error is not None
        else ", ".join(_mount_target_label(mount) for mount in item.mounts)
    )
    mount_paths = ", ".join(_mount_path_label(mount) for mount in item.mounts)
    backend = (
        _unavailable_cell(item.backend_error)
        if item.backend_error is not None
        else item.backend or "-"
    )
    return KubernetesResourceRow(
        ref=_ref(KubernetesResourceKind.PERSISTENT_VOLUME_CLAIM, item.name),
        values=(
            item.name,
            item.phase,
            item.volume_name or "-",
            item.capacity or "-",
            item.storage_class or "-",
            backend,
            mounted_by or "-",
            mount_paths or "-",
        ),
        healthy=item.phase == "Bound",
    )


def _mount_target_label(mount: PersistentVolumeMountSummary) -> str:
    return f"{mount.pod_name}/{mount.container_name}"


def _mount_path_label(mount: PersistentVolumeMountSummary) -> str:
    access = "ro" if mount.read_only else "rw"
    return f"{mount.mount_path} ({access})"


def _unavailable_cell(error: str) -> str:
    message = " ".join(error.split())
    return f"Unavailable: {message[:120]}"


def _require_pvc(resource: KubernetesResourceRef) -> None:
    if resource.kind is not KubernetesResourceKind.PERSISTENT_VOLUME_CLAIM:
        raise ValueError("目录浏览仅支持 PersistentVolumeClaim")
