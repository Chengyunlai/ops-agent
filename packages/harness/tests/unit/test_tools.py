from ops_agent.kubernetes import PodSummary
from ops_agent.tools import create_kubernetes_tools


class FakeKubernetesReader:
    def __init__(self) -> None:
        self.namespaces: list[str] = []

    def list_pods(self, namespace: str) -> list[PodSummary]:
        self.namespaces.append(namespace)
        return [
            PodSummary(
                name="sample-api",
                phase="Running",
                restart_count=2,
            )
        ]


def test_kubernetes_tool_queries_configured_namespace() -> None:
    reader = FakeKubernetesReader()

    tools = create_kubernetes_tools(reader, namespace="sample")
    result = tools[0].invoke({})

    assert reader.namespaces == ["sample"]
    assert result == [
        {
            "name": "sample-api",
            "phase": "Running",
            "restart_count": 2,
        }
    ]
