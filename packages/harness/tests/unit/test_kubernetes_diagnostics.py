from ops_agent.diagnostics import (
    DiagnosisReport,
    Evidence,
    Finding,
    FindingSeverity,
    KubernetesSnapshot,
    diagnose_kubernetes_snapshot,
)
from ops_agent.kubernetes import DeploymentSummary, PodSummary


def test_diagnosis_reports_pod_outside_running_phase() -> None:
    snapshot = KubernetesSnapshot(
        namespace="sample",
        pods=(
            PodSummary(
                name="sample-api",
                phase="Pending",
                restart_count=0,
                ready_containers=0,
                total_containers=1,
            ),
        ),
        deployments=(),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert report.namespace == "sample"
    assert (
        Finding(
            severity=FindingSeverity.WARNING,
            resource_kind="Pod",
            resource_name="sample-api",
            summary="Pod 未处于 Running 状态",
            evidence=(
                Evidence(
                    source="pod_status",
                    message="phase=Pending",
                ),
            ),
        )
        in report.findings
    )


def test_diagnosis_reports_pod_with_unready_containers() -> None:
    snapshot = KubernetesSnapshot(
        namespace="sample",
        pods=(
            PodSummary(
                name="sample-api",
                phase="Running",
                restart_count=0,
                ready_containers=1,
                total_containers=2,
            ),
        ),
        deployments=(),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert report.findings == (
        Finding(
            severity=FindingSeverity.WARNING,
            resource_kind="Pod",
            resource_name="sample-api",
            summary="Pod 容器未全部就绪",
            evidence=(
                Evidence(
                    source="pod_status",
                    message=("ready_containers=1, total_containers=2"),
                ),
            ),
        ),
    )


def test_diagnosis_reports_deployment_with_missing_ready_replicas() -> None:
    snapshot = KubernetesSnapshot(
        namespace="sample",
        pods=(),
        deployments=(
            DeploymentSummary(
                name="sample-api",
                desired_replicas=3,
                ready_replicas=2,
                available_replicas=2,
                updated_replicas=3,
            ),
        ),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert report.findings == (
        Finding(
            severity=FindingSeverity.WARNING,
            resource_kind="Deployment",
            resource_name="sample-api",
            summary="Deployment 就绪副本少于期望副本",
            evidence=(
                Evidence(
                    source="deployment_status",
                    message=("desired_replicas=3, ready_replicas=2"),
                ),
            ),
        ),
    )


def test_diagnosis_does_not_report_healthy_resources() -> None:
    snapshot = KubernetesSnapshot(
        namespace="sample",
        pods=(
            PodSummary(
                name="sample-api",
                phase="Running",
                restart_count=0,
                ready_containers=1,
                total_containers=1,
            ),
        ),
        deployments=(
            DeploymentSummary(
                name="sample-api",
                desired_replicas=3,
                ready_replicas=3,
                available_replicas=3,
                updated_replicas=3,
            ),
        ),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert report == DiagnosisReport(
        namespace="sample",
        findings=(),
    )
