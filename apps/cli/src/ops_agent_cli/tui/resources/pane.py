from ops_agent.monitoring import (
    KubernetesMetricsAvailability,
    KubernetesMetricsStatus,
    KubernetesMonitorSnapshot,
    KubernetesResourceCollection,
    KubernetesResourceKind,
    KubernetesResourceRef,
    KubernetesResourceRow,
)
from rich.text import Text
from textual.app import ComposeResult
from textual.containers import Vertical
from textual.widgets import DataTable, Static
from textual.widgets.data_table import RowDoesNotExist, RowKey


class MonitorPane(Vertical):
    """展示固定 namespace 的资源目录与资源列表。"""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._snapshot: KubernetesMonitorSnapshot | None = None
        self._kind: KubernetesResourceKind | None = None

    def compose(self) -> ComposeResult:
        yield Static(" 实时 · 正在连接 Kubernetes…", id="monitor-title")
        yield Static(id="monitor-tabs")
        yield DataTable(
            cursor_type="row",
            zebra_stripes=True,
            id="monitor-table",
        )
        yield Static("等待第一次资源快照", id="monitor-status")

    def on_mount(self) -> None:
        self._render_tabs()
        self._render_table(preserve_viewport=False)

    def display_snapshot(self, snapshot: KubernetesMonitorSnapshot) -> None:
        self._snapshot = snapshot
        diagnostic_status = f" · {snapshot.finding_count} 项发现"
        if snapshot.diagnostic_errors:
            diagnostic_status += " · 诊断不完整"
        metrics_status = _metrics_status(snapshot.metrics)
        self.query_one("#monitor-status", Static).update(
            f"最近刷新 {snapshot.observed_at.astimezone():%H:%M:%S}"
            f"{diagnostic_status} · {metrics_status}"
            " · Enter 健康 · d 详情 · l 日志（Pod）"
        )
        self._render_title()
        self._render_tabs()
        self._render_table()

    def display_error(self, message: str) -> None:
        self.query_one("#monitor-title", Static).update(
            f" 实时 · {_kind_label(self._snapshot, self._kind)} · 暂时不可用"
        )
        self.query_one("#monitor-status", Static).update(f"刷新失败：{message}")

    def show_overview(self) -> None:
        self._kind = None
        self._render_all()

    def show_kind(self, kind: KubernetesResourceKind) -> None:
        self._kind = kind
        self._render_all()

    def focus_table(self) -> None:
        self.query_one("#monitor-table", DataTable).focus()

    def refresh_theme(self) -> None:
        self._render_tabs()
        self._render_table()

    def open_selected_overview_kind(self) -> bool:
        snapshot = self._snapshot
        table = self.query_one("#monitor-table", DataTable)
        if (
            self._kind is not None
            or snapshot is None
            or not 0 <= table.cursor_row < len(snapshot.resources)
        ):
            return False
        self.show_kind(snapshot.resources[table.cursor_row].kind)
        return True

    def selected_resource(self) -> KubernetesResourceRef | None:
        collection = self._current_collection()
        table = self.query_one("#monitor-table", DataTable)
        if collection is None or not 0 <= table.cursor_row < len(collection.rows):
            return None
        return collection.rows[table.cursor_row].ref

    def _render_all(self) -> None:
        self._render_title()
        self._render_tabs()
        self._render_table(preserve_viewport=False)

    def _render_title(self) -> None:
        snapshot = self._snapshot
        title = " 实时 · Namespace"
        if snapshot is not None:
            title += f" {snapshot.namespace}"
        title += f" · {_kind_label(snapshot, self._kind)}"
        if snapshot is not None:
            if self._kind is None:
                total = sum(len(resource.rows) for resource in snapshot.resources)
                title += f" · {total} 个资源 · {snapshot.finding_count} 项发现"
            elif (collection := self._current_collection()) is not None:
                title += (
                    " · 不可用"
                    if collection.error is not None
                    else f" · {len(collection.rows)}"
                )
        self.query_one("#monitor-title", Static).update(title)

    def _render_tabs(self) -> None:
        theme = self.app.current_theme
        selected_color = theme.accent or theme.primary
        idle_color = theme.foreground or theme.primary
        labels = [("0", "总览", self._kind is None)]
        if self._snapshot is not None:
            labels.extend(
                (
                    resource.shortcut,
                    resource.label.removesuffix("ments").removesuffix("Sets"),
                    resource.kind is self._kind,
                )
                for resource in self._snapshot.resources
                if resource.shortcut is not None
            )
        tabs = [
            (
                f"[bold {selected_color}]{shortcut} {label}[/]"
                if selected
                else f"[{idle_color}]{shortcut} {label}[/]"
            )
            for shortcut, label, selected in labels
        ]
        tabs.append(f"[{idle_color}]↑/↓ Enter 健康[/]")
        self.query_one("#monitor-tabs", Static).update("  ".join(tabs))

    def _render_table(self, *, preserve_viewport: bool = True) -> None:
        table = self.query_one("#monitor-table", DataTable)
        selected_row_key = _selected_row_key(table)
        viewport = (table.scroll_x, table.scroll_y) if preserve_viewport else None
        table.clear(columns=True)
        snapshot = self._snapshot
        if self._kind is None:
            table.add_columns("资源", "数量", "就绪", "发现", "状态")
            if snapshot is not None:
                theme = self.app.current_theme
                for collection in snapshot.resources:
                    count, ready, status, healthy = _collection_status(collection)
                    finding_count = sum(
                        diagnostic.ref.kind is collection.kind
                        for diagnostic in snapshot.diagnostics
                    )
                    table.add_row(
                        collection.label,
                        count,
                        ready,
                        str(finding_count),
                        _health_text(
                            status,
                            healthy,
                            success=theme.success,
                            warning=theme.warning,
                            neutral=theme.foreground,
                        ),
                        key=collection.kind.value,
                    )
        else:
            collection = self._current_collection()
            if collection is None:
                table.add_column("资源")
            else:
                table.add_columns(
                    _column_label(collection.columns[0]),
                    "诊断",
                    *(_column_label(column) for column in collection.columns[1:]),
                )
                if collection.error is not None:
                    table.add_row(
                        Text(
                            "不可用",
                            style=self.app.current_theme.warning,
                        ),
                        collection.error,
                        *("-" for _ in collection.columns[1:]),
                    )
                else:
                    theme = self.app.current_theme
                    for row in collection.rows:
                        has_findings = bool(row.health_reasons)
                        name_style = (
                            theme.warning
                            if row.healthy is False or has_findings
                            else theme.success
                            if row.healthy is True
                            else theme.foreground
                        )
                        values = (
                            Text(row.values[0], style=name_style),
                            _diagnosis_text(
                                row,
                                success=theme.success,
                                warning=theme.warning,
                                neutral=theme.foreground,
                            ),
                            *row.values[1:],
                        )
                        table.add_row(
                            *values,
                            key=f"{row.ref.kind.value}:{row.ref.name}",
                        )
        _restore_selected_row(table, selected_row_key)
        if viewport is not None:
            table.scroll_to(
                x=viewport[0],
                y=viewport[1],
                animate=False,
                force=True,
                immediate=True,
            )

    def _current_collection(self) -> KubernetesResourceCollection | None:
        if self._snapshot is None or self._kind is None:
            return None
        return self._snapshot.collection(self._kind)


def _kind_label(
    snapshot: KubernetesMonitorSnapshot | None,
    kind: KubernetesResourceKind | None,
) -> str:
    if kind is None:
        return "总览"
    if snapshot is not None and (collection := snapshot.collection(kind)) is not None:
        return collection.label
    return str(kind)


def _metrics_status(status: KubernetesMetricsStatus) -> str:
    if status.availability is KubernetesMetricsAvailability.DISABLED:
        return "Metrics 已禁用"
    if status.availability is KubernetesMetricsAvailability.UNAVAILABLE:
        detail = f": {status.error}" if status.error else ""
        return f"Metrics 不可用{detail}"
    if status.observed_at is None:
        return "Metrics 可用"
    return f"Metrics {status.observed_at.astimezone():%H:%M:%S}"


def _selected_row_key(table: DataTable) -> RowKey | None:
    if not table.is_valid_coordinate(table.cursor_coordinate):
        return None
    return table.coordinate_to_cell_key(table.cursor_coordinate).row_key


def _restore_selected_row(table: DataTable, row_key: RowKey | None) -> None:
    if row_key is None:
        return
    try:
        row_index = table.get_row_index(row_key)
    except RowDoesNotExist:
        return
    table.move_cursor(row=row_index)


def _collection_status(
    collection: KubernetesResourceCollection,
) -> tuple[str, str, str, bool | None]:
    if collection.error is not None:
        return "-", "-", "不可用", False
    count = len(collection.rows)
    if count == 0:
        return "0", "-", "空", None
    health = [row.healthy for row in collection.rows if row.healthy is not None]
    if not health:
        return str(count), "-", "已读取", None
    ready = sum(health)
    healthy = ready == len(health)
    return (
        str(count),
        f"{ready}/{len(health)}",
        "健康" if healthy else "需关注",
        healthy,
    )


def _column_label(label: str) -> str:
    return {
        "NAME": "名称",
        "READY": "就绪",
        "STATUS": "状态",
        "RESTARTS": "重启",
        "AGE": "时长",
        "AVAILABLE": "可用",
        "UPDATED": "已更新",
        "CURRENT": "当前",
        "TYPE": "类型",
        "CLUSTER-IP": "集群 IP",
        "PORTS": "端口",
        "DESIRED": "期望",
        "STORAGECLASS": "存储类",
        "CAPACITY": "容量",
        "ACCESS-MODES": "访问模式",
        "VOLUME": "卷",
        "SUCCEEDED": "成功",
        "ACTIVE": "活跃",
        "FAILED": "失败",
        "SCHEDULE": "调度计划",
        "SUSPEND": "暂停",
        "LAST": "上次运行",
        "CLASS": "类别",
        "HOSTS": "主机",
        "ADDRESS": "地址",
        "BACKEND": "后端",
        "MOUNTED BY": "挂载目标",
        "MOUNT PATHS": "挂载路径",
    }.get(label, label)


def _health_text(
    label: str,
    healthy: bool | None,
    *,
    success: str | None,
    warning: str | None,
    neutral: str | None,
) -> Text:
    style = success if healthy is True else warning if healthy is False else neutral
    return Text(label, style=style)


def _diagnosis_text(
    row: KubernetesResourceRow,
    *,
    success: str | None,
    warning: str | None,
    neutral: str | None,
) -> Text:
    if row.health_reasons:
        reason = row.health_reasons[0]
        remaining = len(row.health_reasons) - 1
        suffix = f" · +{remaining}" if remaining else ""
        return Text(f"WARN · {reason}{suffix}", style=warning)
    if row.healthy is False:
        return Text("WARN", style=warning)
    if row.healthy is True:
        return Text("OK", style=success)
    return Text("—", style=neutral)
