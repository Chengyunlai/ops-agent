import subprocess
from datetime import UTC, datetime
from pathlib import Path
from threading import Event
from types import SimpleNamespace

from kubernetes.client.exceptions import ApiException
from ops_agent.kubernetes import (
    ContainerSummary,
    ControllerReferenceSummary,
    CronJobSummary,
    DaemonSetSummary,
    DeploymentConditionSummary,
    DeploymentSummary,
    IngressSummary,
    JobSummary,
    KubernetesEventSummary,
    KubernetesReader,
    KubernetesResourceKind,
    PersistentVolumeClaimSummary,
    PersistentVolumeMountSummary,
    PodDetails,
    ReplicaSetSummary,
    ServiceEndpointSummary,
    ServiceEndpointTargetSummary,
    ServicePortSummary,
    ServiceSummary,
    StatefulSetSummary,
)


class FakeCoreV1Api:
    def __init__(self, *, mount_path: str = "/var/lib/mysql") -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []
        self.mount_path = mount_path

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
        return SimpleNamespace(
            data=(
                b"2026-07-26T10:00:00Z server started\n"
                b"2026-07-26T10:00:01Z request completed\n"
            )
        )

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

    def read_namespaced_service(self, **kwargs):
        self.calls.append(("read_namespaced_service", kwargs))
        return SimpleNamespace(
            api_version="v1",
            kind="Service",
            metadata=SimpleNamespace(name=kwargs["name"]),
        )

    def list_namespaced_persistent_volume_claim(self, **kwargs):
        self.calls.append(("list_namespaced_persistent_volume_claim", kwargs))
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    metadata=SimpleNamespace(name="mysql-data"),
                    spec=SimpleNamespace(
                        volume_name="pvc-123",
                        access_modes=["ReadWriteOnce"],
                        storage_class_name="local-path",
                    ),
                    status=SimpleNamespace(
                        phase="Bound",
                        capacity={"storage": "10Gi"},
                        access_modes=["ReadWriteOnce"],
                    ),
                )
            ]
        )

    def list_namespaced_pod(self, **kwargs):
        self.calls.append(("list_namespaced_pod", kwargs))
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    metadata=SimpleNamespace(name="mysql-0"),
                    spec=SimpleNamespace(
                        volumes=[
                            SimpleNamespace(
                                name="data",
                                persistent_volume_claim=SimpleNamespace(
                                    claim_name="mysql-data"
                                ),
                            )
                        ],
                        containers=[
                            SimpleNamespace(
                                name="mysql",
                                volume_mounts=[
                                    SimpleNamespace(
                                        name="data",
                                        mount_path=self.mount_path,
                                        read_only=False,
                                    )
                                ],
                            )
                        ],
                        init_containers=[
                            SimpleNamespace(
                                name="ensure-dir-ownership",
                                volume_mounts=[
                                    SimpleNamespace(
                                        name="data",
                                        mount_path=self.mount_path,
                                        read_only=False,
                                    )
                                ],
                            )
                        ],
                    ),
                    status=SimpleNamespace(
                        phase="Running",
                        container_statuses=[
                            SimpleNamespace(
                                name="mysql",
                                state=SimpleNamespace(running=SimpleNamespace()),
                            )
                        ],
                    ),
                )
            ]
        )

    def read_persistent_volume(self, **kwargs):
        self.calls.append(("read_persistent_volume", kwargs))
        return SimpleNamespace(
            spec=SimpleNamespace(
                csi=SimpleNamespace(
                    driver="disk.csi.example.com",
                    volume_handle="disk-123",
                ),
                nfs=None,
                local=None,
                host_path=None,
                persistent_volume_reclaim_policy="Retain",
            )
        )


class FakeAppsV1Api:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list_namespaced_deployment(self, **kwargs):
        self.calls.append(("list_namespaced_deployment", kwargs))
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    metadata=SimpleNamespace(
                        name="sample-api",
                        generation=7,
                        annotations={"deployment.kubernetes.io/revision": "4"},
                    ),
                    spec=SimpleNamespace(replicas=3),
                    status=SimpleNamespace(
                        ready_replicas=2,
                        available_replicas=2,
                        updated_replicas=3,
                        observed_generation=7,
                        conditions=[
                            SimpleNamespace(
                                type="Progressing",
                                status="True",
                                reason="NewReplicaSetAvailable",
                                message="ReplicaSet rollout completed",
                            )
                        ],
                    ),
                )
            ]
        )

    def list_namespaced_stateful_set(self, **kwargs):
        self.calls.append(("list_namespaced_stateful_set", kwargs))
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    metadata=SimpleNamespace(name="mysql"),
                    spec=SimpleNamespace(replicas=1),
                    status=SimpleNamespace(
                        ready_replicas=1,
                        current_replicas=1,
                        updated_replicas=1,
                    ),
                )
            ]
        )

    def list_namespaced_daemon_set(self, **kwargs):
        self.calls.append(("list_namespaced_daemon_set", kwargs))
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    metadata=SimpleNamespace(name="log-agent"),
                    status=SimpleNamespace(
                        desired_number_scheduled=3,
                        current_number_scheduled=3,
                        number_ready=2,
                        number_available=2,
                    ),
                )
            ]
        )

    def list_namespaced_replica_set(self, **kwargs):
        self.calls.append(("list_namespaced_replica_set", kwargs))
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    metadata=SimpleNamespace(
                        name="sample-api-7f8",
                        annotations={"deployment.kubernetes.io/revision": "4"},
                        owner_references=[
                            SimpleNamespace(
                                kind="Deployment",
                                name="sample-api",
                                controller=True,
                            )
                        ],
                    ),
                    spec=SimpleNamespace(replicas=2),
                    status=SimpleNamespace(
                        replicas=2,
                        ready_replicas=2,
                    ),
                )
            ]
        )

    def read_namespaced_deployment(self, **kwargs):
        self.calls.append(("read_namespaced_deployment", kwargs))
        return SimpleNamespace(
            api_version="apps/v1",
            kind="Deployment",
            metadata=SimpleNamespace(name=kwargs["name"]),
        )


class FakeBatchV1Api:
    def list_namespaced_job(self, **kwargs):
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    metadata=SimpleNamespace(name="database-migration"),
                    spec=SimpleNamespace(completions=1),
                    status=SimpleNamespace(
                        succeeded=1,
                        active=None,
                        failed=None,
                    ),
                )
            ]
        )

    def list_namespaced_cron_job(self, **kwargs):
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    metadata=SimpleNamespace(name="nightly-backup"),
                    spec=SimpleNamespace(
                        schedule="0 2 * * *",
                        suspend=False,
                    ),
                    status=SimpleNamespace(
                        active=[],
                        last_schedule_time=datetime(
                            2026,
                            7,
                            27,
                            2,
                            tzinfo=UTC,
                        ),
                    ),
                )
            ]
        )


class FakeNetworkingV1Api:
    def list_namespaced_ingress(self, **kwargs):
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    metadata=SimpleNamespace(name="sample"),
                    spec=SimpleNamespace(
                        ingress_class_name="nginx",
                        rules=[SimpleNamespace(host="sample.example.com")],
                    ),
                    status=SimpleNamespace(
                        load_balancer=SimpleNamespace(
                            ingress=[SimpleNamespace(ip="10.0.0.8", hostname=None)]
                        )
                    ),
                )
            ]
        )


class FakeDiscoveryV1Api:
    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, object]]] = []

    def list_namespaced_endpoint_slice(self, **kwargs):
        self.calls.append(("list_namespaced_endpoint_slice", kwargs))
        return SimpleNamespace(
            items=[
                SimpleNamespace(
                    metadata=SimpleNamespace(
                        labels={"kubernetes.io/service-name": "sample-api"}
                    ),
                    endpoints=[
                        SimpleNamespace(
                            addresses=["10.42.0.8"],
                            conditions=SimpleNamespace(ready=True),
                            target_ref=SimpleNamespace(
                                kind="Pod",
                                name="sample-api-7f8-x1",
                            ),
                        ),
                        SimpleNamespace(
                            addresses=["10.42.0.9", "10.42.0.10"],
                            conditions=SimpleNamespace(ready=False),
                        ),
                    ],
                ),
                SimpleNamespace(
                    metadata=SimpleNamespace(
                        labels={"kubernetes.io/service-name": "sample-api"}
                    ),
                    endpoints=[
                        SimpleNamespace(
                            addresses=["10.42.0.11"],
                            conditions=SimpleNamespace(ready=None),
                        )
                    ],
                ),
                SimpleNamespace(
                    metadata=SimpleNamespace(
                        labels={"kubernetes.io/service-name": "another-api"}
                    ),
                    endpoints=[],
                ),
                SimpleNamespace(
                    metadata=SimpleNamespace(labels={}),
                    endpoints=[],
                ),
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


def test_get_pod_logs_applies_container_tail_and_previous_instance() -> None:
    reader, core_api, _ = create_reader()

    logs = reader.get_pod_logs(
        "sample",
        "sample-api",
        container="api",
        tail_lines=200,
        previous=True,
    )

    assert logs == (
        "2026-07-26T10:00:00Z server started\n2026-07-26T10:00:01Z request completed\n"
    )
    assert core_api.calls == [
        (
            "read_namespaced_pod_log",
            {
                "name": "sample-api",
                "namespace": "sample",
                "container": "api",
                "tail_lines": 200,
                "timestamps": True,
                "previous": True,
                "_request_timeout": 7,
                "_preload_content": False,
            },
        )
    ]


def test_get_pod_logs_can_select_a_bounded_time_range() -> None:
    reader, core_api, _ = create_reader()

    reader.get_pod_logs(
        "sample",
        "sample-api",
        container="api",
        tail_lines=None,
        since_seconds=15 * 60,
    )

    assert core_api.calls == [
        (
            "read_namespaced_pod_log",
            {
                "name": "sample-api",
                "namespace": "sample",
                "container": "api",
                "since_seconds": 900,
                "timestamps": True,
                "previous": False,
                "_request_timeout": 7,
                "_preload_content": False,
            },
        )
    ]


def test_follow_pod_logs_streams_complete_lines_and_closes_response() -> None:
    class LogStreamResponse:
        def __init__(self) -> None:
            self.closed = False

        def stream(self, *, amt: int, decode_content: bool):
            assert amt == 64 * 1024
            assert decode_content is True
            yield b"2026-08-04T03:50:00Z INFO first"
            yield b" record\n2026-08-04T03:50:01Z ERROR failed\npartial"
            yield b" final\n"

        def close(self) -> None:
            self.closed = True

    class FollowCoreV1Api(FakeCoreV1Api):
        def __init__(self) -> None:
            super().__init__()
            self.response = LogStreamResponse()

        def read_namespaced_pod_log(self, **kwargs):
            self.calls.append(("read_namespaced_pod_log", kwargs))
            return self.response

    core_api = FollowCoreV1Api()
    reader = KubernetesReader(
        core_api=core_api,
        apps_api=FakeAppsV1Api(),
        request_timeout_seconds=7,
    )
    since_time = datetime(2026, 8, 4, 3, 49, 59, tzinfo=UTC)

    lines = list(
        reader.follow_pod_logs(
            "sample",
            "sample-api",
            container="api",
            since_time=since_time,
            stop_event=Event(),
        )
    )

    assert lines == [
        "2026-08-04T03:50:00Z INFO first record",
        "2026-08-04T03:50:01Z ERROR failed",
        "partial final",
    ]
    assert core_api.calls == [
        (
            "read_namespaced_pod_log",
            {
                "name": "sample-api",
                "namespace": "sample",
                "container": "api",
                "follow": True,
                "since_time": since_time,
                "timestamps": True,
                "_request_timeout": (7, None),
                "_preload_content": False,
            },
        )
    ]
    assert core_api.response.closed is True


def test_stop_following_pod_logs_closes_the_active_stream() -> None:
    class ActiveLogStream:
        def __init__(self) -> None:
            self.closed = False

        def stream(self, *, amt: int, decode_content: bool):
            yield b"2026-08-04T03:50:00Z INFO first record\n"
            if not self.closed:
                yield b"2026-08-04T03:50:01Z INFO second record\n"

        def close(self) -> None:
            self.closed = True

    class FollowCoreV1Api(FakeCoreV1Api):
        def __init__(self) -> None:
            super().__init__()
            self.response = ActiveLogStream()

        def read_namespaced_pod_log(self, **kwargs):
            self.calls.append(("read_namespaced_pod_log", kwargs))
            return self.response

    core_api = FollowCoreV1Api()
    reader = KubernetesReader(
        core_api=core_api,
        apps_api=FakeAppsV1Api(),
        request_timeout_seconds=7,
    )
    stream = reader.follow_pod_logs(
        "sample",
        "sample-api",
        container="api",
        since_time=datetime(2026, 8, 4, 3, 49, 59, tzinfo=UTC),
        stop_event=Event(),
    )

    assert next(stream) == "2026-08-04T03:50:00Z INFO first record"
    reader.stop_following_pod_logs()

    assert core_api.response.closed is True
    stream.close()


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
            generation=7,
            observed_generation=7,
            revision="4",
            conditions=(
                DeploymentConditionSummary(
                    type="Progressing",
                    status="True",
                    reason="NewReplicaSetAvailable",
                    message="ReplicaSet rollout completed",
                ),
            ),
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


def test_list_service_endpoints_aggregates_endpoint_slices() -> None:
    discovery_api = FakeDiscoveryV1Api()
    reader = KubernetesReader(
        core_api=FakeCoreV1Api(),
        apps_api=FakeAppsV1Api(),
        discovery_api=discovery_api,
        request_timeout_seconds=7,
    )

    endpoints = reader.list_service_endpoints("sample")

    assert endpoints == [
        ServiceEndpointSummary(
            service_name="another-api",
            ready_addresses=0,
            not_ready_addresses=0,
            endpoint_slice_count=1,
        ),
        ServiceEndpointSummary(
            service_name="sample-api",
            ready_addresses=2,
            not_ready_addresses=2,
            endpoint_slice_count=2,
            source="EndpointSlice",
            targets=(
                ServiceEndpointTargetSummary(
                    address="10.42.0.8",
                    ready=True,
                    target_kind="Pod",
                    target_name="sample-api-7f8-x1",
                ),
                ServiceEndpointTargetSummary(
                    address="10.42.0.9",
                    ready=False,
                ),
                ServiceEndpointTargetSummary(
                    address="10.42.0.10",
                    ready=False,
                ),
                ServiceEndpointTargetSummary(
                    address="10.42.0.11",
                    ready=True,
                ),
            ),
        ),
    ]
    assert discovery_api.calls == [
        (
            "list_namespaced_endpoint_slice",
            {
                "namespace": "sample",
                "_request_timeout": 7,
            },
        )
    ]


def test_list_service_endpoints_preserves_api_permission_error() -> None:
    class ForbiddenDiscoveryV1Api(FakeDiscoveryV1Api):
        def list_namespaced_endpoint_slice(self, **kwargs):
            raise ApiException(status=403, reason="endpointslices is forbidden")

    reader = KubernetesReader(
        core_api=FakeCoreV1Api(),
        apps_api=FakeAppsV1Api(),
        discovery_api=ForbiddenDiscoveryV1Api(),
        request_timeout_seconds=7,
    )

    try:
        reader.list_service_endpoints("sample")
    except Exception as error:  # noqa: BLE001 - assert public error message
        assert "查询 namespace 'sample' 的 EndpointSlice 失败" in str(error)
        assert "endpointslices is forbidden" in str(error)
    else:
        raise AssertionError("EndpointSlice permission error should be preserved")


def test_list_service_endpoints_falls_back_when_discovery_api_is_unavailable() -> None:
    class UnavailableDiscoveryV1Api(FakeDiscoveryV1Api):
        def list_namespaced_endpoint_slice(self, **kwargs):
            raise ApiException(status=404, reason="the server could not find resource")

    class LegacyEndpointsCoreV1Api(FakeCoreV1Api):
        def list_namespaced_endpoints(self, **kwargs):
            self.calls.append(("list_namespaced_endpoints", kwargs))
            return SimpleNamespace(
                items=[
                    SimpleNamespace(
                        metadata=SimpleNamespace(name="sample-api"),
                        subsets=[
                            SimpleNamespace(
                                addresses=[SimpleNamespace(ip="10.42.0.8")],
                                not_ready_addresses=[SimpleNamespace(ip="10.42.0.9")],
                            ),
                            SimpleNamespace(
                                addresses=[SimpleNamespace(ip="10.42.0.10")],
                                not_ready_addresses=[],
                            ),
                        ],
                    )
                ]
            )

    core_api = LegacyEndpointsCoreV1Api()
    reader = KubernetesReader(
        core_api=core_api,
        apps_api=FakeAppsV1Api(),
        discovery_api=UnavailableDiscoveryV1Api(),
        request_timeout_seconds=7,
    )

    endpoints = reader.list_service_endpoints("sample")

    assert endpoints == [
        ServiceEndpointSummary(
            service_name="sample-api",
            ready_addresses=2,
            not_ready_addresses=1,
            endpoint_slice_count=0,
            source="Endpoints",
            targets=(
                ServiceEndpointTargetSummary(
                    address="10.42.0.8",
                    ready=True,
                ),
                ServiceEndpointTargetSummary(
                    address="10.42.0.9",
                    ready=False,
                ),
                ServiceEndpointTargetSummary(
                    address="10.42.0.10",
                    ready=True,
                ),
            ),
        )
    ]
    assert core_api.calls == [
        (
            "list_namespaced_endpoints",
            {"namespace": "sample", "_request_timeout": 7},
        )
    ]


def test_list_additional_workloads_returns_replica_status() -> None:
    reader, _, apps_api = create_reader()

    stateful_sets = reader.list_stateful_sets("sample")
    daemon_sets = reader.list_daemon_sets("sample")
    replica_sets = reader.list_replica_sets("sample")

    assert stateful_sets == [
        StatefulSetSummary(
            name="mysql",
            desired_replicas=1,
            ready_replicas=1,
            current_replicas=1,
            updated_replicas=1,
        )
    ]
    assert daemon_sets == [
        DaemonSetSummary(
            name="log-agent",
            desired_scheduled=3,
            current_scheduled=3,
            ready_scheduled=2,
            available_scheduled=2,
        )
    ]
    assert replica_sets == [
        ReplicaSetSummary(
            name="sample-api-7f8",
            desired_replicas=2,
            current_replicas=2,
            ready_replicas=2,
            revision="4",
            controller=ControllerReferenceSummary(
                kind="Deployment",
                name="sample-api",
            ),
        )
    ]
    assert [call[0] for call in apps_api.calls] == [
        "list_namespaced_stateful_set",
        "list_namespaced_daemon_set",
        "list_namespaced_replica_set",
    ]


def test_describe_resource_includes_object_and_related_events() -> None:
    reader, _, apps_api = create_reader()

    description = reader.describe_resource(
        "sample",
        KubernetesResourceKind.DEPLOYMENT,
        "sample-api",
    )

    assert "Name:       sample-api" in description
    assert "Namespace:  sample" in description
    assert "Kind:       Deployment" in description
    assert '"api_version": "apps/v1"' in description
    assert "Warning BackOff (count=3" in description
    assert "Back-off restarting failed container" in description
    assert apps_api.calls == [
        (
            "read_namespaced_deployment",
            {
                "name": "sample-api",
                "namespace": "sample",
                "_request_timeout": 7,
            },
        )
    ]


def test_list_batch_network_and_storage_resources() -> None:
    core_api = FakeCoreV1Api()
    reader = KubernetesReader(
        core_api=core_api,
        apps_api=FakeAppsV1Api(),
        batch_api=FakeBatchV1Api(),
        networking_api=FakeNetworkingV1Api(),
        request_timeout_seconds=7,
    )

    assert reader.list_jobs("sample") == [
        JobSummary(
            name="database-migration",
            completions=1,
            succeeded=1,
            active=0,
            failed=0,
        )
    ]
    assert reader.list_cron_jobs("sample") == [
        CronJobSummary(
            name="nightly-backup",
            schedule="0 2 * * *",
            suspended=False,
            active=0,
            last_schedule_time="2026-07-27T02:00:00+00:00",
        )
    ]
    assert reader.list_ingresses("sample") == [
        IngressSummary(
            name="sample",
            ingress_class="nginx",
            hosts=("sample.example.com",),
            addresses=("10.0.0.8",),
        )
    ]
    assert reader.list_persistent_volume_claims("sample") == [
        PersistentVolumeClaimSummary(
            name="mysql-data",
            phase="Bound",
            volume_name="pvc-123",
            capacity="10Gi",
            access_modes=("ReadWriteOnce",),
            storage_class="local-path",
            backend="CSI/disk.csi.example.com",
            reclaim_policy="Retain",
            mounts=(
                PersistentVolumeMountSummary(
                    claim_name="mysql-data",
                    pod_name="mysql-0",
                    pod_phase="Running",
                    container_name="mysql",
                    mount_path="/var/lib/mysql",
                    read_only=False,
                    container_running=True,
                ),
            ),
        )
    ]


def test_browse_pvc_uses_running_mount_and_parses_safe_records() -> None:
    commands: list[dict[str, object]] = []

    def execute(**kwargs) -> str:
        commands.append(kwargs)
        return "O\0d\0backups\0-\0f\0ibdata1\0 1024\0l\0latest\0-\0"

    core_api = FakeCoreV1Api()
    reader = KubernetesReader(
        core_api=core_api,
        apps_api=FakeAppsV1Api(),
        request_timeout_seconds=7,
        pod_executor=execute,
    )

    directory = reader.browse_persistent_volume_claim(
        "sample",
        "mysql-data",
        path=".",
    )

    assert directory.claim_name == "mysql-data"
    assert directory.path == "."
    assert directory.target.pod_name == "mysql-0"
    assert directory.target.container_name == "mysql"
    assert directory.target.mount_path == "/var/lib/mysql"
    assert [
        (entry.name, entry.kind, entry.size_bytes) for entry in directory.entries
    ] == [
        ("backups", "directory", None),
        ("ibdata1", "file", 1024),
        ("latest", "symlink", None),
    ]
    assert commands[0]["name"] == "mysql-0"
    assert commands[0]["container"] == "mysql"
    assert commands[0]["command"][-2:] == ["/var/lib/mysql", "."]


def test_browse_pvc_rejects_path_escape_before_exec() -> None:
    executed = False

    def execute(**kwargs) -> str:
        nonlocal executed
        executed = True
        return ""

    reader = KubernetesReader(
        core_api=FakeCoreV1Api(),
        apps_api=FakeAppsV1Api(),
        request_timeout_seconds=7,
        pod_executor=execute,
    )

    try:
        reader.browse_persistent_volume_claim(
            "sample",
            "mysql-data",
            path="../etc",
        )
    except Exception as error:  # noqa: BLE001 - assert public error message
        assert "挂载根目录" in str(error)
    else:
        raise AssertionError("path escape should be rejected")
    assert not executed


def test_preview_pvc_file_is_bounded_and_reports_truncation() -> None:
    def execute(**kwargs) -> str:
        return "O\x001\x00hello from pvc"

    reader = KubernetesReader(
        core_api=FakeCoreV1Api(),
        apps_api=FakeAppsV1Api(),
        request_timeout_seconds=7,
        pod_executor=execute,
    )

    preview = reader.preview_persistent_volume_claim_file(
        "sample",
        "mysql-data",
        path="logs/app.log",
        max_bytes=1024,
    )

    assert preview.path == "logs/app.log"
    assert preview.content == "hello from pvc"
    assert preview.truncated


def test_storage_scripts_bound_reads_and_reject_symlink_paths(tmp_path: Path) -> None:
    documents = tmp_path / "documents"
    documents.mkdir()
    text_file = documents / "readme.txt"
    text_file.write_text("hello from pvc", encoding="utf-8")
    (tmp_path / "escape").symlink_to(tmp_path.parent, target_is_directory=True)

    def execute(**kwargs):
        result = subprocess.run(
            kwargs["command"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
        )
        return result.stdout

    reader = KubernetesReader(
        core_api=FakeCoreV1Api(mount_path=str(tmp_path)),
        apps_api=FakeAppsV1Api(),
        request_timeout_seconds=7,
        pod_executor=execute,
    )

    directory = reader.browse_persistent_volume_claim(
        "sample",
        "mysql-data",
        path=".",
    )
    entries = {entry.name: entry for entry in directory.entries}
    assert entries["documents"].kind == "directory"
    assert entries["escape"].kind == "symlink"

    preview = reader.preview_persistent_volume_claim_file(
        "sample",
        "mysql-data",
        path="documents/readme.txt",
        max_bytes=5,
    )
    assert preview.content == "hello"
    assert preview.truncated

    try:
        reader.browse_persistent_volume_claim(
            "sample",
            "mysql-data",
            path="escape",
        )
    except Exception as error:  # noqa: BLE001 - assert public error message
        assert "符号链接" in str(error)
    else:
        raise AssertionError("symlink directory should be rejected")


def test_storage_topology_preserves_pod_and_pv_permission_errors() -> None:
    class ForbiddenCoreV1Api(FakeCoreV1Api):
        def list_namespaced_pod(self, **kwargs):
            raise ApiException(status=403, reason="pods is forbidden")

        def read_persistent_volume(self, **kwargs):
            raise ApiException(status=403, reason="persistentvolumes is forbidden")

    reader = KubernetesReader(
        core_api=ForbiddenCoreV1Api(),
        apps_api=FakeAppsV1Api(),
        request_timeout_seconds=7,
    )

    claim = reader.list_persistent_volume_claims("sample")[0]

    assert "pods is forbidden" in claim.mounts_error
    assert "persistentvolumes is forbidden" in claim.backend_error
    assert claim.mounts == ()
    assert claim.backend == "Unavailable"


def test_browse_pvc_retries_another_running_container() -> None:
    class MultipleTargetsCoreV1Api(FakeCoreV1Api):
        def list_namespaced_pod(self, **kwargs):
            response = super().list_namespaced_pod(**kwargs)
            pod = response.items[0]
            pod.spec.containers.append(
                SimpleNamespace(
                    name="aaa-broken",
                    volume_mounts=[
                        SimpleNamespace(
                            name="data",
                            mount_path=self.mount_path,
                            read_only=True,
                        )
                    ],
                )
            )
            pod.status.container_statuses.append(
                SimpleNamespace(
                    name="aaa-broken",
                    state=SimpleNamespace(running=SimpleNamespace()),
                )
            )
            return response

    attempted: list[str] = []

    def execute(**kwargs) -> str:
        attempted.append(kwargs["container"])
        if kwargs["container"] == "aaa-broken":
            return "E\0container has no Python\0"
        return "O\x00f\x00data.txt\x005\x00"

    reader = KubernetesReader(
        core_api=MultipleTargetsCoreV1Api(),
        apps_api=FakeAppsV1Api(),
        request_timeout_seconds=7,
        pod_executor=execute,
    )

    directory = reader.browse_persistent_volume_claim(
        "sample",
        "mysql-data",
        path=".",
    )

    assert attempted == ["aaa-broken", "mysql"]
    assert directory.target.container_name == "mysql"
