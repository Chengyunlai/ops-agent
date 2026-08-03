"""确定性收集 Kubernetes 诊断步骤所需的实时 Evidence。"""

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from langchain_core.tools import BaseTool
from pydantic import BaseModel, ConfigDict, ValidationError

from ops_agent.agent.specialists.kubernetes.capabilities import (
    DIAGNOSTICS_TOOL,
    EVENTS_TOOL,
    POD_LOGS_TOOL,
)
from ops_agent.diagnostics import FindingCode
from ops_agent.kubernetes import KubernetesResourceKind

_PREVIOUS_LOG_FINDING_CODES = frozenset(
    {
        FindingCode.POD_CRASH_LOOP,
        FindingCode.POD_OOM_KILLED,
    }
)


class KubernetesFindingRef(BaseModel):
    model_config = ConfigDict(extra="ignore", frozen=True)

    code: FindingCode | None = None
    resource_kind: KubernetesResourceKind
    resource_name: str
    container_name: str | None = None


@dataclass(frozen=True)
class KubernetesObservation:
    source: str
    payload: object


@dataclass(frozen=True)
class KubernetesEvidenceIssue:
    source: str
    message: str


@dataclass(frozen=True)
class KubernetesEvidence:
    observations: tuple[KubernetesObservation, ...] = ()
    findings: tuple[KubernetesFindingRef, ...] = ()
    issues: tuple[KubernetesEvidenceIssue, ...] = ()

    @property
    def evidence_count(self) -> int:
        return len(self.observations)

    def merge(self, other: "KubernetesEvidence") -> "KubernetesEvidence":
        return KubernetesEvidence(
            observations=self.observations + other.observations,
            findings=self.findings or other.findings,
            issues=self.issues + other.issues,
        )

    def as_prompt_data(self) -> dict[str, object]:
        return {
            "observations": [
                {"source": item.source, "payload": item.payload}
                for item in self.observations
            ],
            "issues": [
                {"source": item.source, "message": item.message} for item in self.issues
            ],
        }


class KubernetesEvidenceCollector:
    """在模型外按 Finding 类型选择固定的只读 Evidence 查询。"""

    def __init__(self, tools: Sequence[BaseTool]) -> None:
        self._tools = {tool.name: tool for tool in tools}

    def collect_workload_health(self) -> KubernetesEvidence:
        result, issue = self._invoke(DIAGNOSTICS_TOOL, {})
        if issue is not None:
            return KubernetesEvidence(issues=(issue,))
        if not isinstance(result, Mapping):
            return KubernetesEvidence(
                issues=(
                    KubernetesEvidenceIssue(
                        source=DIAGNOSTICS_TOOL,
                        message="诊断工具返回了无效的结构",
                    ),
                )
            )
        findings = _parse_findings(result.get("findings"))
        return KubernetesEvidence(
            observations=(
                KubernetesObservation(
                    source=DIAGNOSTICS_TOOL,
                    payload=result,
                ),
            ),
            findings=findings,
        )

    def collect_supporting_evidence(
        self,
        health: KubernetesEvidence,
    ) -> KubernetesEvidence:
        observations: list[KubernetesObservation] = []
        issues: list[KubernetesEvidenceIssue] = []
        pod_names = sorted(
            {
                finding.resource_name
                for finding in health.findings
                if finding.resource_kind is KubernetesResourceKind.POD
            }
        )
        for pod_name in pod_names:
            self._collect(
                tool_name=EVENTS_TOOL,
                arguments={"pod_name": pod_name, "limit": 100},
                source=f"{EVENTS_TOOL}:Pod/{pod_name}",
                observations=observations,
                issues=issues,
            )

        previous_log_targets = sorted(
            {
                (finding.resource_name, finding.container_name)
                for finding in health.findings
                if finding.resource_kind is KubernetesResourceKind.POD
                and finding.code in _PREVIOUS_LOG_FINDING_CODES
                and finding.container_name is not None
            }
        )
        for pod_name, container_name in previous_log_targets:
            self._collect(
                tool_name=POD_LOGS_TOOL,
                arguments={
                    "pod_name": pod_name,
                    "container": container_name,
                    "tail_lines": 200,
                    "previous": True,
                },
                source=f"{POD_LOGS_TOOL}:Pod/{pod_name}/{container_name}:previous",
                observations=observations,
                issues=issues,
            )
        return KubernetesEvidence(
            observations=tuple(observations),
            findings=health.findings,
            issues=tuple(issues),
        )

    def _collect(
        self,
        *,
        tool_name: str,
        arguments: dict[str, object],
        source: str,
        observations: list[KubernetesObservation],
        issues: list[KubernetesEvidenceIssue],
    ) -> None:
        result, issue = self._invoke(tool_name, arguments)
        if issue is not None:
            issues.append(
                KubernetesEvidenceIssue(
                    source=source,
                    message=issue.message,
                )
            )
            return
        observations.append(KubernetesObservation(source=source, payload=result))

    def _invoke(
        self,
        tool_name: str,
        arguments: dict[str, object],
    ) -> tuple[object | None, KubernetesEvidenceIssue | None]:
        tool = self._tools.get(tool_name)
        if tool is None:
            return None, KubernetesEvidenceIssue(
                source=tool_name,
                message="所需只读工具未注册",
            )
        try:
            return tool.invoke(arguments), None
        except Exception as error:  # noqa: BLE001 - 失败必须作为 unavailable 暴露
            return None, KubernetesEvidenceIssue(
                source=tool_name,
                message=str(error),
            )


def _parse_findings(value: object) -> tuple[KubernetesFindingRef, ...]:
    if not isinstance(value, list | tuple):
        return ()
    findings: list[KubernetesFindingRef] = []
    for item in value:
        if not isinstance(item, Mapping):
            continue
        try:
            finding = KubernetesFindingRef.model_validate(item)
        except ValidationError:
            continue
        findings.append(finding)
    return tuple(findings)
