from dataclasses import dataclass

from kubernetes import config
from kubernetes.client import CoreV1Api
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from urllib3.exceptions import HTTPError

from ops_agent.settings import KubernetesSettings


class KubernetesError(Exception):
    """Kubernetes 配置或查询失败。"""


@dataclass(frozen=True)
class PodSummary:
    name: str
    phase: str
    restart_count: int


class KubernetesReader:
    def __init__(
        self,
        api: CoreV1Api,
        request_timeout_seconds: int,
    ) -> None:
        self._api = api
        self._request_timeout_seconds = request_timeout_seconds

    def list_pods(self, namespace: str) -> list[PodSummary]:
        try:
            response = self._api.list_namespaced_pod(
                namespace=namespace,
                _request_timeout=self._request_timeout_seconds,
            )
        except (ApiException, HTTPError) as error:
            raise KubernetesError(
                f"查询 namespace '{namespace}' 的 Pod 失败: {error}"
            ) from error
        return [
            PodSummary(
                name=pod.metadata.name,
                phase=pod.status.phase,
                restart_count=sum(
                    container.restart_count
                    for container in (pod.status.container_statuses or [])
                ),
            )
            for pod in response.items
        ]


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
        api=CoreV1Api(api_client),
        request_timeout_seconds=settings.request_timeout_seconds,
    )
