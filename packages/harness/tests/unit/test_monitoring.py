from ops_agent.kubernetes import (
    DeploymentSummary,
    PodSummary,
    ServicePortSummary,
    ServiceSummary,
)
from ops_agent.monitoring import KubernetesMonitor


class FakeKubernetesSource:
    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def list_pods(self, namespace: str) -> list[PodSummary]:
        self.calls.append(("pods", namespace))
        return [
            PodSummary(
                name="sample-api",
                phase="Running",
                restart_count=1,
                ready_containers=2,
                total_containers=2,
            )
        ]

    def list_deployments(self, namespace: str) -> list[DeploymentSummary]:
        self.calls.append(("deployments", namespace))
        return [
            DeploymentSummary(
                name="sample-api",
                desired_replicas=2,
                ready_replicas=2,
                available_replicas=2,
                updated_replicas=2,
            )
        ]

    def list_services(self, namespace: str) -> list[ServiceSummary]:
        self.calls.append(("services", namespace))
        return [
            ServiceSummary(
                name="sample-api",
                type="ClusterIP",
                cluster_ip="10.43.0.10",
                ports=[
                    ServicePortSummary(
                        name="http",
                        port=80,
                        protocol="TCP",
                        target_port="8080",
                    )
                ],
            )
        ]


def test_monitor_captures_fixed_namespace_snapshot() -> None:
    source = FakeKubernetesSource()
    monitor = KubernetesMonitor(source, namespace="sample")

    snapshot = monitor.snapshot()

    assert snapshot.namespace == "sample"
    assert snapshot.pods[0].name == "sample-api"
    assert snapshot.deployments[0].ready_replicas == 2
    assert snapshot.services[0].cluster_ip == "10.43.0.10"
    assert snapshot.observed_at.tzinfo is not None
    assert source.calls == [
        ("pods", "sample"),
        ("deployments", "sample"),
        ("services", "sample"),
    ]
