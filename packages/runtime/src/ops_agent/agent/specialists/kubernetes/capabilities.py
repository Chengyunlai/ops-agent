from collections.abc import Iterable

from ops_agent.agent.models import (
    CapabilityId,
    CapabilityRegistration,
    CapabilityRegistry,
    ResourceKind,
)

DIAGNOSTICS_TOOL = "diagnose_kubernetes_workloads"
POD_DETAILS_TOOL = "get_kubernetes_pod_details"
POD_LOGS_TOOL = "get_kubernetes_pod_logs"
DEPLOYMENTS_TOOL = "list_kubernetes_deployments"
EVENTS_TOOL = "list_kubernetes_events"
PODS_TOOL = "list_kubernetes_pods"
SERVICE_ENDPOINTS_TOOL = "list_kubernetes_service_endpoints"
SERVICES_TOOL = "list_kubernetes_services"

KUBERNETES_CAPABILITIES = (
    CapabilityRegistration(
        capability=CapabilityId.KUBERNETES_DIAGNOSTICS_READ,
        tool_names=frozenset(
            {
                DIAGNOSTICS_TOOL,
                POD_DETAILS_TOOL,
                POD_LOGS_TOOL,
                DEPLOYMENTS_TOOL,
                EVENTS_TOOL,
                PODS_TOOL,
                SERVICE_ENDPOINTS_TOOL,
                SERVICES_TOOL,
            }
        ),
    ),
    CapabilityRegistration(
        capability=CapabilityId.KUBERNETES_WORKLOADS_READ,
        resources=frozenset(
            {
                ResourceKind.KUBERNETES,
                ResourceKind.WORKLOAD,
            }
        ),
        tool_names=frozenset({DIAGNOSTICS_TOOL}),
    ),
    CapabilityRegistration(
        capability=CapabilityId.KUBERNETES_PODS_READ,
        resources=frozenset({ResourceKind.POD}),
        tool_names=frozenset({PODS_TOOL, POD_DETAILS_TOOL}),
    ),
    CapabilityRegistration(
        capability=CapabilityId.KUBERNETES_DEPLOYMENTS_READ,
        resources=frozenset({ResourceKind.DEPLOYMENT}),
        tool_names=frozenset({DEPLOYMENTS_TOOL}),
    ),
    CapabilityRegistration(
        capability=CapabilityId.KUBERNETES_SERVICES_READ,
        resources=frozenset({ResourceKind.SERVICE}),
        tool_names=frozenset({SERVICE_ENDPOINTS_TOOL, SERVICES_TOOL}),
    ),
    CapabilityRegistration(
        capability=CapabilityId.KUBERNETES_EVENTS_READ,
        resources=frozenset({ResourceKind.EVENT}),
        tool_names=frozenset({EVENTS_TOOL}),
    ),
    CapabilityRegistration(
        capability=CapabilityId.KUBERNETES_LOGS_READ,
        resources=frozenset({ResourceKind.LOG}),
        tool_names=frozenset({POD_LOGS_TOOL}),
    ),
)


def create_kubernetes_capability_registry(
    tool_names: Iterable[str],
) -> CapabilityRegistry:
    """把实际提供的 Kubernetes Tool 投影为可授权 Capability。"""

    available_tool_names = frozenset(tool_names)
    return CapabilityRegistry(
        registrations=KUBERNETES_CAPABILITIES,
        enabled=frozenset(
            registration.capability
            for registration in KUBERNETES_CAPABILITIES
            if registration.tool_names <= available_tool_names
        ),
    )
