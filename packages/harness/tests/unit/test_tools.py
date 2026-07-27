import pytest
from ops_agent.kubernetes import (
    ContainerSummary,
    DeploymentSummary,
    KubernetesEventSummary,
    PodDetails,
    PodSummary,
    ServiceSummary,
)
from ops_agent.tools import create_kubernetes_tools


class FakeKubernetesOperations:
    def __init__(self) -> None:
        self.calls: list[tuple[str, object]] = []

    def list_pods(self, namespace: str) -> list[PodSummary]:
        self.calls.append(("list_pods", namespace))
        return [
            PodSummary(
                name="sample-api",
                phase="Running",
                restart_count=2,
                ready_containers=1,
                total_containers=1,
            )
        ]

    def get_pod_details(
        self,
        namespace: str,
        pod_name: str,
    ) -> PodDetails:
        self.calls.append(("get_pod_details", (namespace, pod_name)))
        return PodDetails(
            name=pod_name,
            phase="Running",
            pod_ip="10.42.0.8",
            node_name="worker-1",
            containers=[
                ContainerSummary(
                    name="api",
                    image="sample-api:v1",
                    ready=True,
                    restart_count=2,
                    state="running",
                )
            ],
        )

    def get_pod_logs(
        self,
        namespace: str,
        pod_name: str,
        *,
        container: str | None,
        tail_lines: int,
    ) -> str:
        self.calls.append(
            (
                "get_pod_logs",
                (namespace, pod_name, container, tail_lines),
            )
        )
        return "server started"

    def list_events(
        self,
        namespace: str,
        *,
        pod_name: str | None,
        limit: int,
    ) -> list[KubernetesEventSummary]:
        self.calls.append(("list_events", (namespace, pod_name, limit)))
        return [
            KubernetesEventSummary(
                type="Warning",
                reason="BackOff",
                message="container restarting",
                object_kind="Pod",
                object_name=pod_name or "sample-api",
                count=2,
                last_seen=None,
            )
        ]

    def list_deployments(
        self,
        namespace: str,
    ) -> list[DeploymentSummary]:
        self.calls.append(("list_deployments", namespace))
        return [
            DeploymentSummary(
                name="sample-api",
                desired_replicas=3,
                ready_replicas=2,
                available_replicas=2,
                updated_replicas=3,
            )
        ]

    def list_services(self, namespace: str) -> list[ServiceSummary]:
        self.calls.append(("list_services", namespace))
        return [
            ServiceSummary(
                name="sample-api",
                type="ClusterIP",
                cluster_ip="10.43.0.10",
                ports=[],
            )
        ]


def create_tools():
    operations = FakeKubernetesOperations()
    tools = {
        tool.name: tool
        for tool in create_kubernetes_tools(
            operations,
            namespace="sample",
        )
    }
    return operations, tools


def test_kubernetes_tools_expose_read_only_operations() -> None:
    _, tools = create_tools()

    assert set(tools) == {
        "list_kubernetes_pods",
        "get_kubernetes_pod_details",
        "get_kubernetes_pod_logs",
        "list_kubernetes_events",
        "list_kubernetes_deployments",
        "list_kubernetes_services",
    }


def test_list_pods_tool_uses_configured_namespace() -> None:
    operations, tools = create_tools()

    result = tools["list_kubernetes_pods"].invoke({})

    assert operations.calls == [("list_pods", "sample")]
    assert result[0]["name"] == "sample-api"
    assert result[0]["ready_containers"] == 1


def test_pod_details_and_logs_tools_use_configured_namespace() -> None:
    operations, tools = create_tools()

    details = tools["get_kubernetes_pod_details"].invoke({"pod_name": "sample-api"})
    logs = tools["get_kubernetes_pod_logs"].invoke(
        {
            "pod_name": "sample-api",
            "container": "api",
            "tail_lines": 50,
        }
    )

    assert details["containers"][0]["state"] == "running"
    assert logs["logs"] == "server started"
    assert operations.calls == [
        ("get_pod_details", ("sample", "sample-api")),
        ("get_pod_logs", ("sample", "sample-api", "api", 50)),
    ]


def test_events_deployments_and_services_tools_are_structured() -> None:
    operations, tools = create_tools()

    events = tools["list_kubernetes_events"].invoke(
        {"pod_name": "sample-api", "limit": 20}
    )
    deployments = tools["list_kubernetes_deployments"].invoke({})
    services = tools["list_kubernetes_services"].invoke({})

    assert events[0]["reason"] == "BackOff"
    assert deployments[0]["ready_replicas"] == 2
    assert services[0]["cluster_ip"] == "10.43.0.10"
    assert operations.calls == [
        ("list_events", ("sample", "sample-api", 20)),
        ("list_deployments", "sample"),
        ("list_services", "sample"),
    ]


@pytest.mark.parametrize(
    ("tool_name", "arguments", "message"),
    [
        (
            "get_kubernetes_pod_logs",
            {"pod_name": "sample-api", "tail_lines": 1001},
            "tail_lines",
        ),
        (
            "get_kubernetes_pod_logs",
            {"pod_name": "sample-api", "tail_lines": 0},
            "tail_lines",
        ),
        (
            "list_kubernetes_events",
            {"limit": 201},
            "limit",
        ),
        (
            "list_kubernetes_events",
            {"limit": 0},
            "limit",
        ),
    ],
)
def test_kubernetes_tools_reject_unbounded_queries(
    tool_name: str,
    arguments: dict[str, object],
    message: str,
) -> None:
    _, tools = create_tools()

    with pytest.raises(ValueError, match=message):
        tools[tool_name].invoke(arguments)
