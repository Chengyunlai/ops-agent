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
        for container in pod.container_statuses:
            if container.reason == "CrashLoopBackOff":
                findings.append(
                    Finding(
                        severity=FindingSeverity.WARNING,
                        resource_kind="Pod",
                        resource_name=pod.name,
                        summary="容器反复崩溃重启",
                        evidence=(
                            Evidence(
                                source="container_status",
                                message=(
                                    f"container={container.name}, "
                                    f"reason={container.reason}, "
                                    f"restart_count={container.restart_count}, "
                                    "previous_reason="
                                    f"{container.previous_reason}, "
                                    "previous_exit_code="
                                    f"{container.previous_exit_code}"
                                ),
                            ),
                        ),
                    )
                )
            if container.reason == "OOMKilled":
                findings.append(
                    Finding(
                        severity=FindingSeverity.WARNING,
                        resource_kind="Pod",
                        resource_name=pod.name,
                        summary="容器因内存不足被终止",
                        evidence=(
                            Evidence(
                                source="container_status",
                                message=(
                                    f"container={container.name}, "
                                    "reason=OOMKilled, "
                                    f"exit_code={container.exit_code}, "
                                    f"restart_count={container.restart_count}"
                                ),
                            ),
                        ),
                    )
                )
            elif container.previous_reason == "OOMKilled":
                findings.append(
                    Finding(
                        severity=FindingSeverity.WARNING,
                        resource_kind="Pod",
                        resource_name=pod.name,
                        summary="容器因内存不足被终止",
                        evidence=(
                            Evidence(
                                source="container_status",
                                message=(
                                    f"container={container.name}, "
                                    "previous_reason=OOMKilled, "
                                    "previous_exit_code="
                                    f"{container.previous_exit_code}, "
                                    f"restart_count={container.restart_count}"
                                ),
                            ),
                        ),
                    )
                )
            if container.reason == "ImagePullBackOff":
                findings.append(
                    Finding(
                        severity=FindingSeverity.WARNING,
                        resource_kind="Pod",
                        resource_name=pod.name,
                        summary="容器镜像拉取失败",
                        evidence=(
                            Evidence(
                                source="container_status",
                                message=(
                                    f"container={container.name}, "
                                    f"state={container.state}, "
                                    f"reason={container.reason}"
                                ),
                            ),
                        ),
                    )
                )
        for condition in pod.conditions:
            if condition.type != "PodScheduled" or condition.status != "False":
                continue
            findings.append(
                Finding(
                    severity=FindingSeverity.WARNING,
                    resource_kind="Pod",
                    resource_name=pod.name,
                    summary="Pod 无法调度",
                    evidence=(
                        Evidence(
                            source="pod_condition",
                            message=(
                                f"condition={condition.type}, "
                                f"status={condition.status}, "
                                f"reason={condition.reason}, "
                                f"message={condition.message}"
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
        if (
            deployment.generation is not None
            and deployment.observed_generation is not None
            and deployment.observed_generation < deployment.generation
        ):
            findings.append(
                Finding(
                    severity=FindingSeverity.WARNING,
                    resource_kind="Deployment",
                    resource_name=deployment.name,
                    summary="Deployment 控制器尚未观察到最新版本",
                    evidence=(
                        Evidence(
                            source="deployment_status",
                            message=(
                                f"generation={deployment.generation}, "
                                "observed_generation="
                                f"{deployment.observed_generation}, "
                                f"revision={deployment.revision}"
                            ),
                        ),
                    ),
                )
            )
        for condition in deployment.conditions:
            if not (
                condition.type == "Progressing"
                and condition.status == "False"
                and condition.reason == "ProgressDeadlineExceeded"
            ):
                continue
            findings.append(
                Finding(
                    severity=FindingSeverity.WARNING,
                    resource_kind="Deployment",
                    resource_name=deployment.name,
                    summary="Deployment rollout 超过进度期限",
                    evidence=(
                        Evidence(
                            source="deployment_status",
                            message=(
                                "desired_replicas="
                                f"{deployment.desired_replicas}, "
                                "updated_replicas="
                                f"{deployment.updated_replicas}, "
                                f"ready_replicas={deployment.ready_replicas}, "
                                "available_replicas="
                                f"{deployment.available_replicas}"
                            ),
                        ),
                        Evidence(
                            source="deployment_condition",
                            message=(
                                f"generation={deployment.generation}, "
                                "observed_generation="
                                f"{deployment.observed_generation}, "
                                f"revision={deployment.revision}, "
                                f"condition={condition.type}, "
                                f"status={condition.status}, "
                                f"reason={condition.reason}, "
                                f"message={condition.message}"
                            ),
                        ),
                        _deployment_topology_evidence(snapshot, deployment.name),
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


def _deployment_topology_evidence(
    snapshot: KubernetesSnapshot,
    deployment_name: str,
) -> Evidence:
    replica_sets = sorted(
        (
            replica_set
            for replica_set in snapshot.replica_sets
            if replica_set.controller is not None
            and replica_set.controller.kind == "Deployment"
            and replica_set.controller.name == deployment_name
        ),
        key=lambda replica_set: replica_set.name,
    )
    replica_set_names = {replica_set.name for replica_set in replica_sets}
    pods = sorted(
        (
            pod
            for pod in snapshot.pods
            if pod.controller is not None
            and pod.controller.kind == "ReplicaSet"
            and pod.controller.name in replica_set_names
        ),
        key=lambda pod: pod.name,
    )
    replica_set_evidence = (
        ", ".join(
            f"{replica_set.name}(revision={replica_set.revision}, "
            f"desired={replica_set.desired_replicas}, "
            f"ready={replica_set.ready_replicas})"
            for replica_set in replica_sets
        )
        or "none"
    )
    pod_evidence = (
        ", ".join(
            f"{pod.name}(owner={pod.controller.name}, phase={pod.phase})"
            for pod in pods
            if pod.controller is not None
        )
        or "none"
    )
    return Evidence(
        source="deployment_topology",
        message=f"replica_sets={replica_set_evidence}; pods={pod_evidence}",
    )
