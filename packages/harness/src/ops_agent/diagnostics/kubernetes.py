from ops_agent.diagnostics.models import (
    DiagnosisReport,
    Evidence,
    Finding,
    FindingSeverity,
    KubernetesSnapshot,
)


def diagnose_kubernetes_snapshot(
    snapshot: KubernetesSnapshot,
) -> DiagnosisReport:
    findings: list[Finding] = []
    for pod in snapshot.pods:
        if pod.phase != "Running":
            findings.append(
                Finding(
                    severity=FindingSeverity.WARNING,
                    resource_kind="Pod",
                    resource_name=pod.name,
                    summary="Pod 未处于 Running 状态",
                    evidence=(
                        Evidence(
                            source="pod_status",
                            message=f"phase={pod.phase}",
                        ),
                    ),
                )
            )
        if pod.ready_containers < pod.total_containers:
            findings.append(
                Finding(
                    severity=FindingSeverity.WARNING,
                    resource_kind="Pod",
                    resource_name=pod.name,
                    summary="Pod 容器未全部就绪",
                    evidence=(
                        Evidence(
                            source="pod_status",
                            message=(
                                f"ready_containers={pod.ready_containers}, "
                                f"total_containers={pod.total_containers}"
                            ),
                        ),
                    ),
                )
            )

    for deployment in snapshot.deployments:
        if deployment.ready_replicas < deployment.desired_replicas:
            findings.append(
                Finding(
                    severity=FindingSeverity.WARNING,
                    resource_kind="Deployment",
                    resource_name=deployment.name,
                    summary="Deployment 就绪副本少于期望副本",
                    evidence=(
                        Evidence(
                            source="deployment_status",
                            message=(
                                "desired_replicas="
                                f"{deployment.desired_replicas}, "
                                "ready_replicas="
                                f"{deployment.ready_replicas}"
                            ),
                        ),
                    ),
                )
            )

    return DiagnosisReport(
        namespace=snapshot.namespace,
        findings=tuple(findings),
    )
