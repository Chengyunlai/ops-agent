from collections.abc import Callable

import pytest
from ops_agent.diagnostics import DiagnosisReport
from ops_agent.kubernetes import KubernetesError, KubernetesReader

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
