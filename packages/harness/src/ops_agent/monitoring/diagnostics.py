"""把确定性 DiagnosisReport 投影为 Monitoring 健康与拓扑模型。"""

from ops_agent.diagnostics import Finding
from ops_agent.kubernetes import (
    DeploymentSummary,
    KubernetesResourceKind,
    PodSummary,
    ReplicaSetSummary,
)
from ops_agent.monitoring.models import (
    KubernetesDeploymentTopology,
    KubernetesMonitorSnapshot,
    KubernetesPodTopology,
    KubernetesReplicaSetTopology,
    KubernetesResourceCollection,
    KubernetesResourceDiagnostic,
    KubernetesResourceRef,
    KubernetesResourceRow,
)


def to_monitor_diagnostic(finding: Finding) -> KubernetesResourceDiagnostic:
    resource_kind = KubernetesResourceKind(finding.resource_kind)
    return KubernetesResourceDiagnostic(
        ref=KubernetesResourceRef(
            kind=resource_kind,
            name=finding.resource_name,
        ),
        severity=finding.severity,
        summary=finding.summary,
        evidence=tuple(f"{item.source}: {item.message}" for item in finding.evidence),
    )


def health_reasons_by_resource(
    diagnostics: tuple[KubernetesResourceDiagnostic, ...],
) -> dict[KubernetesResourceRef, tuple[str, ...]]:
    reasons: dict[KubernetesResourceRef, list[str]] = {}
    for diagnostic in diagnostics:
        summaries = reasons.setdefault(diagnostic.ref, [])
        if diagnostic.summary not in summaries:
            summaries.append(diagnostic.summary)
    return {resource: tuple(items) for resource, items in reasons.items()}


def with_health_reasons(
    collection: KubernetesResourceCollection,
    reasons: dict[KubernetesResourceRef, tuple[str, ...]],
) -> KubernetesResourceCollection:
    return KubernetesResourceCollection(
        kind=collection.kind,
        label=collection.label,
        shortcut=collection.shortcut,
        columns=collection.columns,
        rows=tuple(
            KubernetesResourceRow(
                ref=row.ref,
                values=row.values,
                healthy=False if reasons.get(row.ref) else row.healthy,
                health_reasons=reasons.get(row.ref, ()),
            )
            for row in collection.rows
        ),
        error=collection.error,
    )


def deployment_topologies(
    *,
    deployments: tuple[DeploymentSummary, ...],
    replica_sets: tuple[ReplicaSetSummary, ...],
    pods: tuple[PodSummary, ...],
) -> tuple[KubernetesDeploymentTopology, ...]:
    return tuple(
        _deployment_topology(
            deployment,
            replica_sets=replica_sets,
            pods=pods,
        )
        for deployment in deployments
    )


def _deployment_topology(
    deployment: DeploymentSummary,
    *,
    replica_sets: tuple[ReplicaSetSummary, ...],
    pods: tuple[PodSummary, ...],
) -> KubernetesDeploymentTopology:
    owned_replica_sets = sorted(
        (
            replica_set
            for replica_set in replica_sets
            if replica_set.controller is not None
            and replica_set.controller.kind == "Deployment"
            and replica_set.controller.name == deployment.name
        ),
        key=lambda item: item.name,
    )
    return KubernetesDeploymentTopology(
        ref=KubernetesResourceRef(
            KubernetesResourceKind.DEPLOYMENT,
            deployment.name,
        ),
        generation=deployment.generation,
        observed_generation=deployment.observed_generation,
        revision=deployment.revision,
        conditions=tuple(
            (
                f"{condition.type}={condition.status}"
                f" · {condition.reason or '-'}"
                f" · {condition.message or '-'}"
            )
            for condition in deployment.conditions
        ),
        replica_sets=tuple(
            KubernetesReplicaSetTopology(
                name=replica_set.name,
                desired_replicas=replica_set.desired_replicas,
                ready_replicas=replica_set.ready_replicas,
                revision=replica_set.revision,
                pods=tuple(
                    KubernetesPodTopology(
                        name=pod.name,
                        owner_name=replica_set.name,
                        phase=pod.phase,
                    )
                    for pod in sorted(pods, key=lambda item: item.name)
                    if pod.controller is not None
                    and pod.controller.kind == "ReplicaSet"
                    and pod.controller.name == replica_set.name
                ),
            )
            for replica_set in owned_replica_sets
        ),
    )


def format_resource_diagnostics(
    snapshot: KubernetesMonitorSnapshot,
    resource: KubernetesResourceRef,
) -> str:
    diagnostics = snapshot.diagnostics_for(resource)
    lines = [f"Resource: {resource.kind}/{resource.name}", ""]
    if diagnostics:
        lines.append(f"Findings ({len(diagnostics)}):")
        for diagnostic in diagnostics:
            lines.append(f"  ! {diagnostic.summary}")
            lines.extend(f"      {evidence}" for evidence in diagnostic.evidence)
    else:
        lines.append("Findings: No deterministic warning in the latest snapshot")

    topology = snapshot.deployment_topology(resource)
    if topology is not None:
        lines.extend(
            (
                "",
                "Rollout:",
                "  Generation: {} · Observed: {} · Revision: {}".format(
                    topology.generation or "-",
                    topology.observed_generation or "-",
                    topology.revision or "-",
                ),
                "  Conditions:",
            )
        )
        lines.extend(
            (f"    {condition}" for condition in topology.conditions)
            if topology.conditions
            else ("    none",)
        )
        lines.append("  Topology:")
        if not topology.replica_sets:
            lines.append("    no controller-owned ReplicaSet observed")
        for replica_set in topology.replica_sets:
            lines.append(
                f"    ReplicaSet/{replica_set.name}"
                f" · desired {replica_set.desired_replicas}"
                f" · ready {replica_set.ready_replicas}"
                f" · revision {replica_set.revision or '-'}"
            )
            lines.extend(
                (
                    f"      Pod/{pod.name} · owner {pod.owner_name} · phase {pod.phase}"
                    for pod in replica_set.pods
                )
                if replica_set.pods
                else ("      no controller-owned Pod observed",)
            )

    service_endpoint = snapshot.service_endpoint(resource)
    if service_endpoint is not None:
        lines.extend(
            (
                "",
                "Endpoints:",
                f"  Source: {service_endpoint.source.value} · Ready: {service_endpoint.ready_addresses} · NotReady: {service_endpoint.not_ready_addresses}",
            )
        )
        lines.extend(
            (
                f"  {target.address} -> "
                f"{target.target_kind or 'unknown'}/"
                f"{target.target_name or 'unknown'}"
                f" · {'ready' if target.ready else 'not-ready'}"
                for target in service_endpoint.targets
            )
            if service_endpoint.targets
            else ("  no endpoint target observed",)
        )

    if snapshot.diagnostic_errors:
        lines.extend(("", "Partial diagnostics:"))
        lines.extend(f"  {error}" for error in snapshot.diagnostic_errors)
    return "\n".join(lines)
