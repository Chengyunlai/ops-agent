from ops_agent.kubernetes import (
    ContainerSummary,
    CronJobSummary,
    DaemonSetSummary,
    DeploymentSummary,
    IngressSummary,
    JobSummary,
    PersistentVolumeClaimSummary,
    PodSummary,
    ReplicaSetSummary,
    ServicePortSummary,
    ServiceSummary,
    StatefulSetSummary,
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
        return []

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


def test_monitor_captures_fixed_namespace_snapshot() -> None:
    source = FakeKubernetesSource()
    monitor = KubernetesMonitor(source, namespace="sample")

    snapshot = monitor.snapshot()

    assert snapshot.namespace == "sample"
    assert snapshot.collection(KubernetesResourceKind.POD).rows[0].values == (
        "sample-api",
        "2/2",
        "Running",
        "1",
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
    assert snapshot.observed_at.tzinfo is not None
    assert source.calls == [
        ("pods", "sample"),
        ("deployments", "sample"),
        ("stateful_sets", "sample"),
        ("daemon_sets", "sample"),
        ("services", "sample"),
        ("replica_sets", "sample"),
        ("jobs", "sample"),
        ("cron_jobs", "sample"),
        ("ingresses", "sample"),
        ("persistent_volume_claims", "sample"),
    ]


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
