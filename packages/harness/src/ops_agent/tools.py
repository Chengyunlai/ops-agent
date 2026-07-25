from dataclasses import asdict
from typing import Protocol

from langchain_core.tools import BaseTool, tool

from ops_agent.kubernetes import PodSummary


class PodReader(Protocol):
    def list_pods(self, namespace: str) -> list[PodSummary]: ...


def create_kubernetes_tools(
    reader: PodReader,
    *,
    namespace: str,
) -> list[BaseTool]:
    @tool("list_kubernetes_pods")
    def list_kubernetes_pods() -> list[dict[str, object]]:
        """查询当前 Kubernetes 环境中已配置命名空间的所有 Pod。

        返回每个 Pod 的名称、生命周期阶段和容器重启总次数。
        当用户询问当前 Pod 状态、异常 Pod 或重启情况时使用此工具。
        """
        pods = reader.list_pods(namespace)
        return [asdict(pod) for pod in pods]

    return [list_kubernetes_pods]
