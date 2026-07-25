from datetime import UTC, datetime
from types import SimpleNamespace

from ops_agent.kubernetes import (
    ContainerSummary,
    DeploymentSummary,
    KubernetesEventSummary,
    KubernetesReader,
    PodDetails,
    ServicePortSummary,
    ServiceSummary,
)


class FakeCoreV1Api:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def read_namespaced_pod(self, **kwargs):
        self.calls.append(("read_namespaced_pod", kwargs))
        return SimpleNamespace(
            metadata=SimpleNamespace(name="sample-api"),
            spec=SimpleNamespace(
                node_name="worker-1",
                containers=[
                    SimpleNamespace(
                        name="api",
                        image="registry/sample-api:v1",
                    )
                ],
            ),
            status=SimpleNamespace(
                phase="Running",
                pod_ip="10.42.0.8",
                container_statuses=[
                    SimpleNamespace(
                        name="api",
                        ready=True,
                        restart_count=2,
                        state=SimpleNamespace(
                            running=SimpleNamespace(),
                            waiting=None,
                            terminated=None,
                        ),
                    )
                ],
            ),
        )

    def read_namespaced_pod_log(self, **kwargs):
        self.calls.append(("read_namespaced_pod_log", kwargs))
        return "2026-07-26T10:00:00Z server started"

    def list_namespaced_event(self, **kwargs):
        self.calls.append(("list_namespaced_event", kwargs))
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    type="Warning",
                    reason="BackOff",
                    message="Back-off restarting failed container",
                    involved_object=SimpleNamespace(
                        kind="Pod",
                        name="sample-api",
                    ),
                    count=3,
                    event_time=None,
                    last_timestamp=datetime(
                        2026,
                        7,
                        26,
                        10,
                        5,
                        tzinfo=UTC,
                    ),
                    metadata=SimpleNamespace(
                        creation_timestamp=None,
                    ),
                )
            ]
        )

    def list_namespaced_service(self, **kwargs):
        self.calls.append(("list_namespaced_service", kwargs))
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    metadata=SimpleNamespace(name="sample-api"),
                    spec=SimpleNamespace(
                        type="ClusterIP",
                        cluster_ip="10.43.0.10",
                        ports=[
                            SimpleNamespace(
                                name="http",
                                port=80,
                                protocol="TCP",
                                target_port=8080,
                            )
                        ],
                    ),
                )
            ]
        )


class FakeAppsV1Api:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list_namespaced_deployment(self, **kwargs):
        self.calls.append(("list_namespaced_deployment", kwargs))
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    metadata=SimpleNamespace(name="sample-api"),
                    spec=SimpleNamespace(replicas=3),
                    status=SimpleNamespace(
                        ready_replicas=2,
                        available_replicas=2,
                        updated_replicas=3,
                    ),
                )
            ]
        )


def create_reader() -> tuple[
    KubernetesReader,
    FakeCoreV1Api,
    FakeAppsV1Api,
]:
    core_api = FakeCoreV1Api()
    apps_api = FakeAppsV1Api()
    reader = KubernetesReader(
        core_api=core_api,
        apps_api=apps_api,
        request_timeout_seconds=7,
    )
    return reader, core_api, apps_api


def test_get_pod_details_returns_container_status() -> None:
    reader, core_api, _ = create_reader()

    details = reader.get_pod_details("sample", "sample-api")

    assert details == PodDetails(
        name="sample-api",
        phase="Running",
        pod_ip="10.42.0.8",
        node_name="worker-1",
        containers=[
            ContainerSummary(
                name="api",
                image="registry/sample-api:v1",
                ready=True,
                restart_count=2,
                state="running",
            )
        ],
    )
    assert core_api.calls == [
        (
            "read_namespaced_pod",
            {
                "name": "sample-api",
                "namespace": "sample",
                "_request_timeout": 7,
            },
        )
    ]


def test_get_pod_logs_applies_container_and_tail_limit() -> None:
    reader, core_api, _ = create_reader()

    logs = reader.get_pod_logs(
        "sample",
        "sample-api",
        container="api",
        tail_lines=200,
    )

    assert logs == "2026-07-26T10:00:00Z server started"
    assert core_api.calls == [
        (
            "read_namespaced_pod_log",
            {
                "name": "sample-api",
                "namespace": "sample",
                "container": "api",
                "tail_lines": 200,
                "timestamps": True,
                "_request_timeout": 7,
            },
        )
    ]


def test_list_events_can_filter_by_pod() -> None:
    reader, core_api, _ = create_reader()

    events = reader.list_events(
        "sample",
        pod_name="sample-api",
        limit=100,
    )

    assert events == [
        KubernetesEventSummary(
            type="Warning",
            reason="BackOff",
            message="Back-off restarting failed container",
            object_kind="Pod",
            object_name="sample-api",
            count=3,
            last_seen="2026-07-26T10:05:00+00:00",
        )
    ]
    assert core_api.calls[0][1]["field_selector"] == (
        "involvedObject.kind=Pod,involvedObject.name=sample-api"
    )


def test_list_deployments_returns_replica_status() -> None:
    reader, _, apps_api = create_reader()

    deployments = reader.list_deployments("sample")

    assert deployments == [
        DeploymentSummary(
            name="sample-api",
            desired_replicas=3,
            ready_replicas=2,
            available_replicas=2,
            updated_replicas=3,
        )
    ]
    assert apps_api.calls[0][1]["namespace"] == "sample"


def test_list_services_returns_ports() -> None:
    reader, core_api, _ = create_reader()

    services = reader.list_services("sample")

    assert services == [
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
    assert core_api.calls[0][1]["namespace"] == "sample"
