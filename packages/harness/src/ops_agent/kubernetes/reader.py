from collections.abc import Callable
from datetime import datetime
from typing import TypeVar

from kubernetes import config
from kubernetes.client import AppsV1Api, CoreV1Api
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from urllib3.exceptions import HTTPError

from ops_agent.kubernetes.models import (
    ContainerSummary,
    DeploymentSummary,
    KubernetesEventSummary,
    PodDetails,
    PodSummary,
    ServicePortSummary,
    ServiceSummary,
)
from ops_agent.settings import KubernetesSettings


class KubernetesError(Exception):
    """Kubernetes 配置或查询失败。"""


Result = TypeVar("Result")


class KubernetesReader:
    def __init__(
        self,
        core_api: CoreV1Api,
        apps_api: AppsV1Api,
        request_timeout_seconds: int,
    ) -> None:
        self._core_api = core_api
        self._apps_api = apps_api
        self._request_timeout_seconds = request_timeout_seconds

    def list_pods(self, namespace: str) -> list[PodSummary]:
        response = self._request(
            f"查询 namespace '{namespace}' 的 Pod 失败",
            lambda: self._core_api.list_namespaced_pod(
                namespace=namespace,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        return [_to_pod_summary(pod) for pod in response.items]

    def get_pod_details(
        self,
        namespace: str,
        pod_name: str,
    ) -> PodDetails:
        pod = self._request(
            f"查询 Pod '{pod_name}' 详情失败",
            lambda: self._core_api.read_namespaced_pod(
                name=pod_name,
                namespace=namespace,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        return _to_pod_details(pod)

    def get_pod_logs(
        self,
        namespace: str,
        pod_name: str,
        *,
        container: str | None,
        tail_lines: int,
    ) -> str:
        return self._request(
            f"查询 Pod '{pod_name}' 日志失败",
            lambda: self._core_api.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=container,
                tail_lines=tail_lines,
                timestamps=True,
                _request_timeout=self._request_timeout_seconds,
            ),
        )

    def list_events(
        self,
        namespace: str,
        *,
        pod_name: str | None,
        limit: int,
    ) -> list[KubernetesEventSummary]:
        field_selector = (
            f"involvedObject.kind=Pod,involvedObject.name={pod_name}"
            if pod_name is not None
            else None
        )
        response = self._request(
            f"查询 namespace '{namespace}' 的 Event 失败",
            lambda: self._core_api.list_namespaced_event(
                namespace=namespace,
                field_selector=field_selector,
                limit=limit,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        return [_to_event_summary(event) for event in response.items]

    def list_deployments(
        self,
        namespace: str,
    ) -> list[DeploymentSummary]:
        response = self._request(
            f"查询 namespace '{namespace}' 的 Deployment 失败",
            lambda: self._apps_api.list_namespaced_deployment(
                namespace=namespace,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        return [
            DeploymentSummary(
                name=deployment.metadata.name,
                desired_replicas=deployment.spec.replicas or 0,
                ready_replicas=deployment.status.ready_replicas or 0,
                available_replicas=(deployment.status.available_replicas or 0),
                updated_replicas=deployment.status.updated_replicas or 0,
            )
            for deployment in response.items
        ]

    def list_services(self, namespace: str) -> list[ServiceSummary]:
        response = self._request(
            f"查询 namespace '{namespace}' 的 Service 失败",
            lambda: self._core_api.list_namespaced_service(
                namespace=namespace,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        return [_to_service_summary(service) for service in response.items]

    def _request(
        self,
        failure_message: str,
        request: Callable[[], Result],
    ) -> Result:
        try:
            return request()
        except (ApiException, HTTPError) as error:
            raise KubernetesError(f"{failure_message}: {error}") from error


def create_kubernetes_reader(
    settings: KubernetesSettings,
) -> KubernetesReader:
    try:
        api_client = config.new_client_from_config(
            config_file=str(settings.kubeconfig_path),
            persist_config=False,
        )
    except ConfigException as error:
        raise KubernetesError(
            f"无法加载 kubeconfig: {settings.kubeconfig_path}"
        ) from error
    return KubernetesReader(
        core_api=CoreV1Api(api_client),
        apps_api=AppsV1Api(api_client),
        request_timeout_seconds=settings.request_timeout_seconds,
    )


def _to_pod_summary(pod) -> PodSummary:
    statuses = pod.status.container_statuses or []
    containers = getattr(pod.spec, "containers", None) or []
    return PodSummary(
        name=pod.metadata.name,
        phase=pod.status.phase,
        restart_count=sum(status.restart_count for status in statuses),
        ready_containers=sum(bool(status.ready) for status in statuses),
        total_containers=len(containers),
    )


def _to_pod_details(pod) -> PodDetails:
    statuses = {status.name: status for status in (pod.status.container_statuses or [])}
    containers = [
        _to_container_summary(container, statuses.get(container.name))
        for container in (pod.spec.containers or [])
    ]
    return PodDetails(
        name=pod.metadata.name,
        phase=pod.status.phase,
        pod_ip=pod.status.pod_ip,
        node_name=pod.spec.node_name,
        containers=containers,
    )


def _to_container_summary(container, status) -> ContainerSummary:
    return ContainerSummary(
        name=container.name,
        image=container.image,
        ready=bool(status.ready) if status is not None else False,
        restart_count=(status.restart_count if status is not None else 0),
        state=_container_state(status),
    )


def _container_state(status) -> str:
    if status is None or status.state is None:
        return "unknown"
    if status.state.running is not None:
        return "running"
    if status.state.waiting is not None:
        reason = status.state.waiting.reason or "unknown"
        return f"waiting:{reason}"
    if status.state.terminated is not None:
        reason = (
            status.state.terminated.reason
            or f"exit_code_{status.state.terminated.exit_code}"
        )
        return f"terminated:{reason}"
    return "unknown"


def _to_event_summary(event) -> KubernetesEventSummary:
    involved_object = event.involved_object
    timestamp = (
        event.event_time or event.last_timestamp or event.metadata.creation_timestamp
    )
    return KubernetesEventSummary(
        type=event.type or "Unknown",
        reason=event.reason or "Unknown",
        message=event.message or "",
        object_kind=involved_object.kind or "Unknown",
        object_name=involved_object.name or "Unknown",
        count=event.count or 0,
        last_seen=_isoformat(timestamp),
    )


def _to_service_summary(service) -> ServiceSummary:
    return ServiceSummary(
        name=service.metadata.name,
        type=service.spec.type,
        cluster_ip=service.spec.cluster_ip,
        ports=[
            ServicePortSummary(
                name=port.name,
                port=port.port,
                protocol=port.protocol,
                target_port=str(port.target_port),
            )
            for port in (service.spec.ports or [])
        ],
    )


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None
