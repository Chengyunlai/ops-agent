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

    endpoints_by_service = {
        endpoints.service_name: endpoints for endpoints in snapshot.service_endpoints
    }
    for service in snapshot.services:
        if service.type == "ExternalName":
            continue
        endpoints = endpoints_by_service.get(service.name)
        ready_addresses = endpoints.ready_addresses if endpoints is not None else 0
        if ready_addresses > 0:
            continue
        not_ready_addresses = (
            endpoints.not_ready_addresses if endpoints is not None else 0
        )
        endpoint_slice_count = (
            endpoints.endpoint_slice_count if endpoints is not None else 0
        )
        findings.append(
            Finding(
                severity=FindingSeverity.WARNING,
                resource_kind="Service",
                resource_name=service.name,
                summary="Service 没有 Ready Endpoint",
                evidence=(
                    Evidence(
                        source="service_endpoints",
                        message=(
                            f"type={service.type}, "
                            f"ready_addresses={ready_addresses}, "
                            f"not_ready_addresses={not_ready_addresses}, "
                            f"endpoint_slices={endpoint_slice_count}"
                        ),
                    ),
                ),
            )
        )

    return DiagnosisReport(
        namespace=snapshot.namespace,
        findings=tuple(findings),
    )
