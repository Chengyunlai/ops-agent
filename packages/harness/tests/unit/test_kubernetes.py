from pathlib import Path
from types import SimpleNamespace

import pytest
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from ops_agent.kubernetes import (
    KubernetesError,
    KubernetesReader,
    PodSummary,
    create_kubernetes_reader,
)
from ops_agent.kubernetes import reader as reader_module
from ops_agent.settings import KubernetesSettings
from urllib3.exceptions import HTTPError


class FakeCoreV1Api:
    def __init__(self, pods: list[SimpleNamespace]) -> None:
        self._pods = pods
        self.calls: list[tuple[str, int]] = []

    def list_namespaced_pod(
        self,
        namespace: str,
        *,
        _request_timeout: int,
    ) -> SimpleNamespace:
        self.calls.append((namespace, _request_timeout))
        return SimpleNamespace(items=self._pods)


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


def test_list_pods_returns_summaries_from_api() -> None:
    pod = SimpleNamespace(
        metadata=SimpleNamespace(name="sample-api"),
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
        )
    ]
    assert api.calls == [("sample", 7)]


def test_list_pods_uses_zero_restarts_before_containers_start() -> None:
    pod = SimpleNamespace(
        metadata=SimpleNamespace(name="sample-pending"),
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
    client_calls: list[tuple[str, bool]] = []

    def fake_new_client_from_config(
        *,
        config_file: str,
        persist_config: bool,
    ) -> object:
        client_calls.append((config_file, persist_config))
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
    settings = KubernetesSettings(
        environment="test",
        namespace="sample",
        kubeconfig_path=Path("/tmp/ops_agent-kubeconfig"),
        request_timeout_seconds=11,
    )

    reader = create_kubernetes_reader(settings)
    pods = reader.list_pods(settings.namespace)

    assert pods == []
    assert client_calls == [
        ("/tmp/ops_agent-kubeconfig", False),
    ]
    assert core_api.calls == [("sample", 11)]


def test_create_kubernetes_reader_reports_invalid_kubeconfig(
    monkeypatch,
) -> None:
    def fail_to_load_config(
        *,
        config_file: str,
        persist_config: bool,
    ) -> object:
        raise ConfigException("Invalid kube-config file")

    monkeypatch.setattr(
        reader_module.config,
        "new_client_from_config",
        fail_to_load_config,
    )
    settings = KubernetesSettings(
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
