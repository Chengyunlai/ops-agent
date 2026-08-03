from datetime import UTC, datetime
from threading import Event

from ops_agent.diagnostics import FindingCode
from ops_agent.kubernetes import (
    ContainerResourceSummary,
    ContainerSummary,
    ControllerReferenceSummary,
    CronJobSummary,
    DaemonSetSummary,
    DeploymentSummary,
    IngressSummary,
    JobSummary,
    KubernetesChangeSignal,
    KubernetesWatchOutcome,
    KubernetesWatchResult,
    PersistentVolumeClaimSummary,
    PersistentVolumeMountSummary,
    PodConditionSummary,
    PodSummary,
    ReplicaSetSummary,
    ServiceEndpointSource,
    ServiceEndpointSummary,
    ServiceEndpointTargetSummary,
    ServicePortSummary,
    ServiceSummary,
    StatefulSetSummary,
    VolumeDirectory,
    VolumeEntry,
    VolumeEntryKind,
    VolumeFilePreview,
)
from ops_agent.monitoring import (
    KubernetesMonitor,
    KubernetesResourceKind,
    KubernetesResourceRef,
)


class FakeKubernetesSource:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []
        self.containers = ["api"]
        self.failing_containers: set[str] = set()

    def list_pods(self, namespace: str) -> list[PodSummary]:
        self.calls.append(("pods", namespace))
        return [
            PodSummary(
                name="sample-api",
                phase="Running",
                restart_count=1,
                ready_containers=2,
                total_containers=2,
                created_at=datetime(2026, 7, 27, 6, 15, tzinfo=UTC),
            )
        ]

    def list_deployments(self, namespace: str) -> list[DeploymentSummary]:
        self.calls.append(("deployments", namespace))
        return [
            DeploymentSummary(
                name="sample-api",
                desired_replicas=2,
                ready_replicas=2,
                available_replicas=2,
                updated_replicas=2,
            )
        ]

    def list_stateful_sets(self, namespace: str) -> list[StatefulSetSummary]:
        self.calls.append(("stateful_sets", namespace))
        return [
            StatefulSetSummary(
                name="mysql",
                desired_replicas=1,
                ready_replicas=1,
                current_replicas=1,
                updated_replicas=1,
            )
        ]

    def list_daemon_sets(self, namespace: str) -> list[DaemonSetSummary]:
        self.calls.append(("daemon_sets", namespace))
        return [
            DaemonSetSummary(
                name="log-agent",
                desired_scheduled=2,
                current_scheduled=2,
                ready_scheduled=2,
                available_scheduled=2,
            )
        ]

    def list_replica_sets(self, namespace: str) -> list[ReplicaSetSummary]:
        self.calls.append(("replica_sets", namespace))
        return [
            ReplicaSetSummary(
                name="sample-api-7f8",
                desired_replicas=2,
                current_replicas=2,
                ready_replicas=2,
            )
        ]

    def list_services(self, namespace: str) -> list[ServiceSummary]:
        self.calls.append(("services", namespace))
        return [
            ServiceSummary(
                name="sample-api",
                type="ClusterIP",
                cluster_ip="10.43.0.10",
                ports=[
                    ServicePortSummary(
                        name="http",
                        port=80,
                        protocol="TCP",
                        target_port="8080",
                    )
                ],
            )
        ]

    def list_service_endpoints(
        self,
        namespace: str,
    ) -> list[ServiceEndpointSummary]:
        self.calls.append(("service_endpoints", namespace))
        return [
            ServiceEndpointSummary(
                service_name="sample-api",
                ready_addresses=2,
                not_ready_addresses=0,
                endpoint_slice_count=1,
            )
        ]

    def list_jobs(self, namespace: str) -> list[JobSummary]:
        self.calls.append(("jobs", namespace))
        return []

    def list_cron_jobs(self, namespace: str) -> list[CronJobSummary]:
        self.calls.append(("cron_jobs", namespace))
        return []

    def list_ingresses(self, namespace: str) -> list[IngressSummary]:
        self.calls.append(("ingresses", namespace))
        return []

    def list_persistent_volume_claims(
        self,
        namespace: str,
    ) -> list[PersistentVolumeClaimSummary]:
        self.calls.append(("persistent_volume_claims", namespace))
        return [
            PersistentVolumeClaimSummary(
                name="mysql-data",
                phase="Bound",
                volume_name="pvc-123",
                capacity="10Gi",
                access_modes=("ReadWriteOnce",),
                storage_class="fast",
                backend="CSI/disk.example.com",
                reclaim_policy="Retain",
                mounts=(
                    PersistentVolumeMountSummary(
                        claim_name="mysql-data",
                        pod_name="mysql-0",
                        pod_phase="Running",
                        container_name="mysql",
                        mount_path="/var/lib/mysql",
                        read_only=False,
                    ),
                ),
            )
        ]

    def describe_resource(
        self,
        namespace: str,
        kind: KubernetesResourceKind,
        name: str,
    ) -> str:
        self.calls.append((f"describe:{kind}", f"{namespace}/{name}"))
        return "kind: Pod\nmetadata:\n  name: sample-api"

    def get_pod_details(self, namespace: str, pod_name: str):
        self.calls.append(("pod_details", f"{namespace}/{pod_name}"))
        return type(
            "PodDetails",
            (),
            {
                "containers": [
                    ContainerSummary(
                        name=name,
                        image="registry/sample-api:v1",
                        ready=True,
                        restart_count=0,
                        state="running",
                    )
                    for name in self.containers
                ]
            },
        )()

    def get_pod_logs(
        self,
        namespace: str,
        pod_name: str,
        *,
        container: str | None,
        tail_lines: int,
    ) -> str:
        self.calls.append(
            ("pod_logs", f"{namespace}/{pod_name}/{container}/{tail_lines}")
        )
        if container in self.failing_containers:
            raise RuntimeError(f"{container} logs forbidden")
        return f"{container} server started"

    def browse_persistent_volume_claim(
        self,
        namespace: str,
        claim_name: str,
        *,
        path: str,
    ) -> VolumeDirectory:
        self.calls.append(("browse_pvc", f"{namespace}/{claim_name}/{path}"))
        target = self.list_persistent_volume_claims(namespace)[0].mounts[0]
        return VolumeDirectory(
            claim_name=claim_name,
            path=path,
            target=target,
            entries=(
                VolumeEntry(
                    name="backups",
                    kind=VolumeEntryKind.DIRECTORY,
                    size_bytes=None,
                ),
            ),
        )

    def preview_persistent_volume_claim_file(
        self,
        namespace: str,
        claim_name: str,
        *,
        path: str,
        max_bytes: int,
    ) -> VolumeFilePreview:
        self.calls.append(("preview_pvc", f"{namespace}/{claim_name}/{path}"))
        target = self.list_persistent_volume_claims(namespace)[0].mounts[0]
        return VolumeFilePreview(
            claim_name=claim_name,
            path=path,
            target=target,
            content="backup complete",
            truncated=False,
        )


def test_monitor_captures_fixed_namespace_snapshot() -> None:
    source = FakeKubernetesSource()
    monitor = KubernetesMonitor(
        source,
        namespace="sample",
        clock=lambda: datetime(2026, 7, 29, 10, 30, tzinfo=UTC),
    )

    snapshot = monitor.snapshot()

    assert snapshot.namespace == "sample"
    assert snapshot.collection(KubernetesResourceKind.POD).rows[0].values == (
        "sample-api",
        "2/2",
        "Running",
        "1",
        "2d",
    )
    assert snapshot.collection(KubernetesResourceKind.POD).columns == (
        "NAME",
        "READY",
        "STATUS",
        "RESTARTS",
        "AGE",
    )
    assert snapshot.collection(KubernetesResourceKind.DEPLOYMENT).rows[0].values[1] == (
        "2/2"
    )
    assert snapshot.collection(KubernetesResourceKind.STATEFUL_SET).rows[
        0
    ].ref.name == ("mysql")
    assert snapshot.collection(KubernetesResourceKind.DAEMON_SET).rows[0].healthy
    assert snapshot.collection(KubernetesResourceKind.REPLICA_SET).rows[0].ref.name == (
        "sample-api-7f8"
    )
    service_collection = snapshot.collection(KubernetesResourceKind.SERVICE)
    assert service_collection.rows[0].values[2] == "10.43.0.10"
    assert service_collection.rows[0].healthy is None
    storage_collection = snapshot.collection(
        KubernetesResourceKind.PERSISTENT_VOLUME_CLAIM
    )
    assert storage_collection.shortcut == "7"
    assert storage_collection.rows[0].values == (
        "mysql-data",
        "Bound",
        "pvc-123",
        "10Gi",
        "fast",
        "CSI/disk.example.com",
        "mysql-0/mysql",
        "/var/lib/mysql (rw)",
    )
    assert snapshot.observed_at.tzinfo is not None
    assert source.calls == [
        ("pods", "sample"),
        ("deployments", "sample"),
        ("services", "sample"),
        ("replica_sets", "sample"),
        ("service_endpoints", "sample"),
        ("stateful_sets", "sample"),
        ("daemon_sets", "sample"),
        ("jobs", "sample"),
        ("cron_jobs", "sample"),
        ("ingresses", "sample"),
        ("persistent_volume_claims", "sample"),
    ]


def test_monitor_waits_for_change_without_exposing_namespace() -> None:
    class WatchableSource(FakeKubernetesSource):
        def wait_for_change(
            self,
            namespace: str,
            *,
            timeout_seconds: int,
            stop_event: Event | None = None,
        ) -> KubernetesWatchResult:
            self.calls.append(("watch", f"{namespace}/{timeout_seconds}"))
            assert stop_event is expected_stop
            return expected_result

        def stop_waiting_for_change(self) -> None:
            self.calls.append(("stop_watch", "sample"))

    expected_stop = Event()
    expected_result = KubernetesWatchResult(
        outcome=KubernetesWatchOutcome.CHANGED,
        change=KubernetesChangeSignal(
            resource_kind=KubernetesResourceKind.POD,
            event_type="MODIFIED",
            resource_name="sample-api",
        ),
    )
    source = WatchableSource()
    monitor = KubernetesMonitor(source, namespace="sample")

    result = monitor.wait_for_change(
        timeout_seconds=5,
        stop_event=expected_stop,
    )
    monitor.stop_waiting_for_change()

    assert result == expected_result
    assert source.calls == [
        ("watch", "sample/5"),
        ("stop_watch", "sample"),
    ]


def test_monitor_lists_selected_pod_containers_for_manual_actions() -> None:
    source = FakeKubernetesSource()
    source.containers = ["api", "sidecar"]
    monitor = KubernetesMonitor(source, namespace="sample")

    containers = monitor.pod_containers(
        KubernetesResourceRef(
            kind=KubernetesResourceKind.POD,
            name="sample-api",
        )
    )

    assert containers == ("api", "sidecar")
    assert source.calls == [("pod_details", "sample/sample-api")]


def test_monitor_browses_and_previews_pvc_without_exposing_namespace() -> None:
    source = FakeKubernetesSource()
    monitor = KubernetesMonitor(source, namespace="sample")
    resource = KubernetesResourceRef(
        kind=KubernetesResourceKind.PERSISTENT_VOLUME_CLAIM,
        name="mysql-data",
    )

    directory = monitor.browse_pvc(resource, path="backups")
    preview = monitor.preview_pvc_file(
        resource,
        path="backups/latest.txt",
    )

    assert directory.entries[0].name == "backups"
    assert preview.title == "PVC/mysql-data · backups/latest.txt"
    assert "backup complete" in preview.content
    assert ("browse_pvc", "sample/mysql-data/backups") in source.calls
    assert ("preview_pvc", "sample/mysql-data/backups/latest.txt") in source.calls


def test_monitor_exposes_storage_permission_errors_in_topology_rows() -> None:
    class ForbiddenStorageSource(FakeKubernetesSource):
        def list_persistent_volume_claims(
            self,
            namespace: str,
        ) -> list[PersistentVolumeClaimSummary]:
            self.calls.append(("persistent_volume_claims", namespace))
            return [
                PersistentVolumeClaimSummary(
                    name="mysql-data",
                    phase="Bound",
                    volume_name="pvc-123",
                    capacity="10Gi",
                    access_modes=("ReadWriteOnce",),
                    storage_class="fast",
                    backend="Unavailable",
                    backend_error="persistentvolumes is forbidden",
                    mounts_error="pods is forbidden",
                )
            ]

    snapshot = KubernetesMonitor(
        ForbiddenStorageSource(),
        namespace="sample",
    ).snapshot()
    row = snapshot.collection(KubernetesResourceKind.PERSISTENT_VOLUME_CLAIM).rows[0]

    assert row.values[5] == "Unavailable: persistentvolumes is forbidden"
    assert row.values[6] == "Unavailable: pods is forbidden"
    assert row.values[7] == "-"


def test_monitor_reads_selected_resource_without_exposing_namespace() -> None:
    source = FakeKubernetesSource()
    monitor = KubernetesMonitor(source, namespace="sample")

    resource = KubernetesResourceRef(
        kind=KubernetesResourceKind.POD,
        name="sample-api",
    )
    description = monitor.describe(resource)
    logs = monitor.pod_logs(resource, tail_lines=200)

    assert description.title == "Describe · Pod/sample-api"
    assert "metadata:" in description.content
    assert logs.title == "Logs · Pod/sample-api · api · last 200 lines/container"
    assert logs.content == "api server started"
    assert source.calls == [
        ("describe:Pod", "sample/sample-api"),
        ("pod_details", "sample/sample-api"),
        ("pod_logs", "sample/sample-api/api/200"),
    ]


def test_monitor_keeps_other_resource_types_when_one_query_fails() -> None:
    class PartiallyForbiddenSource(FakeKubernetesSource):
        def list_replica_sets(self, namespace: str) -> list[ReplicaSetSummary]:
            self.calls.append(("replica_sets", namespace))
            raise RuntimeError("replicasets is forbidden")

    monitor = KubernetesMonitor(PartiallyForbiddenSource(), namespace="sample")

    snapshot = monitor.snapshot()

    replicas = snapshot.collection(KubernetesResourceKind.REPLICA_SET)
    services = snapshot.collection(KubernetesResourceKind.SERVICE)
    assert replicas.error == "replicasets is forbidden"
    assert replicas.rows == ()
    assert services.error is None
    assert services.rows[0].ref.name == "sample-api"


def test_monitor_combines_logs_from_every_pod_container() -> None:
    source = FakeKubernetesSource()
    source.containers = ["api", "sidecar"]
    monitor = KubernetesMonitor(source, namespace="sample")
    resource = KubernetesResourceRef(
        kind=KubernetesResourceKind.POD,
        name="sample-api",
    )

    logs = monitor.pod_logs(resource, tail_lines=50)

    assert logs.title == (
        "Logs · Pod/sample-api · all 2 containers · last 50 lines/container"
    )
    assert logs.content == (
        "===== container: api =====\napi server started\n\n"
        "===== container: sidecar =====\nsidecar server started"
    )
    assert source.calls == [
        ("pod_details", "sample/sample-api"),
        ("pod_logs", "sample/sample-api/api/50"),
        ("pod_logs", "sample/sample-api/sidecar/50"),
    ]


def test_monitor_keeps_other_container_logs_when_one_container_fails() -> None:
    source = FakeKubernetesSource()
    source.containers = ["api", "sidecar"]
    source.failing_containers = {"sidecar"}
    monitor = KubernetesMonitor(source, namespace="sample")

    logs = monitor.pod_logs(
        KubernetesResourceRef(
            kind=KubernetesResourceKind.POD,
            name="sample-api",
        )
    )

    assert "api server started" in logs.content
    assert "===== container: sidecar =====\n[读取失败] sidecar logs forbidden" in (
        logs.content
    )


def test_monitor_exposes_deterministic_findings_as_resource_health_reasons() -> None:
    class UnhealthySource(FakeKubernetesSource):
        def list_pods(self, namespace: str) -> list[PodSummary]:
            self.calls.append(("pods", namespace))
            return [
                PodSummary(
                    name="sample-api-7f8-x1",
                    phase="Pending",
                    restart_count=0,
                    ready_containers=0,
                    total_containers=1,
                    controller=ControllerReferenceSummary(
                        kind="ReplicaSet",
                        name="sample-api-7f8",
                    ),
                    qos_class="Burstable",
                    resources=(
                        ContainerResourceSummary(
                            name="api",
                            cpu_request="2",
                            memory_request="1Gi",
                        ),
                    ),
                    conditions=(
                        PodConditionSummary(
                            type="PodScheduled",
                            status="False",
                            reason="Unschedulable",
                            message="0/3 nodes are available: Insufficient cpu",
                        ),
                    ),
                )
            ]

        def list_deployments(self, namespace: str) -> list[DeploymentSummary]:
            self.calls.append(("deployments", namespace))
            return [
                DeploymentSummary(
                    name="sample-api",
                    desired_replicas=1,
                    ready_replicas=0,
                    available_replicas=0,
                    updated_replicas=1,
                    generation=7,
                    observed_generation=7,
                    revision="3",
                )
            ]

        def list_replica_sets(self, namespace: str) -> list[ReplicaSetSummary]:
            self.calls.append(("replica_sets", namespace))
            return [
                ReplicaSetSummary(
                    name="sample-api-7f8",
                    desired_replicas=1,
                    current_replicas=1,
                    ready_replicas=0,
                    revision="3",
                    controller=ControllerReferenceSummary(
                        kind="Deployment",
                        name="sample-api",
                    ),
                )
            ]

        def list_service_endpoints(
            self,
            namespace: str,
        ) -> list[ServiceEndpointSummary]:
            self.calls.append(("service_endpoints", namespace))
            return [
                ServiceEndpointSummary(
                    service_name="sample-api",
                    ready_addresses=0,
                    not_ready_addresses=1,
                    endpoint_slice_count=1,
                )
            ]

    monitor = KubernetesMonitor(UnhealthySource(), namespace="sample")

    snapshot = monitor.snapshot()

    pod = snapshot.collection(KubernetesResourceKind.POD).rows[0]
    deployment = snapshot.collection(KubernetesResourceKind.DEPLOYMENT).rows[0]
    service = snapshot.collection(KubernetesResourceKind.SERVICE).rows[0]
    assert pod.health_reasons == (
        "Pod 未处于 Running 状态",
        "Pod 容器未全部就绪",
        "Pod 因资源不足无法调度",
    )
    assert deployment.health_reasons == (
        "Deployment 就绪副本少于期望副本",
        "Pod/sample-api-7f8-x1: Pod 未处于 Running 状态",
        "Pod/sample-api-7f8-x1: Pod 容器未全部就绪",
        "Pod/sample-api-7f8-x1: Pod 因资源不足无法调度",
    )
    assert service.health_reasons == ("Service 没有 Ready Endpoint",)
    assert snapshot.finding_count == 5

    details = monitor.diagnostics(
        KubernetesResourceRef(
            kind=KubernetesResourceKind.DEPLOYMENT,
            name="sample-api",
        )
    )
    assert details.title == "Health · Deployment/sample-api"
    assert "Generation: 7 · Observed: 7 · Revision: 3" in details.content
    assert "ReplicaSet/sample-api-7f8 · desired 1 · ready 0 · revision 3" in (
        details.content
    )
    assert "Pod/sample-api-7f8-x1 · owner sample-api-7f8 · phase Pending" in (
        details.content
    )
    assert "! [pod_resource_unschedulable] Pod 因资源不足无法调度" in details.content
    assert "requests(cpu=2, memory=1Gi" in details.content
    assert (
        next(
            diagnostic
            for diagnostic in snapshot.diagnostics
            if diagnostic.summary == "Pod 因资源不足无法调度"
        ).code
        is FindingCode.POD_RESOURCE_UNSCHEDULABLE
    )


def test_monitor_marks_endpoint_diagnostics_partial_without_guessing_no_backends() -> (
    None
):
    class ForbiddenEndpointSource(FakeKubernetesSource):
        def list_service_endpoints(
            self,
            namespace: str,
        ) -> list[ServiceEndpointSummary]:
            self.calls.append(("service_endpoints", namespace))
            raise RuntimeError("endpointslices is forbidden")

    snapshot = KubernetesMonitor(
        ForbiddenEndpointSource(),
        namespace="sample",
    ).snapshot()

    service = snapshot.collection(KubernetesResourceKind.SERVICE).rows[0]
    assert service.health_reasons == ()
    assert service.healthy is None
    assert snapshot.finding_count == 0
    assert snapshot.diagnostic_errors == (
        "Service Endpoint 诊断不可用：endpointslices is forbidden",
    )


def test_monitor_shows_healthy_service_endpoint_to_pod_topology() -> None:
    class HealthyTopologySource(FakeKubernetesSource):
        def list_service_endpoints(
            self,
            namespace: str,
        ) -> list[ServiceEndpointSummary]:
            self.calls.append(("service_endpoints", namespace))
            return [
                ServiceEndpointSummary(
                    service_name="sample-api",
                    ready_addresses=1,
                    not_ready_addresses=0,
                    endpoint_slice_count=1,
                    source=ServiceEndpointSource.ENDPOINT_SLICE,
                    targets=(
                        ServiceEndpointTargetSummary(
                            address="10.42.0.8",
                            ready=True,
                            target_kind="Pod",
                            target_name="sample-api-7f8-x1",
                        ),
                    ),
                )
            ]

    monitor = KubernetesMonitor(HealthyTopologySource(), namespace="sample")
    monitor.snapshot()

    details = monitor.diagnostics(
        KubernetesResourceRef(
            kind=KubernetesResourceKind.SERVICE,
            name="sample-api",
        )
    )

    assert "Source: EndpointSlice · Ready: 1 · NotReady: 0" in details.content
    assert "10.42.0.8 -> Pod/sample-api-7f8-x1 · ready" in details.content
