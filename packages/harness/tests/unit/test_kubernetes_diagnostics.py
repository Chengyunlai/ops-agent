from ops_agent.diagnostics import (
    DiagnosisReport,
    Evidence,
    Finding,
    FindingCode,
    FindingSeverity,
    KubernetesSnapshot,
    diagnose_kubernetes_snapshot,
)
from ops_agent.kubernetes import (
    ContainerStatusSummary,
    ControllerReferenceSummary,
    DeploymentConditionSummary,
    DeploymentSummary,
    PodConditionSummary,
    PodSummary,
    ReplicaSetSummary,
    ServiceEndpointSource,
    ServiceEndpointSummary,
    ServiceEndpointTargetSummary,
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
            container_name="api",
            code=FindingCode.POD_CRASH_LOOP,
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
            container_name="worker",
            code=FindingCode.POD_OOM_KILLED,
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
            container_name="worker",
            code=FindingCode.POD_OOM_KILLED,
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
            container_name="api",
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


def test_diagnosis_reports_rollout_deadline_with_owned_resources() -> None:
    snapshot = KubernetesSnapshot(
        namespace="sample",
        pods=(
            PodSummary(
                name="sample-api-7f8-abc",
                phase="Pending",
                restart_count=0,
                controller=ControllerReferenceSummary(
                    kind="ReplicaSet",
                    name="sample-api-7f8",
                ),
            ),
        ),
        deployments=(
            DeploymentSummary(
                name="sample-api",
                desired_replicas=3,
                ready_replicas=1,
                available_replicas=1,
                updated_replicas=1,
                generation=7,
                observed_generation=7,
                revision="4",
                conditions=(
                    DeploymentConditionSummary(
                        type="Progressing",
                        status="False",
                        reason="ProgressDeadlineExceeded",
                        message="ReplicaSet sample-api-7f8 has timed out progressing",
                    ),
                ),
            ),
        ),
        replica_sets=(
            ReplicaSetSummary(
                name="sample-api-7f8",
                desired_replicas=3,
                current_replicas=1,
                ready_replicas=1,
                revision="4",
                controller=ControllerReferenceSummary(
                    kind="Deployment",
                    name="sample-api",
                ),
            ),
        ),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert (
        Finding(
            severity=FindingSeverity.WARNING,
            resource_kind="Deployment",
            resource_name="sample-api",
            summary="Deployment rollout 超过进度期限",
            evidence=(
                Evidence(
                    source="deployment_status",
                    message=(
                        "desired_replicas=3, updated_replicas=1, "
                        "ready_replicas=1, available_replicas=1"
                    ),
                ),
                Evidence(
                    source="deployment_condition",
                    message=(
                        "generation=7, observed_generation=7, revision=4, "
                        "condition=Progressing, status=False, "
                        "reason=ProgressDeadlineExceeded, message=ReplicaSet "
                        "sample-api-7f8 has timed out progressing"
                    ),
                ),
                Evidence(
                    source="deployment_topology",
                    message=(
                        "replica_sets=sample-api-7f8(revision=4, desired=3, "
                        "ready=1); pods=sample-api-7f8-abc"
                        "(owner=sample-api-7f8, phase=Pending)"
                    ),
                ),
            ),
        )
        in report.findings
    )


def test_diagnosis_reports_deployment_updated_replicas_stalled() -> None:
    snapshot = KubernetesSnapshot(
        namespace="sample",
        pods=(),
        deployments=(
            DeploymentSummary(
                name="sample-api",
                desired_replicas=3,
                ready_replicas=3,
                available_replicas=3,
                updated_replicas=1,
                generation=8,
                observed_generation=8,
                revision="5",
            ),
        ),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert report.findings == (
        Finding(
            severity=FindingSeverity.WARNING,
            resource_kind="Deployment",
            resource_name="sample-api",
            summary="Deployment 更新副本少于期望副本",
            evidence=(
                Evidence(
                    source="deployment_status",
                    message=(
                        "desired_replicas=3, updated_replicas=1, "
                        "ready_replicas=3, available_replicas=3, revision=5"
                    ),
                ),
            ),
        ),
    )


def test_diagnosis_reports_unobserved_deployment_generation() -> None:
    snapshot = KubernetesSnapshot(
        namespace="sample",
        pods=(),
        deployments=(
            DeploymentSummary(
                name="sample-api",
                desired_replicas=3,
                ready_replicas=3,
                available_replicas=3,
                updated_replicas=3,
                generation=8,
                observed_generation=7,
                revision="5",
            ),
        ),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert report.findings == (
        Finding(
            severity=FindingSeverity.WARNING,
            resource_kind="Deployment",
            resource_name="sample-api",
            summary="Deployment 控制器尚未观察到最新版本",
            evidence=(
                Evidence(
                    source="deployment_status",
                    message="generation=8, observed_generation=7, revision=5",
                ),
            ),
        ),
    )


def test_diagnosis_ignores_inactive_progress_deadline_condition() -> None:
    snapshot = KubernetesSnapshot(
        namespace="sample",
        pods=(),
        deployments=(
            DeploymentSummary(
                name="sample-api",
                desired_replicas=3,
                ready_replicas=3,
                available_replicas=3,
                updated_replicas=3,
                generation=8,
                observed_generation=8,
                revision="5",
                conditions=(
                    DeploymentConditionSummary(
                        type="Progressing",
                        status="True",
                        reason="ProgressDeadlineExceeded",
                    ),
                ),
            ),
        ),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert report.findings == ()


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
                        "source=none, type=ClusterIP, ready_addresses=0, "
                        "not_ready_addresses=0, endpoint_slices=0"
                    ),
                ),
            ),
        ),
    )


def test_diagnosis_explains_service_endpoint_source_and_pod_topology() -> None:
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
                ready_addresses=0,
                not_ready_addresses=1,
                endpoint_slice_count=0,
                source=ServiceEndpointSource.ENDPOINTS,
                targets=(
                    ServiceEndpointTargetSummary(
                        address="10.42.0.8",
                        ready=False,
                        target_kind="Pod",
                        target_name="sample-api-7f8-x1",
                    ),
                ),
            ),
        ),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    finding = report.findings[0]
    assert finding.evidence == (
        Evidence(
            source="service_endpoints",
            message=(
                "source=Endpoints, type=ClusterIP, ready_addresses=0, "
                "not_ready_addresses=1, endpoint_slices=0"
            ),
        ),
        Evidence(
            source="service_topology",
            message="10.42.0.8 -> Pod/sample-api-7f8-x1 (not-ready)",
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
                generation=7,
                observed_generation=7,
                revision="4",
                conditions=(
                    DeploymentConditionSummary(
                        type="Progressing",
                        status="True",
                        reason="NewReplicaSetAvailable",
                    ),
                ),
            ),
        ),
    )

    report = diagnose_kubernetes_snapshot(snapshot)

    assert report == DiagnosisReport(
        namespace="sample",
        findings=(),
    )
