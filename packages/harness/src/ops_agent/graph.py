from collections.abc import Sequence

from langchain.agents import create_agent
from langchain_core.language_models import BaseChatModel
from langchain_core.tools import BaseTool, StructuredTool

from ops_agent.agent import OpsAgent

SUPERVISOR_PROMPT = """\
你是运维任务的主 Agent，负责理解请求、选择专业子 Agent，并汇总最终回答。

规则：
- 涉及 Kubernetes 当前状态或故障诊断时，必须委派给 kubernetes_diagnostics。
- 不要绕过子 Agent 猜测集群状态。
- 清楚区分子 Agent 返回的事实和你的推断。
- 当前系统只支持只读诊断，不能声称已经执行任何修改操作。
- 使用简洁中文回答。
"""

KUBERNETES_AGENT_PROMPT = """\
你是 Kubernetes 只读诊断子 Agent。

规则：
- 涉及集群当前状态的问题，必须先调用工具获取真实数据，不能凭空猜测。
- Kubernetes 环境和 namespace 已由应用配置固定，不要要求用户重复提供。
- 清楚区分工具返回的事实和你的推断。
- 宽泛的工作负载健康检查必须先调用 diagnose_kubernetes_workloads 获取确定性诊断。
- 根据诊断 finding，再按需查询 Pod、Deployment、Event、Pod 详情和日志补充证据。
- 优先指出未就绪、非 Running 或发生过重启的 Pod，但不要把所有重启都直接判定为故障。
- 日志只用于验证具体问题，不要无目的地读取大量日志。
- 当前只能查询，不能声称已经执行重启、删除、扩缩容或其他修改操作。
- 返回可由主 Agent 直接引用的简洁中文诊断结果。
"""


def create_ops_agent(
    model: BaseChatModel,
    kubernetes_tools: Sequence[BaseTool],
) -> OpsAgent:
    """构建当前运维领域的主 Agent 与 Kubernetes 子 Agent 图。"""

    kubernetes_agent = OpsAgent(
        create_agent(
            model=model,
            tools=list(kubernetes_tools),
            system_prompt=KUBERNETES_AGENT_PROMPT,
            name="kubernetes_diagnostics_agent",
        )
    )
    return OpsAgent(
        create_agent(
            model=model,
            tools=[_create_kubernetes_delegation_tool(kubernetes_agent)],
            system_prompt=SUPERVISOR_PROMPT,
            name="ops_supervisor",
        )
    )


def _create_kubernetes_delegation_tool(
    kubernetes_agent: OpsAgent,
) -> BaseTool:
    def delegate(request: str) -> str:
        return kubernetes_agent.ask(request)

    return StructuredTool.from_function(
        delegate,
        name="kubernetes_diagnostics",
        description=(
            "检查 Kubernetes 集群当前状态，诊断 Pod、Deployment、Event 和日志问题。"
        ),
    )
