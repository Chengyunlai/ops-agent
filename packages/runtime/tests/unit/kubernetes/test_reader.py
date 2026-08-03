from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Thread
from types import SimpleNamespace

import pytest
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from ops_agent.kubernetes import (
    ContainerResourceSummary,
    ContainerResourceType,
    ContainerStatusSummary,
    ControllerReferenceSummary,
    KubernetesChangeSignal,
    KubernetesConnectionSettings,
    KubernetesError,
    KubernetesReader,
    KubernetesResourceKind,
    KubernetesWatchOutcome,
    KubernetesWatchResult,
    PodConditionSummary,
    PodSummary,
    create_kubernetes_reader,
)
from ops_agent.kubernetes import reader as reader_module
from urllib3.exceptions import HTTPError


class FakeCoreV1Api:
    def __init__(
        self,
        pods: list[SimpleNamespace],
        *,
        resource_version: str | None = None,
    ) -> None:
        self._pods = pods
        self._resource_version = resource_version
        self.calls: list[tuple[str, int]] = []

    def list_namespaced_pod(
        self,
        namespace: str,
        *,
        _request_timeout: int,
    ) -> SimpleNamespace:
        self.calls.append((namespace, _request_timeout))
        return SimpleNamespace(
            items=self._pods,
            metadata=SimpleNamespace(resource_version=self._resource_version),
        )


class ForbiddenCoreV1Api:
    def list_namespaced_pod(
        self,
        namespace: str,
        *,
        _request_timeout: int,
    ) -> SimpleNamespace:
        raise ApiException(status=403, reason="Forbidden")


class UnreachableCoreV1Api:
    def list_namespaced_pod(
        self,
        namespace: str,
        *,
        _request_timeout: int,
    ) -> SimpleNamespace:
        raise HTTPError("connection failed")


class FakePodWatcher:
    def __init__(self, events: list[dict[str, object]]) -> None:
        self._events = events
        self.calls: list[tuple[object, dict[str, object]]] = []
        self.stopped = False

    def stream(self, request: object, **kwargs: object):
        self.calls.append((request, kwargs))
        yield from self._events

    def stop(self) -> None:
        self.stopped = True


class FailingPodWatcher(FakePodWatcher):
    def __init__(self, error: Exception) -> None:
        super().__init__([])
        self._error = error

    def stream(self, request: object, **kwargs: object):
        self.calls.append((request, kwargs))
        raise self._error


class BlockingPodWatcher(FakePodWatcher):
    def __init__(self) -> None:
        super().__init__([])
        self.started = Event()
        self.released = Event()

    def stream(self, request: object, **kwargs: object):
        self.calls.append((request, kwargs))
        self.started.set()
        self.released.wait(timeout=1)
        if False:
            yield {}

    def stop(self) -> None:
        super().stop()
        self.released.set()


def test_list_pods_returns_summaries_from_api() -> None:
    created_at = datetime(2026, 7, 28, 9, 15, tzinfo=UTC)
    pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="sample-api",
            creation_timestamp=created_at,
        ),
        spec=SimpleNamespace(
            containers=[SimpleNamespace(), SimpleNamespace()],
        ),
        status=SimpleNamespace(
            phase="Running",
            container_statuses=[
                SimpleNamespace(restart_count=1, ready=True),
                SimpleNamespace(restart_count=2, ready=False),
            ],
        ),
    )
    api = FakeCoreV1Api([pod])
    reader = KubernetesReader(
        core_api=api,
        apps_api=object(),
        request_timeout_seconds=7,
    )

    pods = reader.list_pods(namespace="sample")

    assert pods == [
        PodSummary(
            name="sample-api",
            phase="Running",
            restart_count=3,
            ready_containers=1,
            total_containers=2,
            created_at=created_at,
        )
    ]
    assert api.calls == [("sample", 7)]


def test_wait_for_change_returns_pod_watch_signal() -> None:
    pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="sample-api",
            resource_version="42",
        )
    )
    watcher = FakePodWatcher([{"type": "MODIFIED", "object": pod}])
    api = FakeCoreV1Api([])
    reader = KubernetesReader(
        core_api=api,
        apps_api=object(),
        request_timeout_seconds=7,
        watch_factory=lambda: watcher,
    )

    result = reader.wait_for_change(
        namespace="sample",
        timeout_seconds=5,
    )

    assert result == KubernetesWatchResult(
        outcome=KubernetesWatchOutcome.CHANGED,
        change=KubernetesChangeSignal(
            resource_kind=KubernetesResourceKind.POD,
            event_type="MODIFIED",
            resource_name="sample-api",
        ),
    )
    assert watcher.calls == [
        (
            api.list_namespaced_pod,
            {
                "namespace": "sample",
                "timeout_seconds": 5,
                "_request_timeout": 12,
            },
        )
    ]
    assert watcher.stopped


def test_wait_for_change_treats_watch_timeout_as_no_change() -> None:
    watcher = FakePodWatcher([])
    reader = KubernetesReader(
        core_api=FakeCoreV1Api([]),
        apps_api=object(),
        request_timeout_seconds=7,
        watch_factory=lambda: watcher,
    )

    result = reader.wait_for_change(
        namespace="sample",
        timeout_seconds=5,
    )

    assert result == KubernetesWatchResult(
        outcome=KubernetesWatchOutcome.TIMED_OUT,
    )
    assert watcher.stopped


def test_wait_for_change_reports_forbidden_watch_as_unavailable() -> None:
    watcher = FailingPodWatcher(ApiException(status=403, reason="Forbidden"))
    reader = KubernetesReader(
        core_api=FakeCoreV1Api([]),
        apps_api=object(),
        request_timeout_seconds=7,
        watch_factory=lambda: watcher,
    )

    result = reader.wait_for_change(
        namespace="sample",
        timeout_seconds=5,
    )

    assert result.outcome is KubernetesWatchOutcome.UNAVAILABLE
    assert result.change is None
    assert "403" in (result.unavailable_reason or "")
    assert "Forbidden" in (result.unavailable_reason or "")
    assert watcher.stopped


def test_wait_for_change_continues_from_latest_pod_list_version() -> None:
    watcher = FakePodWatcher([])
    api = FakeCoreV1Api([], resource_version="100")
    reader = KubernetesReader(
        core_api=api,
        apps_api=object(),
        request_timeout_seconds=7,
        watch_factory=lambda: watcher,
    )

    reader.list_pods("sample")
    reader.wait_for_change(namespace="sample", timeout_seconds=5)

    assert watcher.calls[0][1]["resource_version"] == "100"


def test_wait_for_change_does_not_open_watch_after_stop() -> None:
    watcher = FakePodWatcher([])
    stop_event = Event()
    stop_event.set()
    reader = KubernetesReader(
        core_api=FakeCoreV1Api([]),
        apps_api=object(),
        request_timeout_seconds=7,
        watch_factory=lambda: watcher,
    )

    result = reader.wait_for_change(
        namespace="sample",
        timeout_seconds=5,
        stop_event=stop_event,
    )

    assert result.outcome is KubernetesWatchOutcome.STOPPED
    assert watcher.calls == []


def test_stop_waiting_for_change_unblocks_active_watch() -> None:
    watcher = BlockingPodWatcher()
    reader = KubernetesReader(
        core_api=FakeCoreV1Api([]),
        apps_api=object(),
        request_timeout_seconds=7,
        watch_factory=lambda: watcher,
    )
    results: list[KubernetesWatchResult] = []
    thread = Thread(
        target=lambda: results.append(
            reader.wait_for_change(namespace="sample", timeout_seconds=30)
        )
    )
    thread.start()
    assert watcher.started.wait(timeout=1)

    reader.stop_waiting_for_change()
    thread.join(timeout=1)

    assert not thread.is_alive()
    assert watcher.stopped
    assert results == [KubernetesWatchResult(outcome=KubernetesWatchOutcome.TIMED_OUT)]


def test_list_pods_normalizes_container_states_and_scheduling_conditions() -> None:
    pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="sample-api",
            creation_timestamp=None,
            owner_references=[
                SimpleNamespace(
                    kind="ReplicaSet",
                    name="sample-api-7f8",
                    controller=True,
                )
            ],
        ),
        spec=SimpleNamespace(
            containers=[
                SimpleNamespace(
                    name="api",
                    resources=SimpleNamespace(
                        requests={"cpu": "250m", "memory": "128Mi"},
                        limits={
                            "cpu": "1",
                            "memory": "512Mi",
                            "ephemeral-storage": "1Gi",
                        },
                    ),
                )
            ],
            init_containers=[
                SimpleNamespace(
                    name="migration",
                    resources=SimpleNamespace(
                        requests={"cpu": "2", "memory": "1Gi"},
                        limits={"cpu": "2", "memory": "1Gi"},
                    ),
                )
            ],
        ),
        status=SimpleNamespace(
            phase="Pending",
            reason=None,
            message=None,
            qos_class="Burstable",
            container_statuses=[
                SimpleNamespace(
                    name="api",
                    restart_count=4,
                    ready=False,
                    state=SimpleNamespace(
                        running=None,
                        waiting=SimpleNamespace(reason="CrashLoopBackOff"),
                        terminated=None,
                    ),
                    last_state=SimpleNamespace(
                        running=None,
                        waiting=None,
                        terminated=SimpleNamespace(
                            reason="OOMKilled",
                            exit_code=137,
                        ),
                    ),
                )
            ],
            conditions=[
                SimpleNamespace(
                    type="PodScheduled",
                    status="False",
                    reason="Unschedulable",
                    message="0/3 nodes are available: insufficient cpu",
                )
            ],
        ),
    )
    reader = KubernetesReader(
        core_api=FakeCoreV1Api([pod]),
        apps_api=object(),
        request_timeout_seconds=7,
    )

    pods = reader.list_pods(namespace="sample")

    assert pods == [
        PodSummary(
            name="sample-api",
            phase="Pending",
            restart_count=4,
            ready_containers=0,
            total_containers=1,
            container_statuses=(
                ContainerStatusSummary(
                    name="api",
                    ready=False,
                    restart_count=4,
                    state="waiting",
                    reason="CrashLoopBackOff",
                    exit_code=None,
                    previous_reason="OOMKilled",
                    previous_exit_code=137,
                ),
            ),
            conditions=(
                PodConditionSummary(
                    type="PodScheduled",
                    status="False",
                    reason="Unschedulable",
                    message="0/3 nodes are available: insufficient cpu",
                ),
            ),
            controller=ControllerReferenceSummary(
                kind="ReplicaSet",
                name="sample-api-7f8",
            ),
            qos_class="Burstable",
            resources=(
                ContainerResourceSummary(
                    name="api",
                    cpu_request="250m",
                    cpu_limit="1",
                    memory_request="128Mi",
                    memory_limit="512Mi",
                    ephemeral_storage_limit="1Gi",
                ),
                ContainerResourceSummary(
                    name="migration",
                    container_type=ContainerResourceType.INIT,
                    cpu_request="2",
                    cpu_limit="2",
                    memory_request="1Gi",
                    memory_limit="1Gi",
                ),
            ),
        )
    ]


def test_list_pods_uses_zero_restarts_before_containers_start() -> None:
    pod = SimpleNamespace(
        metadata=SimpleNamespace(
            name="sample-pending",
            creation_timestamp=None,
        ),
        spec=SimpleNamespace(containers=[]),
        status=SimpleNamespace(
            phase="Pending",
            container_statuses=None,
        ),
    )
    api = FakeCoreV1Api([pod])
    reader = KubernetesReader(
        core_api=api,
        apps_api=object(),
        request_timeout_seconds=7,
    )

    pods = reader.list_pods(namespace="sample")

    assert pods == [
        PodSummary(
            name="sample-pending",
            phase="Pending",
            restart_count=0,
        )
    ]


def test_list_pods_reports_kubernetes_api_failure() -> None:
    reader = KubernetesReader(
        core_api=ForbiddenCoreV1Api(),
        apps_api=object(),
        request_timeout_seconds=7,
    )

    with pytest.raises(
        KubernetesError,
        match="查询 namespace 'sample' 的 Pod 失败",
    ):
        reader.list_pods(namespace="sample")


def test_list_pods_reports_network_failure() -> None:
    reader = KubernetesReader(
        core_api=UnreachableCoreV1Api(),
        apps_api=object(),
        request_timeout_seconds=7,
    )

    with pytest.raises(
        KubernetesError,
        match="查询 namespace 'sample' 的 Pod 失败",
    ):
        reader.list_pods(namespace="sample")


def test_create_kubernetes_reader_uses_settings(monkeypatch) -> None:
    api_client = object()
    core_api = FakeCoreV1Api([])
    apps_api = object()
    client_calls: list[tuple[str, bool, object]] = []

    def fake_new_client_from_config(
        *,
        config_file: str,
        persist_config: bool,
        client_configuration: object,
    ) -> object:
        client_calls.append((config_file, persist_config, client_configuration))
        return api_client

    def fake_core_v1_api(received_api_client: object) -> FakeCoreV1Api:
        assert received_api_client is api_client
        return core_api

    def fake_apps_v1_api(received_api_client: object) -> object:
        assert received_api_client is api_client
        return apps_api

    monkeypatch.setattr(
        reader_module.config,
        "new_client_from_config",
        fake_new_client_from_config,
    )
    monkeypatch.setattr(
        reader_module,
        "CoreV1Api",
        fake_core_v1_api,
    )
    monkeypatch.setattr(
        reader_module,
        "AppsV1Api",
        fake_apps_v1_api,
    )
    settings = KubernetesConnectionSettings(
        environment="test",
        namespace="sample",
        kubeconfig_path=Path("~/ops_agent-kubeconfig"),
        request_timeout_seconds=11,
        proxy_url="http://127.0.0.1:7897",
    )

    reader = create_kubernetes_reader(settings)
    pods = reader.list_pods(settings.namespace)

    assert pods == []
    assert len(client_calls) == 1
    assert client_calls[0][:2] == (
        str(Path("~/ops_agent-kubeconfig").expanduser()),
        False,
    )
    assert client_calls[0][2].proxy == "http://127.0.0.1:7897/"
    assert core_api.calls == [("sample", 11)]


def test_create_kubernetes_reader_reports_invalid_kubeconfig(
    monkeypatch,
) -> None:
    def fail_to_load_config(
        *,
        config_file: str,
        persist_config: bool,
        client_configuration: object,
    ) -> object:
        raise ConfigException("Invalid kube-config file")

    monkeypatch.setattr(
        reader_module.config,
        "new_client_from_config",
        fail_to_load_config,
    )
    settings = KubernetesConnectionSettings(
        environment="test",
        namespace="sample",
        kubeconfig_path=Path("/tmp/missing-kubeconfig"),
        request_timeout_seconds=11,
    )

    with pytest.raises(
        KubernetesError,
        match="无法加载 kubeconfig: /tmp/missing-kubeconfig",
    ):
        create_kubernetes_reader(settings)
