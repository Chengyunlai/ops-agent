from dataclasses import asdict
from typing import Protocol

from langchain_core.tools import BaseTool, tool

from ops_agent.diagnostics import (
    KubernetesSnapshot,
    diagnose_kubernetes_snapshot,
)
from ops_agent.kubernetes import (
    DeploymentSummary,
    KubernetesEventSummary,
    PodDetails,
    PodSummary,
    ReplicaSetSummary,
    ServiceEndpointSummary,
    ServiceSummary,
)


class KubernetesOperations(Protocol):
    def list_pods(self, namespace: str) -> list[PodSummary]: ...

    def get_pod_details(
        self,
        namespace: str,
        pod_name: str,
    ) -> PodDetails: ...

    def get_pod_logs(
        self,
        namespace: str,
        pod_name: str,
        *,
        container: str | None,
        tail_lines: int,
        previous: bool,
    ) -> str: ...

    def list_events(
        self,
        namespace: str,
        *,
        pod_name: str | None,
        limit: int,
    ) -> list[KubernetesEventSummary]: ...

    def list_deployments(
        self,
        namespace: str,
    ) -> list[DeploymentSummary]: ...

    def list_replica_sets(
        self,
        namespace: str,
    ) -> list[ReplicaSetSummary]: ...

    def list_services(
        self,
        namespace: str,
    ) -> list[ServiceSummary]: ...

    def list_service_endpoints(
        self,
        namespace: str,
    ) -> list[ServiceEndpointSummary]: ...


def create_kubernetes_tools(
    reader: KubernetesOperations,
    *,
    namespace: str,
) -> list[BaseTool]:
    @tool("list_kubernetes_pods")
    def list_kubernetes_pods() -> list[dict[str, object]]:
        """列出已配置 namespace 的 Pod 状态、就绪数和重启次数。"""
        return [asdict(pod) for pod in reader.list_pods(namespace)]

    @tool("diagnose_kubernetes_workloads")
    def diagnose_kubernetes_workloads() -> dict[str, object]:
        """诊断 Pod、Deployment rollout、所属 ReplicaSet 和 Service 健康状态。"""
        snapshot = KubernetesSnapshot(
            namespace=namespace,
            pods=tuple(reader.list_pods(namespace)),
            deployments=tuple(reader.list_deployments(namespace)),
            replica_sets=tuple(reader.list_replica_sets(namespace)),
            services=tuple(reader.list_services(namespace)),
            service_endpoints=tuple(reader.list_service_endpoints(namespace)),
        )
        return asdict(diagnose_kubernetes_snapshot(snapshot))

    @tool("get_kubernetes_pod_details")
    def get_kubernetes_pod_details(
        pod_name: str,
    ) -> dict[str, object]:
        """查询指定 Pod 的节点、IP、容器镜像、状态和重启次数。"""
        return asdict(reader.get_pod_details(namespace, pod_name))

    @tool("get_kubernetes_pod_logs")
    def get_kubernetes_pod_logs(
        pod_name: str,
        container: str | None = None,
        tail_lines: int = 200,
        previous: bool = False,
    ) -> dict[str, object]:
        """读取指定 Pod 最近的日志。

        tail_lines 必须在 1 到 1000 之间。多容器 Pod 可指定 container；
        previous=true 可读取上一个已终止容器实例的日志。
        """
        _require_range(tail_lines, "tail_lines", minimum=1, maximum=1000)
        logs = reader.get_pod_logs(
            namespace,
            pod_name,
            container=container,
            tail_lines=tail_lines,
            previous=previous,
        )
        return {
            "pod_name": pod_name,
            "container": container,
            "tail_lines": tail_lines,
            "previous": previous,
            "logs": logs,
        }

    @tool("list_kubernetes_events")
    def list_kubernetes_events(
        pod_name: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        """列出已配置 namespace 的 Event。

        可用 pod_name 只查询某个 Pod，limit 必须在 1 到 200 之间。
        """
        _require_range(limit, "limit", minimum=1, maximum=200)
        events = reader.list_events(
            namespace,
            pod_name=pod_name,
            limit=limit,
        )
        return [asdict(event) for event in events]

    @tool("list_kubernetes_deployments")
    def list_kubernetes_deployments() -> list[dict[str, object]]:
        """列出已配置 namespace 的 Deployment 副本状态。"""
        return [asdict(deployment) for deployment in reader.list_deployments(namespace)]

    @tool("list_kubernetes_services")
    def list_kubernetes_services() -> list[dict[str, object]]:
        """列出已配置 namespace 的 Service、ClusterIP 和端口。"""
        return [asdict(service) for service in reader.list_services(namespace)]

    @tool("list_kubernetes_service_endpoints")
    def list_kubernetes_service_endpoints() -> list[dict[str, object]]:
        """列出已配置 namespace 的 Service Endpoint 就绪统计。"""
        return [
            asdict(endpoints) for endpoints in reader.list_service_endpoints(namespace)
        ]

    return [
        list_kubernetes_pods,
        diagnose_kubernetes_workloads,
        get_kubernetes_pod_details,
        get_kubernetes_pod_logs,
        list_kubernetes_events,
        list_kubernetes_deployments,
        list_kubernetes_services,
        list_kubernetes_service_endpoints,
    ]


def _require_range(
    value: int,
    field_name: str,
    *,
    minimum: int,
    maximum: int,
) -> None:
    if (
        not isinstance(value, int)
        or isinstance(value, bool)
        or not minimum <= value <= maximum
    ):
        raise ValueError(f"{field_name} 必须在 {minimum} 到 {maximum} 之间")
