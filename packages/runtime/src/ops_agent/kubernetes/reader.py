import json
from collections.abc import Callable, Iterator, Mapping
from dataclasses import dataclass, field
from datetime import date, datetime
from threading import Event, Lock
from typing import Protocol, TypeVar

from kubernetes import config
from kubernetes import watch as kubernetes_watch
from kubernetes.client import (
    AppsV1Api,
    BatchV1Api,
    Configuration,
    CoreV1Api,
    DiscoveryV1Api,
    NetworkingV1Api,
)
from kubernetes.client.exceptions import ApiException
from kubernetes.config.config_exception import ConfigException
from kubernetes.stream import stream as kubernetes_stream
from urllib3.exceptions import HTTPError

from ops_agent.kubernetes.errors import KubernetesError
from ops_agent.kubernetes.models import (
    ContainerResourceSummary,
    ContainerResourceType,
    ContainerStatusSummary,
    ContainerSummary,
    ControllerReferenceSummary,
    CronJobSummary,
    DaemonSetSummary,
    DeploymentConditionSummary,
    DeploymentSummary,
    IngressSummary,
    JobSummary,
    KubernetesEventSummary,
    KubernetesResourceKind,
    PersistentVolumeClaimSummary,
    PodConditionSummary,
    PodDetails,
    PodSummary,
    ReplicaSetSummary,
    ServiceEndpointSource,
    ServiceEndpointSummary,
    ServiceEndpointTargetSummary,
    ServicePortSummary,
    ServiceSummary,
    StatefulSetSummary,
    VolumeDirectory,
    VolumeFilePreview,
)
from ops_agent.kubernetes.observations import (
    KubernetesChangeSignal,
    KubernetesWatchOutcome,
    KubernetesWatchResult,
)
from ops_agent.kubernetes.settings import KubernetesConnectionSettings
from ops_agent.kubernetes.storage import KubernetesStorageReader

Result = TypeVar("Result")


class _Watcher(Protocol):
    def stream(self, request: Callable[..., object], **kwargs: object) -> Iterator: ...

    def stop(self) -> None: ...


@dataclass
class _ServiceEndpointCounts:
    ready_addresses: int = 0
    not_ready_addresses: int = 0
    endpoint_slice_count: int = 0
    targets: list[ServiceEndpointTargetSummary] = field(default_factory=list)


class KubernetesReader:
    def __init__(
        self,
        core_api: CoreV1Api,
        apps_api: AppsV1Api,
        request_timeout_seconds: int,
        batch_api: BatchV1Api | None = None,
        networking_api: NetworkingV1Api | None = None,
        discovery_api: DiscoveryV1Api | None = None,
        pod_executor: Callable[..., str] | None = None,
        watch_factory: Callable[[], _Watcher] | None = None,
    ) -> None:
        self._core_api = core_api
        self._apps_api = apps_api
        self._batch_api = batch_api
        self._networking_api = networking_api
        self._discovery_api = discovery_api
        self._request_timeout_seconds = request_timeout_seconds
        self._watch_factory = watch_factory or kubernetes_watch.Watch
        self._watch_lock = Lock()
        self._active_watcher: _Watcher | None = None
        self._pod_resource_version: str | None = None
        self._storage = KubernetesStorageReader(
            core_api=core_api,
            request_timeout_seconds=request_timeout_seconds,
            pod_executor=pod_executor,
        )

    def list_pods(self, namespace: str) -> list[PodSummary]:
        response = self._request(
            f"查询 namespace '{namespace}' 的 Pod 失败",
            lambda: self._core_api.list_namespaced_pod(
                namespace=namespace,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        response_metadata = getattr(response, "metadata", None)
        self._pod_resource_version = getattr(
            response_metadata,
            "resource_version",
            self._pod_resource_version,
        )
        return [_to_pod_summary(pod) for pod in response.items]

    def wait_for_change(
        self,
        namespace: str,
        *,
        timeout_seconds: int,
        stop_event: Event | None = None,
    ) -> KubernetesWatchResult:
        """Wait for one Pod change as a bounded read-only invalidation signal."""
        if stop_event is not None and stop_event.is_set():
            return KubernetesWatchResult(
                outcome=KubernetesWatchOutcome.STOPPED,
            )
        watcher = self._watch_factory()
        with self._watch_lock:
            self._active_watcher = watcher
        kwargs: dict[str, object] = {
            "namespace": namespace,
            "timeout_seconds": timeout_seconds,
            "_request_timeout": timeout_seconds + self._request_timeout_seconds,
        }
        if self._pod_resource_version is not None:
            kwargs["resource_version"] = self._pod_resource_version
        try:
            if stop_event is not None and stop_event.is_set():
                return KubernetesWatchResult(
                    outcome=KubernetesWatchOutcome.STOPPED,
                )
            for event in watcher.stream(
                self._core_api.list_namespaced_pod,
                **kwargs,
            ):
                if stop_event is not None and stop_event.is_set():
                    return KubernetesWatchResult(
                        outcome=KubernetesWatchOutcome.STOPPED,
                    )
                event_type = str(event.get("type", "UNKNOWN"))
                resource = event.get("object")
                metadata = getattr(resource, "metadata", None)
                resource_version = getattr(metadata, "resource_version", None)
                if resource_version is not None:
                    self._pod_resource_version = str(resource_version)
                return KubernetesWatchResult(
                    outcome=KubernetesWatchOutcome.CHANGED,
                    change=KubernetesChangeSignal(
                        resource_kind=KubernetesResourceKind.POD,
                        event_type=event_type,
                        resource_name=getattr(metadata, "name", None),
                    ),
                )
            return KubernetesWatchResult(
                outcome=KubernetesWatchOutcome.TIMED_OUT,
            )
        except (ApiException, HTTPError) as error:
            return KubernetesWatchResult(
                outcome=KubernetesWatchOutcome.UNAVAILABLE,
                unavailable_reason=str(error),
            )
        finally:
            with self._watch_lock:
                if self._active_watcher is watcher:
                    self._active_watcher = None
            watcher.stop()

    def stop_waiting_for_change(self) -> None:
        """Immediately stop the active bounded Watch request, if any."""
        with self._watch_lock:
            watcher = self._active_watcher
        if watcher is not None:
            watcher.stop()

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
        previous: bool = False,
    ) -> str:
        response = self._request(
            f"查询 Pod '{pod_name}' 日志失败",
            lambda: self._core_api.read_namespaced_pod_log(
                name=pod_name,
                namespace=namespace,
                container=container,
                tail_lines=tail_lines,
                timestamps=True,
                previous=previous,
                _request_timeout=self._request_timeout_seconds,
                _preload_content=False,
            ),
        )
        payload = getattr(response, "data", response)
        if isinstance(payload, bytes | bytearray):
            return bytes(payload).decode("utf-8", errors="replace")
        return str(payload)

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
                generation=getattr(deployment.metadata, "generation", None),
                observed_generation=getattr(
                    deployment.status,
                    "observed_generation",
                    None,
                ),
                revision=(getattr(deployment.metadata, "annotations", None) or {}).get(
                    "deployment.kubernetes.io/revision"
                ),
                conditions=tuple(
                    DeploymentConditionSummary(
                        type=condition.type,
                        status=condition.status,
                        reason=condition.reason,
                        message=condition.message,
                    )
                    for condition in (
                        getattr(deployment.status, "conditions", None) or []
                    )
                ),
            )
            for deployment in response.items
        ]

    def list_stateful_sets(
        self,
        namespace: str,
    ) -> list[StatefulSetSummary]:
        response = self._request(
            f"查询 namespace '{namespace}' 的 StatefulSet 失败",
            lambda: self._apps_api.list_namespaced_stateful_set(
                namespace=namespace,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        return [
            StatefulSetSummary(
                name=stateful_set.metadata.name,
                desired_replicas=stateful_set.spec.replicas or 0,
                ready_replicas=stateful_set.status.ready_replicas or 0,
                current_replicas=stateful_set.status.current_replicas or 0,
                updated_replicas=stateful_set.status.updated_replicas or 0,
            )
            for stateful_set in response.items
        ]

    def list_daemon_sets(
        self,
        namespace: str,
    ) -> list[DaemonSetSummary]:
        response = self._request(
            f"查询 namespace '{namespace}' 的 DaemonSet 失败",
            lambda: self._apps_api.list_namespaced_daemon_set(
                namespace=namespace,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        return [
            DaemonSetSummary(
                name=daemon_set.metadata.name,
                desired_scheduled=(daemon_set.status.desired_number_scheduled or 0),
                current_scheduled=(daemon_set.status.current_number_scheduled or 0),
                ready_scheduled=daemon_set.status.number_ready or 0,
                available_scheduled=daemon_set.status.number_available or 0,
            )
            for daemon_set in response.items
        ]

    def list_replica_sets(
        self,
        namespace: str,
    ) -> list[ReplicaSetSummary]:
        response = self._request(
            f"查询 namespace '{namespace}' 的 ReplicaSet 失败",
            lambda: self._apps_api.list_namespaced_replica_set(
                namespace=namespace,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        return [
            ReplicaSetSummary(
                name=replica_set.metadata.name,
                desired_replicas=replica_set.spec.replicas or 0,
                current_replicas=replica_set.status.replicas or 0,
                ready_replicas=replica_set.status.ready_replicas or 0,
                revision=(getattr(replica_set.metadata, "annotations", None) or {}).get(
                    "deployment.kubernetes.io/revision"
                ),
                controller=_to_controller_reference(replica_set.metadata),
            )
            for replica_set in response.items
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

    def list_service_endpoints(
        self,
        namespace: str,
    ) -> list[ServiceEndpointSummary]:
        discovery_api = self._require_api(self._discovery_api, "DiscoveryV1Api")
        try:
            response = self._request(
                f"查询 namespace '{namespace}' 的 EndpointSlice 失败",
                lambda: discovery_api.list_namespaced_endpoint_slice(
                    namespace=namespace,
                    _request_timeout=self._request_timeout_seconds,
                ),
            )
        except KubernetesError as error:
            cause = error.__cause__
            if isinstance(cause, ApiException) and cause.status == 404:
                return self._list_legacy_service_endpoints(namespace)
            raise
        totals: dict[str, _ServiceEndpointCounts] = {}
        for endpoint_slice in response.items:
            labels = endpoint_slice.metadata.labels or {}
            service_name = labels.get("kubernetes.io/service-name")
            if not service_name:
                continue
            summary = totals.setdefault(service_name, _ServiceEndpointCounts())
            summary.endpoint_slice_count += 1
            for endpoint in endpoint_slice.endpoints or []:
                addresses = endpoint.addresses or []
                address_count = len(addresses)
                conditions = getattr(endpoint, "conditions", None)
                ready = getattr(conditions, "ready", None) is not False
                if not ready:
                    summary.not_ready_addresses += address_count
                else:
                    summary.ready_addresses += address_count
                target_ref = getattr(endpoint, "target_ref", None)
                summary.targets.extend(
                    ServiceEndpointTargetSummary(
                        address=address,
                        ready=ready,
                        target_kind=getattr(target_ref, "kind", None),
                        target_name=getattr(target_ref, "name", None),
                    )
                    for address in addresses
                )
        return [
            ServiceEndpointSummary(
                service_name=service_name,
                ready_addresses=counts.ready_addresses,
                not_ready_addresses=counts.not_ready_addresses,
                endpoint_slice_count=counts.endpoint_slice_count,
                source=ServiceEndpointSource.ENDPOINT_SLICE,
                targets=tuple(counts.targets),
            )
            for service_name, counts in sorted(totals.items())
        ]

    def _list_legacy_service_endpoints(
        self,
        namespace: str,
    ) -> list[ServiceEndpointSummary]:
        response = self._request(
            f"查询 namespace '{namespace}' 的 Endpoints 失败",
            lambda: self._core_api.list_namespaced_endpoints(
                namespace=namespace,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        return [
            ServiceEndpointSummary(
                service_name=endpoints.metadata.name,
                ready_addresses=sum(
                    len(subset.addresses or []) for subset in (endpoints.subsets or [])
                ),
                not_ready_addresses=sum(
                    len(subset.not_ready_addresses or [])
                    for subset in (endpoints.subsets or [])
                ),
                endpoint_slice_count=0,
                source=ServiceEndpointSource.ENDPOINTS,
                targets=tuple(
                    ServiceEndpointTargetSummary(
                        address=address.ip,
                        ready=ready,
                        target_kind=getattr(
                            getattr(address, "target_ref", None),
                            "kind",
                            None,
                        ),
                        target_name=getattr(
                            getattr(address, "target_ref", None),
                            "name",
                            None,
                        ),
                    )
                    for subset in (endpoints.subsets or [])
                    for ready, addresses in (
                        (True, subset.addresses or []),
                        (False, subset.not_ready_addresses or []),
                    )
                    for address in addresses
                ),
            )
            for endpoints in sorted(response.items, key=lambda item: item.metadata.name)
        ]

    def list_jobs(self, namespace: str) -> list[JobSummary]:
        batch_api = self._require_api(self._batch_api, "BatchV1Api")
        response = self._request(
            f"查询 namespace '{namespace}' 的 Job 失败",
            lambda: batch_api.list_namespaced_job(
                namespace=namespace,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        return [
            JobSummary(
                name=job.metadata.name,
                completions=job.spec.completions or 1,
                succeeded=job.status.succeeded or 0,
                active=job.status.active or 0,
                failed=job.status.failed or 0,
            )
            for job in response.items
        ]

    def list_cron_jobs(self, namespace: str) -> list[CronJobSummary]:
        batch_api = self._require_api(self._batch_api, "BatchV1Api")
        response = self._request(
            f"查询 namespace '{namespace}' 的 CronJob 失败",
            lambda: batch_api.list_namespaced_cron_job(
                namespace=namespace,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        return [
            CronJobSummary(
                name=cron_job.metadata.name,
                schedule=cron_job.spec.schedule,
                suspended=bool(cron_job.spec.suspend),
                active=len(cron_job.status.active or []),
                last_schedule_time=_isoformat(cron_job.status.last_schedule_time),
            )
            for cron_job in response.items
        ]

    def list_ingresses(self, namespace: str) -> list[IngressSummary]:
        networking_api = self._require_api(
            self._networking_api,
            "NetworkingV1Api",
        )
        response = self._request(
            f"查询 namespace '{namespace}' 的 Ingress 失败",
            lambda: networking_api.list_namespaced_ingress(
                namespace=namespace,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        return [_to_ingress_summary(ingress) for ingress in response.items]

    def list_persistent_volume_claims(
        self,
        namespace: str,
    ) -> list[PersistentVolumeClaimSummary]:
        return self._storage.list_claims(namespace)

    def browse_persistent_volume_claim(
        self,
        namespace: str,
        claim_name: str,
        *,
        path: str,
    ) -> VolumeDirectory:
        return self._storage.browse(
            namespace,
            claim_name,
            path=path,
        )

    def preview_persistent_volume_claim_file(
        self,
        namespace: str,
        claim_name: str,
        *,
        path: str,
        max_bytes: int,
    ) -> VolumeFilePreview:
        return self._storage.preview(
            namespace,
            claim_name,
            path=path,
            max_bytes=max_bytes,
        )

    def describe_resource(
        self,
        namespace: str,
        kind: KubernetesResourceKind,
        name: str,
    ) -> str:
        reader = self._resource_reader(kind)
        resource = self._request(
            f"查询 {kind} '{name}' 详情失败",
            lambda: reader(
                name=name,
                namespace=namespace,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        related_events_error = None
        try:
            events = self._resource_events(namespace, kind, name)
        except KubernetesError as error:
            events = []
            related_events_error = str(error)
        return _format_description(
            namespace=namespace,
            kind=kind,
            name=name,
            resource=resource,
            events=events,
            related_events_error=related_events_error,
        )

    def _resource_reader(self, kind: KubernetesResourceKind):
        match kind:
            case KubernetesResourceKind.POD:
                return self._core_api.read_namespaced_pod
            case KubernetesResourceKind.SERVICE:
                return self._core_api.read_namespaced_service
            case KubernetesResourceKind.DEPLOYMENT:
                return self._apps_api.read_namespaced_deployment
            case KubernetesResourceKind.STATEFUL_SET:
                return self._apps_api.read_namespaced_stateful_set
            case KubernetesResourceKind.DAEMON_SET:
                return self._apps_api.read_namespaced_daemon_set
            case KubernetesResourceKind.REPLICA_SET:
                return self._apps_api.read_namespaced_replica_set
            case KubernetesResourceKind.JOB:
                batch_api = self._require_api(self._batch_api, "BatchV1Api")
                return batch_api.read_namespaced_job
            case KubernetesResourceKind.CRON_JOB:
                batch_api = self._require_api(self._batch_api, "BatchV1Api")
                return batch_api.read_namespaced_cron_job
            case KubernetesResourceKind.INGRESS:
                networking_api = self._require_api(
                    self._networking_api,
                    "NetworkingV1Api",
                )
                return networking_api.read_namespaced_ingress
            case KubernetesResourceKind.PERSISTENT_VOLUME_CLAIM:
                return self._core_api.read_namespaced_persistent_volume_claim

    def _resource_events(
        self,
        namespace: str,
        kind: KubernetesResourceKind,
        name: str,
    ) -> list[object]:
        response = self._request(
            f"查询 {kind} '{name}' 关联 Event 失败",
            lambda: self._core_api.list_namespaced_event(
                namespace=namespace,
                field_selector=f"involvedObject.kind={kind},involvedObject.name={name}",
                limit=100,
                _request_timeout=self._request_timeout_seconds,
            ),
        )
        return list(response.items)

    @staticmethod
    def _require_api(api, name: str):
        if api is None:
            raise KubernetesError(f"{name} 未配置")
        return api

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
    settings: KubernetesConnectionSettings,
) -> KubernetesReader:
    client_configuration = Configuration()
    if settings.proxy_url is not None:
        client_configuration.proxy = str(settings.proxy_url)
    kubeconfig_path = settings.kubeconfig_path.expanduser()
    try:
        api_client = config.new_client_from_config(
            config_file=str(kubeconfig_path),
            persist_config=False,
            client_configuration=client_configuration,
        )
    except ConfigException as error:
        raise KubernetesError(f"无法加载 kubeconfig: {kubeconfig_path}") from error
    core_api = CoreV1Api(api_client)

    def execute_pod_command(**kwargs) -> str:
        return kubernetes_stream(
            core_api.connect_get_namespaced_pod_exec,
            **kwargs,
        )

    return KubernetesReader(
        core_api=core_api,
        apps_api=AppsV1Api(api_client),
        batch_api=BatchV1Api(api_client),
        networking_api=NetworkingV1Api(api_client),
        discovery_api=DiscoveryV1Api(api_client),
        request_timeout_seconds=settings.request_timeout_seconds,
        pod_executor=execute_pod_command,
    )


def _to_pod_summary(pod) -> PodSummary:
    statuses = pod.status.container_statuses or []
    containers = getattr(pod.spec, "containers", None) or []
    init_containers = getattr(pod.spec, "init_containers", None) or []
    return PodSummary(
        name=pod.metadata.name,
        phase=pod.status.phase,
        restart_count=sum(status.restart_count for status in statuses),
        ready_containers=sum(bool(status.ready) for status in statuses),
        total_containers=len(containers),
        created_at=pod.metadata.creation_timestamp,
        container_statuses=tuple(
            _to_container_status_summary(status)
            for status in statuses
            if getattr(status, "name", None)
        ),
        conditions=tuple(
            PodConditionSummary(
                type=condition.type,
                status=condition.status,
                reason=condition.reason,
                message=condition.message,
            )
            for condition in (getattr(pod.status, "conditions", None) or [])
        ),
        controller=_to_controller_reference(pod.metadata),
        status_reason=getattr(pod.status, "reason", None),
        status_message=getattr(pod.status, "message", None),
        qos_class=getattr(pod.status, "qos_class", None),
        resources=tuple(
            _to_container_resource_summary(
                container,
                container_type=ContainerResourceType.APP,
            )
            for container in containers
            if getattr(container, "name", None)
        )
        + tuple(
            _to_container_resource_summary(
                container,
                container_type=ContainerResourceType.INIT,
            )
            for container in init_containers
            if getattr(container, "name", None)
        ),
    )


def _to_container_resource_summary(
    container,
    *,
    container_type: ContainerResourceType,
) -> ContainerResourceSummary:
    resources = getattr(container, "resources", None)
    requests = getattr(resources, "requests", None) or {}
    limits = getattr(resources, "limits", None) or {}
    return ContainerResourceSummary(
        name=container.name,
        container_type=container_type,
        cpu_request=_resource_quantity(requests, "cpu"),
        cpu_limit=_resource_quantity(limits, "cpu"),
        memory_request=_resource_quantity(requests, "memory"),
        memory_limit=_resource_quantity(limits, "memory"),
        ephemeral_storage_request=_resource_quantity(
            requests,
            "ephemeral-storage",
        ),
        ephemeral_storage_limit=_resource_quantity(
            limits,
            "ephemeral-storage",
        ),
    )


def _resource_quantity(values: object, name: str) -> str | None:
    if not isinstance(values, Mapping):
        return None
    value = values.get(name)
    return str(value) if value is not None else None


def _to_controller_reference(metadata) -> ControllerReferenceSummary | None:
    for reference in getattr(metadata, "owner_references", None) or []:
        if bool(reference.controller):
            return ControllerReferenceSummary(
                kind=reference.kind,
                name=reference.name,
            )
    return None


def _to_container_status_summary(status) -> ContainerStatusSummary:
    state, reason, exit_code = _container_state_observation(status.state)
    _, previous_reason, previous_exit_code = _container_state_observation(
        getattr(status, "last_state", None)
    )
    return ContainerStatusSummary(
        name=status.name,
        ready=bool(status.ready),
        restart_count=status.restart_count or 0,
        state=state,
        reason=reason,
        exit_code=exit_code,
        previous_reason=previous_reason,
        previous_exit_code=previous_exit_code,
    )


def _container_state_observation(state) -> tuple[str, str | None, int | None]:
    if state is None:
        return "unknown", None, None
    if getattr(state, "running", None) is not None:
        return "running", None, None
    waiting = getattr(state, "waiting", None)
    if waiting is not None:
        return "waiting", waiting.reason, None
    terminated = getattr(state, "terminated", None)
    if terminated is not None:
        return "terminated", terminated.reason, terminated.exit_code
    return "unknown", None, None


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


def _to_ingress_summary(ingress) -> IngressSummary:
    load_balancers = (
        ingress.status.load_balancer.ingress
        if ingress.status.load_balancer is not None
        else None
    )
    addresses = tuple(
        address
        for item in (load_balancers or [])
        for address in (item.ip, item.hostname)
        if address
    )
    return IngressSummary(
        name=ingress.metadata.name,
        ingress_class=ingress.spec.ingress_class_name,
        hosts=tuple(
            rule.host for rule in (ingress.spec.rules or []) if rule.host is not None
        ),
        addresses=addresses,
    )


def _isoformat(value: datetime | None) -> str | None:
    return value.isoformat() if value is not None else None


def _to_serializable(value):
    if value is None or isinstance(value, str | int | float | bool):
        return value
    if isinstance(value, date | datetime):
        return value.isoformat()
    if isinstance(value, list | tuple):
        return [_to_serializable(item) for item in value]
    if isinstance(value, dict):
        return {
            key: _to_serializable(item)
            for key, item in value.items()
            if item is not None
        }
    if hasattr(value, "to_dict"):
        return _to_serializable(value.to_dict())
    if hasattr(value, "__dict__"):
        return {
            key: _to_serializable(item)
            for key, item in vars(value).items()
            if item is not None
        }
    return str(value)


def _format_description(
    *,
    namespace: str,
    kind: KubernetesResourceKind,
    name: str,
    resource,
    events: list[object],
    related_events_error: str | None,
) -> str:
    details = json.dumps(
        _to_serializable(resource),
        ensure_ascii=False,
        indent=2,
    )
    lines = [
        f"Name:       {name}",
        f"Namespace:  {namespace}",
        f"Kind:       {kind}",
        "",
        "Resource details",
        "----------------",
        details,
        "",
        "Related events",
        "--------------",
    ]
    if related_events_error is not None:
        lines.append(f"Unavailable: {related_events_error}")
    elif not events:
        lines.append("No related events.")
    else:
        for event in events:
            summary = _to_event_summary(event)
            header = (
                f"{summary.type} {summary.reason} "
                f"(count={summary.count}, last={summary.last_seen or '-'})"
            )
            lines.extend(
                (
                    header,
                    f"  {summary.message}",
                )
            )
    return "\n".join(lines)
