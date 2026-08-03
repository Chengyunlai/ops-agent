from ops_agent.diagnostics.models import (
    DiagnosisReport,
    Evidence,
    Finding,
    FindingCode,
    FindingSeverity,
    KubernetesSnapshot,
)
from ops_agent.kubernetes import ContainerResourceType, PodSummary

_SCHEDULING_RESOURCE_MARKERS = (
    "insufficient cpu",
    "insufficient memory",
    "insufficient ephemeral-storage",
    "too many pods",
    "memory-pressure",
    "disk-pressure",
    "pid-pressure",
)
_EVICTION_RESOURCE_MARKERS = (
    "the node was low on resource:",
    "the node had condition: [memorypressure]",
    "the node had condition: [diskpressure]",
    "the node had condition: [pidpressure]",
    "ephemeral local storage usage exceeds",
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
                    evidence=(_pod_status_evidence(pod),),
                )
            )
        if _is_resource_pressure_eviction(pod.status_reason, pod.status_message):
            findings.append(
                Finding(
                    severity=FindingSeverity.WARNING,
                    resource_kind="Pod",
                    resource_name=pod.name,
                    summary="Pod 因节点资源压力被驱逐",
                    code=FindingCode.POD_RESOURCE_PRESSURE_EVICTION,
                    evidence=_with_pod_resources(
                        pod,
                        Evidence(
                            source="pod_status",
                            message=(
                                f"phase={pod.phase}, reason={pod.status_reason}, "
                                f"message={pod.status_message}"
                            ),
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
                        container_name=container.name,
                        code=FindingCode.POD_CRASH_LOOP,
                        evidence=_with_pod_resources(
                            pod,
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
                        container_name=container.name,
                        code=FindingCode.POD_OOM_KILLED,
                        evidence=_with_pod_resources(
                            pod,
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
                        container_name=container.name,
                        code=FindingCode.POD_OOM_KILLED,
                        evidence=_with_pod_resources(
                            pod,
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
                        container_name=container.name,
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
            resource_shortage = _contains_marker(
                condition.message,
                _SCHEDULING_RESOURCE_MARKERS,
            )
            findings.append(
                Finding(
                    severity=FindingSeverity.WARNING,
                    resource_kind="Pod",
                    resource_name=pod.name,
                    summary=(
                        "Pod 因资源不足无法调度"
                        if resource_shortage
                        else "Pod 无法调度"
                    ),
                    code=(
                        FindingCode.POD_RESOURCE_UNSCHEDULABLE
                        if resource_shortage
                        else None
                    ),
                    evidence=_with_pod_resources(
                        pod,
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
        if deployment.updated_replicas < deployment.desired_replicas:
            findings.append(
                Finding(
                    severity=FindingSeverity.WARNING,
                    resource_kind="Deployment",
                    resource_name=deployment.name,
                    summary="Deployment 更新副本少于期望副本",
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
                                f"{deployment.available_replicas}, "
                                f"revision={deployment.revision}"
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
        endpoint_source = endpoints.source.value if endpoints is not None else "none"
        evidence = [
            Evidence(
                source="service_endpoints",
                message=(
                    f"source={endpoint_source}, "
                    f"type={service.type}, "
                    f"ready_addresses={ready_addresses}, "
                    f"not_ready_addresses={not_ready_addresses}, "
                    f"endpoint_slices={endpoint_slice_count}"
                ),
            )
        ]
        if endpoints is not None and endpoints.targets:
            evidence.append(
                Evidence(
                    source="service_topology",
                    message="; ".join(
                        (
                            f"{target.address} -> "
                            f"{target.target_kind or 'unknown'}/"
                            f"{target.target_name or 'unknown'} "
                            f"({'ready' if target.ready else 'not-ready'})"
                        )
                        for target in endpoints.targets
                    ),
                )
            )
        findings.append(
            Finding(
                severity=FindingSeverity.WARNING,
                resource_kind="Service",
                resource_name=service.name,
                summary="Service 没有 Ready Endpoint",
                evidence=tuple(evidence),
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


def _is_resource_pressure_eviction(
    reason: str | None,
    message: str | None,
) -> bool:
    normalized = (message or "").casefold().lstrip()
    storage_limit_eviction = (
        normalized.startswith("container ")
        and " exceeded its local ephemeral storage limit" in normalized
    ) or (
        normalized.startswith("usage of emptydir volume ")
        and " exceeds the limit" in normalized
    )
    return reason == "Evicted" and (
        _contains_marker(normalized, _EVICTION_RESOURCE_MARKERS)
        or storage_limit_eviction
    )


def _contains_marker(message: str | None, markers: tuple[str, ...]) -> bool:
    normalized = (message or "").casefold()
    return any(marker in normalized for marker in markers)


def _pod_status_evidence(pod: PodSummary) -> Evidence:
    details = [f"phase={pod.phase}"]
    if pod.status_reason is not None:
        details.append(f"reason={pod.status_reason}")
    if pod.status_message is not None:
        details.append(f"message={pod.status_message}")
    return Evidence(source="pod_status", message=", ".join(details))


def _with_pod_resources(
    pod: PodSummary,
    evidence: Evidence,
) -> tuple[Evidence, ...]:
    resource_evidence = _pod_resource_evidence(pod)
    return (
        (evidence, resource_evidence) if resource_evidence is not None else (evidence,)
    )


def _pod_resource_evidence(pod: PodSummary) -> Evidence | None:
    if pod.qos_class is None and not pod.resources:
        return None
    container_resources = "; ".join(
        (
            (
                "init_container="
                if resource.container_type is ContainerResourceType.INIT
                else "container="
            )
            + f"{resource.name}, "
            "requests("
            f"cpu={resource.cpu_request or 'unset'}, "
            f"memory={resource.memory_request or 'unset'}, "
            "ephemeral-storage="
            f"{resource.ephemeral_storage_request or 'unset'}), "
            "limits("
            f"cpu={resource.cpu_limit or 'unset'}, "
            f"memory={resource.memory_limit or 'unset'}, "
            "ephemeral-storage="
            f"{resource.ephemeral_storage_limit or 'unset'})"
        )
        for resource in pod.resources
    )
    message = f"qos_class={pod.qos_class or 'unset'}"
    if container_resources:
        message += f"; {container_resources}"
    return Evidence(source="pod_resources", message=message)
