from ops_agent.diagnostics import (
    DiagnosisReport,
    Evidence,
    Finding,
    FindingSeverity,
    KubernetesSnapshot,
    diagnose_kubernetes_snapshot,
)
from ops_agent.kubernetes import (
    ContainerStatusSummary,
    DeploymentSummary,
    PodConditionSummary,
    PodSummary,
    ServiceEndpointSummary,
    ServiceSummary,
)


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


def test_diagnosis_reports_crash_loop_with_previous_exit_evidence() -> None:
    snapshot = KubernetesSnapshot(
        namespace="sample",
        pods=(
            PodSummary(
                name="sample-api",
                phase="Running",
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
                        previous_reason="Error",
                        previous_exit_code=1,
                    ),
                ),
            ),
        ),
        deployments=(),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert (
        Finding(
            severity=FindingSeverity.WARNING,
            resource_kind="Pod",
            resource_name="sample-api",
            summary="容器反复崩溃重启",
            evidence=(
                Evidence(
                    source="container_status",
                    message=(
                        "container=api, reason=CrashLoopBackOff, restart_count=4, "
                        "previous_reason=Error, previous_exit_code=1"
                    ),
                ),
            ),
        )
        in report.findings
    )


def test_diagnosis_reports_previous_oom_termination() -> None:
    snapshot = KubernetesSnapshot(
        namespace="sample",
        pods=(
            PodSummary(
                name="memory-worker",
                phase="Running",
                restart_count=1,
                ready_containers=1,
                total_containers=1,
                container_statuses=(
                    ContainerStatusSummary(
                        name="worker",
                        ready=True,
                        restart_count=1,
                        state="running",
                        reason=None,
                        exit_code=None,
                        previous_reason="OOMKilled",
                        previous_exit_code=137,
                    ),
                ),
            ),
        ),
        deployments=(),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert report.findings == (
        Finding(
            severity=FindingSeverity.WARNING,
            resource_kind="Pod",
            resource_name="memory-worker",
            summary="容器因内存不足被终止",
            evidence=(
                Evidence(
                    source="container_status",
                    message=(
                        "container=worker, previous_reason=OOMKilled, "
                        "previous_exit_code=137, restart_count=1"
                    ),
                ),
            ),
        ),
    )


def test_diagnosis_reports_current_oom_termination() -> None:
    snapshot = KubernetesSnapshot(
        namespace="sample",
        pods=(
            PodSummary(
                name="memory-job",
                phase="Failed",
                restart_count=0,
                container_statuses=(
                    ContainerStatusSummary(
                        name="worker",
                        ready=False,
                        restart_count=0,
                        state="terminated",
                        reason="OOMKilled",
                        exit_code=137,
                        previous_reason=None,
                        previous_exit_code=None,
                    ),
                ),
            ),
        ),
        deployments=(),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert (
        Finding(
            severity=FindingSeverity.WARNING,
            resource_kind="Pod",
            resource_name="memory-job",
            summary="容器因内存不足被终止",
            evidence=(
                Evidence(
                    source="container_status",
                    message=(
                        "container=worker, reason=OOMKilled, exit_code=137, "
                        "restart_count=0"
                    ),
                ),
            ),
        )
        in report.findings
    )


def test_diagnosis_reports_image_pull_backoff() -> None:
    snapshot = KubernetesSnapshot(
        namespace="sample",
        pods=(
            PodSummary(
                name="sample-api",
                phase="Pending",
                restart_count=0,
                ready_containers=0,
                total_containers=1,
                container_statuses=(
                    ContainerStatusSummary(
                        name="api",
                        ready=False,
                        restart_count=0,
                        state="waiting",
                        reason="ImagePullBackOff",
                        exit_code=None,
                        previous_reason=None,
                        previous_exit_code=None,
                    ),
                ),
            ),
        ),
        deployments=(),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert (
        Finding(
            severity=FindingSeverity.WARNING,
            resource_kind="Pod",
            resource_name="sample-api",
            summary="容器镜像拉取失败",
            evidence=(
                Evidence(
                    source="container_status",
                    message="container=api, state=waiting, reason=ImagePullBackOff",
                ),
            ),
        )
        in report.findings
    )


def test_diagnosis_reports_unschedulable_pending_pod() -> None:
    snapshot = KubernetesSnapshot(
        namespace="sample",
        pods=(
            PodSummary(
                name="sample-worker",
                phase="Pending",
                restart_count=0,
                ready_containers=0,
                total_containers=1,
                conditions=(
                    PodConditionSummary(
                        type="PodScheduled",
                        status="False",
                        reason="Unschedulable",
                        message="0/3 nodes are available: insufficient cpu",
                    ),
                ),
            ),
        ),
        deployments=(),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert (
        Finding(
            severity=FindingSeverity.WARNING,
            resource_kind="Pod",
            resource_name="sample-worker",
            summary="Pod 无法调度",
            evidence=(
                Evidence(
                    source="pod_condition",
                    message=(
                        "condition=PodScheduled, status=False, "
                        "reason=Unschedulable, message=0/3 nodes are available: "
                        "insufficient cpu"
                    ),
                ),
            ),
        )
        in report.findings
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


def test_diagnosis_reports_service_without_ready_endpoints() -> None:
    snapshot = KubernetesSnapshot(
        namespace="sample",
        pods=(),
        deployments=(),
        services=(
            ServiceSummary(
                name="sample-api",
                type="ClusterIP",
                cluster_ip="10.43.0.10",
                ports=[],
            ),
        ),
        service_endpoints=(),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert report.findings == (
        Finding(
            severity=FindingSeverity.WARNING,
            resource_kind="Service",
            resource_name="sample-api",
            summary="Service 没有 Ready Endpoint",
            evidence=(
                Evidence(
                    source="service_endpoints",
                    message=(
                        "type=ClusterIP, ready_addresses=0, "
                        "not_ready_addresses=0, endpoint_slices=0"
                    ),
                ),
            ),
        ),
    )


def test_diagnosis_does_not_report_service_with_ready_endpoints() -> None:
    snapshot = KubernetesSnapshot(
        namespace="sample",
        pods=(),
        deployments=(),
        services=(
            ServiceSummary(
                name="sample-api",
                type="ClusterIP",
                cluster_ip="10.43.0.10",
                ports=[],
            ),
        ),
        service_endpoints=(
            ServiceEndpointSummary(
                service_name="sample-api",
                ready_addresses=2,
                not_ready_addresses=1,
                endpoint_slice_count=2,
            ),
        ),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert report.findings == ()


def test_diagnosis_does_not_report_external_name_without_endpoint_slices() -> None:
    snapshot = KubernetesSnapshot(
        namespace="sample",
        pods=(),
        deployments=(),
        services=(
            ServiceSummary(
                name="external-api",
                type="ExternalName",
                cluster_ip=None,
                ports=[],
            ),
        ),
        service_endpoints=(),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert report.findings == ()


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
