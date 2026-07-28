from typing import ClassVar

from ops_agent.monitoring import (
    KubernetesMonitorSnapshot,
    KubernetesResourceCollection,
    KubernetesResourceContent,
    KubernetesResourceKind,
    KubernetesResourceRef,
)
from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, RichLog, Static


class ResourceViewer(ModalScreen[None]):
    """只读显示一次固定 Kubernetes API 查询的结果。"""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "返回", priority=True),
        Binding("q", "close", "返回", priority=True),
    ]

    def __init__(self, *, loading_title: str) -> None:
        super().__init__()
        self._loading_title = loading_title

    def compose(self) -> ComposeResult:
        with Vertical(id="resource-viewer"):
            yield Static(self._loading_title, id="resource-title")
            yield RichLog(
                highlight=False,
                markup=False,
                wrap=False,
                id="resource-content",
            )
            yield Static(
                " Esc/q 返回 · ↑/↓/PgUp/PgDn 滚动 · 鼠标拖选复制",
                id="resource-footer",
            )

    def on_mount(self) -> None:
        self.query_one("#resource-content", RichLog).write("正在读取 Kubernetes API…")

    def display_content(self, content: KubernetesResourceContent) -> None:
        self.query_one("#resource-title", Static).update(content.title)
        viewer = self.query_one("#resource-content", RichLog)
        viewer.clear()
        viewer.write((content.content or "（没有返回内容）").rstrip("\r\n"))

    def display_error(self, message: str) -> None:
        self.query_one("#resource-title", Static).update("读取失败")
        viewer = self.query_one("#resource-content", RichLog)
        viewer.clear()
        viewer.write(message)

    def action_close(self) -> None:
        self.dismiss()


class MonitorPane(Vertical):
    """展示固定 namespace 的资源目录与资源列表。"""

    def __init__(self, *, id: str | None = None) -> None:
        super().__init__(id=id)
        self._snapshot: KubernetesMonitorSnapshot | None = None
        self._kind: KubernetesResourceKind | None = None

    def compose(self) -> ComposeResult:
        yield Static(" LIVE · 正在连接 Kubernetes…", id="monitor-title")
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
        self.query_one("#monitor-status", Static).update(
            f"最近刷新 {snapshot.observed_at.astimezone():%H:%M:%S}"
            " · d Describe · l Logs(Pod)"
        )
        self._render_title()
        self._render_tabs()
        self._render_table()

    def display_error(self, message: str) -> None:
        self.query_one("#monitor-title", Static).update(
            f" LIVE · {_kind_label(self._snapshot, self._kind)} · 暂时不可用"
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
        self._render_table()

    def _render_title(self) -> None:
        snapshot = self._snapshot
        title = " LIVE · Namespace"
        if snapshot is not None:
            title += f" {snapshot.namespace}"
        title += f" · {_kind_label(snapshot, self._kind)}"
        if snapshot is not None:
            if self._kind is None:
                total = sum(len(resource.rows) for resource in snapshot.resources)
                title += f" · {total} resources"
            elif (collection := self._current_collection()) is not None:
                title += (
                    " · Unavailable"
                    if collection.error is not None
                    else f" · {len(collection.rows)}"
                )
        self.query_one("#monitor-title", Static).update(title)

    def _render_tabs(self) -> None:
        labels = [("0", "Overview", self._kind is None)]
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
                f"[bold #ffcc66]{shortcut} {label}[/]"
                if selected
                else f"[#8fa1b3]{shortcut} {label}[/]"
            )
            for shortcut, label, selected in labels
        ]
        tabs.append("[#8fa1b3]↑/↓ Enter 浏览目录[/]")
        self.query_one("#monitor-tabs", Static).update("  ".join(tabs))

    def _render_table(self) -> None:
        table = self.query_one("#monitor-table", DataTable)
        table.clear(columns=True)
        snapshot = self._snapshot
        if self._kind is None:
            table.add_columns("RESOURCE", "COUNT", "READY", "STATUS")
            if snapshot is not None:
                for collection in snapshot.resources:
                    count, ready, status, healthy = _collection_status(collection)
                    table.add_row(
                        collection.label,
                        count,
                        ready,
                        _health_text(status, healthy),
                        key=collection.kind.value,
                    )
            return

        collection = self._current_collection()
        if collection is None:
            table.add_column("RESOURCE")
            return
        table.add_columns(*collection.columns)
        if collection.error is not None:
            table.add_row(
                Text("Unavailable", style="#ffcc66"),
                collection.error,
                *("-" for _ in collection.columns[2:]),
            )
            return
        for row in collection.rows:
            name_style = (
                "#ffcc66"
                if row.healthy is False
                else "#51d8d0"
                if row.healthy is True
                else "#d7dee7"
            )
            values = (Text(row.values[0], style=name_style), *row.values[1:])
            table.add_row(*values, key=row.ref.name)

    def _current_collection(self) -> KubernetesResourceCollection | None:
        if self._snapshot is None or self._kind is None:
            return None
        return self._snapshot.collection(self._kind)


def _kind_label(
    snapshot: KubernetesMonitorSnapshot | None,
    kind: KubernetesResourceKind | None,
) -> str:
    if kind is None:
        return "Overview"
    if snapshot is not None and (collection := snapshot.collection(kind)) is not None:
        return collection.label
    return str(kind)


def _collection_status(
    collection: KubernetesResourceCollection,
) -> tuple[str, str, str, bool | None]:
    if collection.error is not None:
        return "-", "-", "Unavailable", False
    count = len(collection.rows)
    if count == 0:
        return "0", "-", "Empty", None
    health = [row.healthy for row in collection.rows if row.healthy is not None]
    if not health:
        return str(count), "-", "Inventory", None
    ready = sum(health)
    healthy = ready == len(health)
    return (
        str(count),
        f"{ready}/{len(health)}",
        "Healthy" if healthy else "Attention",
        healthy,
    )


def _health_text(label: str, healthy: bool | None) -> Text:
    style = (
        "#51d8d0" if healthy is True else "#ffcc66" if healthy is False else "#8fa1b3"
    )
    return Text(label, style=style)
