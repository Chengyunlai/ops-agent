import time
from collections.abc import Callable
from threading import Thread

import pytest
from ops_agent.diagnostics import DiagnosisReport
from ops_agent.kubernetes import (
    KubernetesError,
    KubernetesReader,
    KubernetesWatchOutcome,
    ServiceEndpointSource,
)

pytestmark = pytest.mark.kubernetes_integration


def test_fixed_failures_produce_grounded_diagnostics(
    eventually_diagnose: Callable[[], DiagnosisReport],
) -> None:
    report = eventually_diagnose()

    findings = {
        (finding.resource_kind, finding.resource_name, finding.summary): finding
        for finding in report.findings
    }
    assert (
        "Service",
        "diagnostics-no-endpoint",
        "Service 没有 Ready Endpoint",
    ) in findings
    assert (
        "Pod",
        "diagnostics-crash-loop",
        "容器反复崩溃重启",
    ) in findings
    assert (
        "Pod",
        "diagnostics-image-pull",
        "容器镜像拉取失败",
    ) in findings

    resource_pressure = findings[
        (
            "Pod",
            "diagnostics-resource-pressure",
            "Pod 因资源不足无法调度",
        )
    ]
    assert "insufficient cpu" in resource_pressure.evidence[0].message.casefold()
    assert "requests(cpu=100k, memory=128Mi" in resource_pressure.evidence[1].message

    rollout = findings[
        (
            "Deployment",
            "diagnostics-rollout",
            "Deployment rollout 超过进度期限",
        )
    ]
    assert tuple(evidence.source for evidence in rollout.evidence) == (
        "deployment_status",
        "deployment_condition",
        "deployment_topology",
    )
    assert "diagnostics-rollout" in rollout.evidence[2].message
    assert "diagnostics-rollout-" in rollout.evidence[2].message


def test_endpoint_slice_rbac_failure_remains_an_explicit_error(
    restricted_reader: KubernetesReader,
) -> None:
    assert restricted_reader.list_services("ops-agent-diagnostics-e2e")

    with pytest.raises(
        KubernetesError,
        match=r"(?s)EndpointSlice.*endpointslices.*forbidden",
    ):
        restricted_reader.list_service_endpoints("ops-agent-diagnostics-e2e")


def test_watch_rbac_failure_falls_back_without_failing_reader(
    restricted_reader: KubernetesReader,
) -> None:
    restricted_reader.list_pods("ops-agent-diagnostics-e2e")

    result = restricted_reader.wait_for_change(
        "ops-agent-diagnostics-e2e",
        timeout_seconds=2,
    )

    assert result.outcome is KubernetesWatchOutcome.UNAVAILABLE
    assert "forbidden" in (result.unavailable_reason or "").casefold()


def test_watch_reports_live_pod_change(
    cluster_reader: KubernetesReader,
    trigger_pod_change: Callable[[], None],
) -> None:
    cluster_reader.list_pods("ops-agent-diagnostics-e2e")
    trigger = Thread(target=trigger_pod_change)
    trigger.start()

    result = cluster_reader.wait_for_change(
        "ops-agent-diagnostics-e2e",
        timeout_seconds=10,
    )
    trigger.join(timeout=5)

    assert not trigger.is_alive()
    assert result.outcome is KubernetesWatchOutcome.CHANGED


def test_endpoint_slice_preserves_service_to_pod_topology(
    cluster_reader: KubernetesReader,
) -> None:
    endpoint = _eventually_ready_endpoint(cluster_reader)

    assert endpoint.source is ServiceEndpointSource.ENDPOINT_SLICE
    assert any(
        target.ready
        and target.target_kind == "Pod"
        and target.target_name == "diagnostics-ready"
        for target in endpoint.targets
    )


def test_endpoint_slice_404_falls_back_to_live_core_v1_endpoints(
    legacy_fallback_reader: KubernetesReader,
) -> None:
    endpoint = _eventually_ready_endpoint(legacy_fallback_reader)

    assert endpoint.source is ServiceEndpointSource.ENDPOINTS
    assert endpoint.ready_addresses >= 1
    assert any(target.target_name == "diagnostics-ready" for target in endpoint.targets)


def _eventually_ready_endpoint(reader: KubernetesReader):
    deadline = time.monotonic() + 120
    while time.monotonic() < deadline:
        endpoint = next(
            (
                item
                for item in reader.list_service_endpoints("ops-agent-diagnostics-e2e")
                if item.service_name == "diagnostics-ready"
            ),
            None,
        )
        if endpoint is not None and endpoint.ready_addresses >= 1:
            return endpoint
        time.sleep(2)
    raise AssertionError("timed out waiting for diagnostics-ready endpoint")
