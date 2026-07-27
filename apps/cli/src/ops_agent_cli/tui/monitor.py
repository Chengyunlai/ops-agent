from enum import StrEnum

from ops_agent.kubernetes import PodSummary
from ops_agent.monitoring import KubernetesMonitorSnapshot
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static


class MonitorView(StrEnum):
    PODS = "pods"
    DEPLOYMENTS = "deployments"
    SERVICES = "services"


class MonitorPane(Vertical):
    """用一个稳定表格展示可切换的 Kubernetes 资源快照。"""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._snapshot: KubernetesMonitorSnapshot | None = None
        self._view = MonitorView.PODS

    def compose(self) -> ComposeResult:
        yield Static(" LIVE MONITOR · 正在连接…", id="monitor-title")
        yield Static(id="monitor-tabs")
        yield DataTable(
            cursor_type="row",
            zebra_stripes=True,
            id="monitor-table",
        )
        yield Static("等待第一次资源快照", id="monitor-status")

    def on_mount(self) -> None:
        self._render_tabs()
        self._render_table()

    def display_snapshot(self, snapshot: KubernetesMonitorSnapshot) -> None:
        self._snapshot = snapshot
        healthy_pods = sum(_is_healthy_pod(pod) for pod in snapshot.pods)
        ready_deployments = sum(
            deployment.ready_replicas == deployment.desired_replicas
            for deployment in snapshot.deployments
        )
        self.query_one("#monitor-title", Static).update(
            " LIVE MONITOR"
            f" · Pods {healthy_pods}/{len(snapshot.pods)}"
            f" · Deploy {ready_deployments}/{len(snapshot.deployments)}"
            f" · Services {len(snapshot.services)}"
        )
        self.query_one("#monitor-status", Static).update(
            f"最近刷新 {snapshot.observed_at.astimezone():%H:%M:%S} · 自动刷新"
        )
        self._render_table()

    def display_error(self, message: str) -> None:
        self.query_one("#monitor-title", Static).update(" LIVE MONITOR · 暂时不可用")
        self.query_one("#monitor-status", Static).update(f"刷新失败：{message}")

    def show_view(self, view: MonitorView) -> None:
        self._view = view
        self._render_tabs()
        self._render_table()

    def _render_tabs(self) -> None:
        labels = {
            MonitorView.PODS: "1 Pods",
            MonitorView.DEPLOYMENTS: "2 Deployments",
            MonitorView.SERVICES: "3 Services",
        }
        tabs = [
            (
                f"[bold #ffcc66]{label}[/]"
                if view is self._view
                else f"[#8fa1b3]{label}[/]"
            )
            for view, label in labels.items()
        ]
        self.query_one("#monitor-tabs", Static).update("  ".join(tabs))

    def _render_table(self) -> None:
        table = self.query_one("#monitor-table", DataTable)
        table.clear(columns=True)
        snapshot = self._snapshot
        if self._view is MonitorView.PODS:
            table.add_columns("NAME", "READY", "STATUS", "RESTARTS")
            if snapshot is not None:
                for pod in snapshot.pods:
                    healthy = _is_healthy_pod(pod)
                    table.add_row(
                        pod.name,
                        f"{pod.ready_containers}/{pod.total_containers}",
                        Text(
                            pod.phase,
                            style=("#51d8d0" if healthy else "#ffcc66"),
                        ),
                        Text(
                            str(pod.restart_count),
                            style=("#ffcc66" if pod.restart_count else "#d7dee7"),
                        ),
                        key=pod.name,
                    )
        elif self._view is MonitorView.DEPLOYMENTS:
            table.add_columns("NAME", "READY", "AVAILABLE", "UPDATED")
            if snapshot is not None:
                for deployment in snapshot.deployments:
                    table.add_row(
                        deployment.name,
                        (f"{deployment.ready_replicas}/{deployment.desired_replicas}"),
                        str(deployment.available_replicas),
                        str(deployment.updated_replicas),
                        key=deployment.name,
                    )
        else:
            table.add_columns("NAME", "TYPE", "CLUSTER-IP", "PORTS")
            if snapshot is not None:
                for service in snapshot.services:
                    ports = ", ".join(
                        f"{port.port}/{port.protocol}" for port in service.ports
                    )
                    table.add_row(
                        service.name,
                        service.type,
                        service.cluster_ip or "-",
                        ports or "-",
                        key=service.name,
                    )


def _is_healthy_pod(pod: PodSummary) -> bool:
    return pod.phase == "Running" and pod.ready_containers == pod.total_containers
