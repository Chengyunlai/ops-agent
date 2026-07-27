from enum import StrEnum
from typing import Self

from langchain.agents import create_agent
from langchain.agents.structured_output import ToolStrategy
from langchain_core.language_models import BaseChatModel
from pydantic import BaseModel, ConfigDict, Field, model_validator

from ops_agent.kubernetes_agent import KubernetesAgent

PLANNER_PROMPT = """\
你是 Kubernetes 只读诊断计划器，只返回 ExecutionPlan，不回答用户问题。

规则：
- 计划包含 1 到 3 个按顺序执行的步骤。
- objective 只能选择模型声明的受控诊断目标。
- 第一步必须检查工作负载健康，之后才能补充证据或分析根因。
- 计划只能读取和诊断，不能包含修改 Kubernetes 资源的操作。
"""


class DiagnosticObjective(StrEnum):
    WORKLOAD_HEALTH = "workload_health"
    SUPPORTING_EVIDENCE = "supporting_evidence"
    ROOT_CAUSE = "root_cause"

    @property
    def instruction(self) -> str:
        return {
            DiagnosticObjective.WORKLOAD_HEALTH: "收集相关工作负载健康状态",
            DiagnosticObjective.SUPPORTING_EVIDENCE: "根据已有异常补充事件和日志证据",
            DiagnosticObjective.ROOT_CAUSE: "基于已收集证据分析根因和处理建议",
        }[self]


class _PlanModel(BaseModel):
    model_config = ConfigDict(extra="forbid", frozen=True)


class PlanStep(_PlanModel):
    objective: DiagnosticObjective


class ExecutionPlan(_PlanModel):
    """按声明顺序执行的有限 Kubernetes 诊断步骤。"""

    steps: tuple[PlanStep, ...] = Field(min_length=1, max_length=3)

    @model_validator(mode="after")
    def objectives_must_follow_diagnostic_order(self) -> Self:
        objectives = [step.objective for step in self.steps]
        if len(objectives) != len(set(objectives)):
            raise ValueError("诊断计划不能包含重复目标")
        if objectives[0] is not DiagnosticObjective.WORKLOAD_HEALTH:
            raise ValueError("诊断计划必须先检查工作负载健康")
        expected_order = {
            DiagnosticObjective.WORKLOAD_HEALTH: 0,
            DiagnosticObjective.SUPPORTING_EVIDENCE: 1,
            DiagnosticObjective.ROOT_CAUSE: 2,
        }
        ranks = [expected_order[objective] for objective in objectives]
        if ranks != sorted(ranks):
            raise ValueError("诊断计划目标顺序无效")
        return self


class PlanExecutionResult(_PlanModel):
    answer: str | None = None
    evidence_count: int = Field(ge=0)


class KubernetesDiagnosticPlanner:
    """隐藏结构化计划模型调用及非法计划拒绝。"""

    def __init__(self, model: BaseChatModel) -> None:
        self._runner = create_agent(
            model=model,
            tools=[],
            system_prompt=PLANNER_PROMPT,
            response_format=ToolStrategy(
                ExecutionPlan,
                handle_errors=False,
            ),
            name="kubernetes_diagnostics_planner",
        )

    def create(self, question: str) -> ExecutionPlan | None:
        try:
            result = self._runner.invoke(
                {
                    "messages": [
                        {
                            "role": "user",
                            "content": question,
                        }
                    ]
                }
            )
        except Exception:  # noqa: BLE001 - 非法计划必须统一拒绝
            return None
        plan = result.get("structured_response")
        return plan if isinstance(plan, ExecutionPlan) else None


class KubernetesPlanExecutor:
    """顺序执行受控诊断目标，遇到无工具证据立即停止。"""

    def __init__(self, agent: KubernetesAgent) -> None:
        self._agent = agent

    def execute(
        self,
        question: str,
        plan: ExecutionPlan,
    ) -> PlanExecutionResult:
        summaries: list[tuple[DiagnosticObjective, str]] = []
        evidence_count = 0
        for step in plan.steps:
            result = self._agent.diagnose(
                _format_step_request(question, step, summaries)
            )
            evidence_count += result.evidence_count
            if not result.is_grounded or result.answer is None:
                return PlanExecutionResult(evidence_count=evidence_count)
            summaries.append((step.objective, result.answer))

        lines = ["诊断计划执行完成："]
        lines.extend(
            f"{index}. {objective.instruction}：{summary}"
            for index, (objective, summary) in enumerate(summaries, start=1)
        )
        return PlanExecutionResult(
            answer="\n".join(lines),
            evidence_count=evidence_count,
        )


def _format_step_request(
    original_question: str,
    step: PlanStep,
    completed_results: list[tuple[DiagnosticObjective, str]],
) -> str:
    context = "\n".join(
        f"- {objective.instruction}: {summary}"
        for objective, summary in completed_results
    )
    request = (
        f"原始问题：{original_question}\n当前诊断目标：{step.objective.instruction}"
    )
    if context:
        request += f"\n已完成步骤结果：\n{context}"
    return request
