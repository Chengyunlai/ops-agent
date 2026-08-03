from langchain_core.tools import StructuredTool
from ops_agent.agent.specialists.kubernetes import KubernetesEvidenceCollector


def test_collector_deterministically_reads_pod_events_and_previous_logs() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def diagnose_workloads() -> dict[str, object]:
        calls.append(("diagnostics", {}))
        return {
            "namespace": "sample",
            "findings": [
                {
                    "severity": "warning",
                    "code": "pod_crash_loop",
                    "resource_kind": "Pod",
                    "resource_name": "sample-api",
                    "summary": "display text can change independently",
                    "container_name": "api",
                    "evidence": [],
                }
            ],
        }

    def list_events(
        pod_name: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, object]]:
        calls.append(("events", {"pod_name": pod_name, "limit": limit}))
        return [{"reason": "BackOff", "object_name": pod_name}]

    def get_logs(
        pod_name: str,
        container: str | None = None,
        tail_lines: int = 200,
        previous: bool = False,
    ) -> dict[str, object]:
        calls.append(
            (
                "logs",
                {
                    "pod_name": pod_name,
                    "container": container,
                    "tail_lines": tail_lines,
                    "previous": previous,
                },
            )
        )
        return {"logs": "previous instance failed", "previous": previous}

    collector = KubernetesEvidenceCollector(
        [
            StructuredTool.from_function(
                diagnose_workloads,
                name="diagnose_kubernetes_workloads",
                description="diagnose",
            ),
            StructuredTool.from_function(
                list_events,
                name="list_kubernetes_events",
                description="events",
            ),
            StructuredTool.from_function(
                get_logs,
                name="get_kubernetes_pod_logs",
                description="logs",
            ),
        ]
    )

    health = collector.collect_workload_health()
    supporting = collector.collect_supporting_evidence(health)

    assert health.evidence_count == 1
    assert health.findings[0].container_name == "api"
    assert supporting.evidence_count == 2
    assert supporting.issues == ()
    assert calls == [
        ("diagnostics", {}),
        ("events", {"pod_name": "sample-api", "limit": 100}),
        (
            "logs",
            {
                "pod_name": "sample-api",
                "container": "api",
                "tail_lines": 200,
                "previous": True,
            },
        ),
    ]


def test_collector_keeps_failed_previous_logs_distinct_from_empty_logs() -> None:
    def diagnose_workloads() -> dict[str, object]:
        return {
            "findings": [
                {
                    "code": "pod_oom_killed",
                    "resource_kind": "Pod",
                    "resource_name": "sample-api",
                    "summary": "display text can change independently",
                    "container_name": "worker",
                }
            ]
        }

    def list_events(
        pod_name: str | None = None,
        limit: int = 100,
    ) -> list[object]:
        return []

    def get_logs(
        pod_name: str,
        container: str | None = None,
        tail_lines: int = 200,
        previous: bool = False,
    ) -> dict[str, object]:
        raise RuntimeError("previous logs are forbidden")

    collector = KubernetesEvidenceCollector(
        [
            StructuredTool.from_function(
                diagnose_workloads,
                name="diagnose_kubernetes_workloads",
                description="diagnose",
            ),
            StructuredTool.from_function(
                list_events,
                name="list_kubernetes_events",
                description="events",
            ),
            StructuredTool.from_function(
                get_logs,
                name="get_kubernetes_pod_logs",
                description="logs",
            ),
        ]
    )

    supporting = collector.collect_supporting_evidence(
        collector.collect_workload_health()
    )

    assert supporting.evidence_count == 1
    assert len(supporting.issues) == 1
    assert supporting.issues[0].source == (
        "get_kubernetes_pod_logs:Pod/sample-api/worker:previous"
    )
    assert supporting.issues[0].message == "previous logs are forbidden"


def test_resource_pressure_finding_collects_events_without_previous_logs() -> None:
    calls: list[tuple[str, dict[str, object]]] = []

    def diagnose_workloads() -> dict[str, object]:
        return {
            "findings": [
                {
                    "code": "pod_resource_unschedulable",
                    "resource_kind": "Pod",
                    "resource_name": "memory-worker",
                    "summary": "Pod 因资源不足无法调度",
                }
            ]
        }

    def list_events(
        pod_name: str | None = None,
        limit: int = 100,
    ) -> list[object]:
        calls.append(("events", {"pod_name": pod_name, "limit": limit}))
        return []

    collector = KubernetesEvidenceCollector(
        [
            StructuredTool.from_function(
                diagnose_workloads,
                name="diagnose_kubernetes_workloads",
                description="diagnose",
            ),
            StructuredTool.from_function(
                list_events,
                name="list_kubernetes_events",
                description="events",
            ),
        ]
    )

    health = collector.collect_workload_health()
    supporting = collector.collect_supporting_evidence(health)

    assert health.findings[0].code.value == "pod_resource_unschedulable"
    assert supporting.issues == ()
    assert calls == [("events", {"pod_name": "memory-worker", "limit": 100})]
