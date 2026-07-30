import asyncio
import posixpath
from typing import ClassVar, Protocol, cast

from ops_agent.monitoring import (
    KubernetesMonitorSnapshot,
    KubernetesResourceCollection,
    KubernetesResourceContent,
    KubernetesResourceKind,
    KubernetesResourceRef,
    VolumeDirectory,
    VolumeEntry,
    VolumeEntryKind,
)
from rich.text import Text
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, DataTable, RichLog, Static
from textual.widgets.data_table import RowDoesNotExist, RowKey

from ops_agent_cli.pod_access import DownloadResult


class CopyModeController(Protocol):
    def exit_copy_mode(self) -> bool: ...

    def action_toggle_copy_mode(self) -> None: ...


class VolumeBrowserSource(Protocol):
    def browse_pvc(
        self,
        resource: KubernetesResourceRef,
        *,
        path: str = ".",
    ) -> VolumeDirectory: ...

    def preview_pvc_file(
        self,
        resource: KubernetesResourceRef,
        *,
        path: str,
        max_bytes: int = 64 * 1024,
    ) -> KubernetesResourceContent: ...


class PvcDownloader(Protocol):
    def download_pvc_file(
        self,
        *,
        claim_name: str,
        pod_name: str,
        container_name: str,
        mount_path: str,
        relative_path: str,
    ) -> DownloadResult: ...


class ResourceViewer(ModalScreen[None]):
    """只读显示一次固定 Kubernetes API 查询的结果。"""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "escape", "返回", priority=True),
        Binding("q", "close", "返回", priority=True),
    ]

    def __init__(self, *, loading_title: str, copy_mode: bool = False) -> None:
        super().__init__()
        self._loading_title = loading_title
        self._copy_mode = copy_mode

    def compose(self) -> ComposeResult:
        with Vertical(id="resource-viewer"):
            yield Static(self._loading_title, id="resource-title")
            yield RichLog(
                highlight=False,
                markup=False,
                wrap=False,
                id="resource-content",
            )
            with Horizontal(id="resource-footer"):
                yield Static(self._footer_text(), id="resource-footer-text")
                yield Button(
                    "复制",
                    id="resource-copy-button",
                    compact=True,
                    flat=True,
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

    def action_escape(self) -> None:
        controller = cast(CopyModeController, self.app)
        if self._copy_mode and controller.exit_copy_mode():
            return
        self.dismiss()

    def set_copy_mode(self, enabled: bool) -> None:
        self._copy_mode = enabled
        self.query_one("#resource-footer-text", Static).update(self._footer_text())

    @on(Button.Pressed, "#resource-copy-button")
    def toggle_copy_mode(self) -> None:
        controller = cast(CopyModeController, self.app)
        controller.action_toggle_copy_mode()

    def _footer_text(self) -> str:
        return (
            " COPY MODE · 直接用鼠标拖选复制 · Esc 恢复鼠标控制"
            if self._copy_mode
            else " Esc/q 返回 · ↑/↓/PgUp/PgDn 滚动 · F2 备用"
        )


class VolumeBrowser(ModalScreen[None]):
    """通过现有 Pod 以只读方式浏览 PVC 挂载目录。"""

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "close", "返回", priority=True),
        Binding("q", "close", "返回", priority=True),
        Binding("backspace", "parent", "上级目录", priority=True),
        Binding("r", "refresh", "刷新", priority=True),
        Binding("s", "download", "下载文件", priority=True),
    ]

    def __init__(
        self,
        *,
        source: VolumeBrowserSource,
        downloader: PvcDownloader,
        resource: KubernetesResourceRef,
    ) -> None:
        super().__init__()
        self._source = source
        self._downloader = downloader
        self._resource = resource
        self._directory: VolumeDirectory | None = None
        self._loading = False
        self._preview_loading = False
        self._download_loading = False

    def compose(self) -> ComposeResult:
        with Vertical(id="volume-browser"):
            yield Static(
                f" STORAGE · PVC/{self._resource.name} · READ-ONLY",
                id="volume-browser-title",
            )
            yield Static("正在查找可用挂载…", id="volume-browser-target")
            yield Static("/", id="volume-browser-path")
            yield DataTable(
                cursor_type="row",
                zebra_stripes=True,
                id="volume-browser-table",
            )
            yield Static("正在读取目录…", id="volume-browser-status")
            yield Static(
                " Enter 打开/预览 · s 下载文件 · Backspace 上级 · r 刷新 · Esc/q 返回",
                id="volume-browser-footer",
            )

    def on_mount(self) -> None:
        self.query_one("#volume-browser-table", DataTable).add_columns(
            "NAME",
            "TYPE",
            "SIZE",
        )
        self._load_directory(".")

    def action_close(self) -> None:
        self.dismiss()

    def action_parent(self) -> None:
        directory = self._directory
        if directory is None or directory.path == ".":
            return
        parent = posixpath.dirname(directory.path)
        self._load_directory(parent or ".")

    def action_refresh(self) -> None:
        path = self._directory.path if self._directory is not None else "."
        self._load_directory(path)

    def action_download(self) -> None:
        selected = self._selected_entry()
        directory = self._directory
        if selected is None or directory is None:
            self.query_one("#volume-browser-status", Static).update("请先选择一个文件")
            return
        entry, path = selected
        if entry.kind is not VolumeEntryKind.FILE:
            self.query_one("#volume-browser-status", Static).update(
                "Artifact Download 仅支持普通文件"
            )
            return
        if self._download_loading:
            self.query_one("#volume-browser-status", Static).update(
                "上一文件仍在下载，请稍候"
            )
            return
        self._download_loading = True
        self.query_one("#volume-browser-status", Static).update(f"正在下载 {path}…")
        self._download_file(path)

    @on(DataTable.RowSelected, "#volume-browser-table")
    def open_selected_entry(self) -> None:
        selected = self._selected_entry()
        if selected is None:
            return
        entry, path = selected
        if entry.kind is VolumeEntryKind.DIRECTORY:
            self._load_directory(path)
        elif entry.kind is VolumeEntryKind.FILE:
            self._open_file_preview(path)
        elif entry.kind is VolumeEntryKind.SYMLINK:
            self.query_one("#volume-browser-status", Static).update(
                "为避免越过 PVC 挂载根目录，不跟随符号链接"
            )
        else:
            self.query_one("#volume-browser-status", Static).update(
                "当前条目类型不支持预览"
            )

    @work(
        group="volume-download",
        exclusive=True,
        exit_on_error=False,
    )
    async def _download_file(self, path: str) -> None:
        directory = self._directory
        if directory is None:
            self._download_loading = False
            return
        target = directory.target
        try:
            result = await asyncio.to_thread(
                self._downloader.download_pvc_file,
                claim_name=self._resource.name,
                pod_name=target.pod_name,
                container_name=target.container_name,
                mount_path=target.mount_path,
                relative_path=path,
            )
        except Exception as error:  # noqa: BLE001 - 下载错误必须回显到浏览器
            if self.is_mounted:
                self.query_one("#volume-browser-status", Static).update(
                    f"下载失败：{error}"
                )
        else:
            if self.is_mounted:
                self.query_one("#volume-browser-status", Static).update(
                    _download_result_status(result)
                )
        finally:
            self._download_loading = False

    @work(
        group="volume-directory",
        exit_on_error=False,
    )
    async def _load_directory(self, path: str) -> None:
        if self._loading:
            return
        self._loading = True
        self.query_one("#volume-browser-status", Static).update(
            f"正在读取 /{path.removeprefix('.').lstrip('/')}…"
        )
        try:
            directory = await asyncio.to_thread(
                self._source.browse_pvc,
                self._resource,
                path=path,
            )
        except Exception as error:  # noqa: BLE001 - 浏览器必须显示适配器错误
            if self.is_mounted:
                self.query_one("#volume-browser-status", Static).update(
                    f"读取失败：{error}"
                )
            return
        finally:
            self._loading = False
        if not self.is_mounted:
            return
        self._directory = directory
        target = directory.target
        self.query_one("#volume-browser-target", Static).update(
            f" Pod {target.pod_name} · Container {target.container_name}"
            f" · Mount {target.mount_path}"
        )
        self.query_one("#volume-browser-path", Static).update(
            f" /{directory.path.removeprefix('.').lstrip('/')}"
        )
        table = self.query_one("#volume-browser-table", DataTable)
        table.clear()
        for entry in directory.entries:
            table.add_row(
                entry.name,
                _volume_entry_label(entry.kind),
                _format_size(entry.size_bytes),
                key=entry.name,
            )
        self.query_one("#volume-browser-status", Static).update(
            f"{len(directory.entries)} 个条目 · 只读"
        )
        table.focus()

    def _open_file_preview(self, path: str) -> None:
        if self._preview_loading:
            self.query_one("#volume-browser-status", Static).update(
                "上一文件预览仍在结束，请稍候"
            )
            return
        self._preview_loading = True
        viewer = ResourceViewer(
            loading_title=f"PVC/{self._resource.name} · {path}",
        )
        self.app.push_screen(viewer)
        self.app.call_after_refresh(
            self._start_file_preview,
            viewer=viewer,
            path=path,
        )

    def _start_file_preview(
        self,
        *,
        viewer: ResourceViewer,
        path: str,
    ) -> None:
        self.app.run_worker(
            self._load_file_preview(viewer=viewer, path=path),
            group="volume-preview",
            exit_on_error=False,
        )

    async def _load_file_preview(
        self,
        *,
        viewer: ResourceViewer,
        path: str,
    ) -> None:
        try:
            content = await asyncio.to_thread(
                self._source.preview_pvc_file,
                self._resource,
                path=path,
            )
        except Exception as error:  # noqa: BLE001 - 文件预览必须恢复 exec 异常
            if viewer.is_mounted:
                viewer.display_error(str(error))
        else:
            if viewer.is_mounted:
                viewer.display_content(content)
        finally:
            self._preview_loading = False

    def _selected_entry(self) -> tuple[VolumeEntry, str] | None:
        directory = self._directory
        table = self.query_one("#volume-browser-table", DataTable)
        if directory is None or not 0 <= table.cursor_row < len(directory.entries):
            return None
        entry = directory.entries[table.cursor_row]
        return entry, _child_volume_path(directory.path, entry.name)


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
        theme = self.app.current_theme
        selected_color = theme.accent or theme.primary
        idle_color = theme.foreground or theme.primary
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
                f"[bold {selected_color}]{shortcut} {label}[/]"
                if selected
                else f"[{idle_color}]{shortcut} {label}[/]"
            )
            for shortcut, label, selected in labels
        ]
        tabs.append(f"[{idle_color}]↑/↓ Enter 打开[/]")
        self.query_one("#monitor-tabs", Static).update("  ".join(tabs))

    def _render_table(self) -> None:
        table = self.query_one("#monitor-table", DataTable)
        selected_row_key = _selected_row_key(table)
        table.clear(columns=True)
        snapshot = self._snapshot
        if self._kind is None:
            table.add_columns("RESOURCE", "COUNT", "READY", "STATUS")
            if snapshot is not None:
                theme = self.app.current_theme
                for collection in snapshot.resources:
                    count, ready, status, healthy = _collection_status(collection)
                    table.add_row(
                        collection.label,
                        count,
                        ready,
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
                table.add_column("RESOURCE")
            else:
                table.add_columns(*collection.columns)
                if collection.error is not None:
                    table.add_row(
                        Text(
                            "Unavailable",
                            style=self.app.current_theme.warning,
                        ),
                        collection.error,
                        *("-" for _ in collection.columns[2:]),
                    )
                else:
                    theme = self.app.current_theme
                    for row in collection.rows:
                        name_style = (
                            theme.warning
                            if row.healthy is False
                            else theme.success
                            if row.healthy is True
                            else theme.foreground
                        )
                        values = (
                            Text(row.values[0], style=name_style),
                            *row.values[1:],
                        )
                        table.add_row(
                            *values,
                            key=f"{row.ref.kind.value}:{row.ref.name}",
                        )
        _restore_selected_row(table, selected_row_key)

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


def _child_volume_path(parent: str, name: str) -> str:
    return name if parent == "." else posixpath.join(parent, name)


def _volume_entry_label(kind: VolumeEntryKind) -> str:
    return {
        VolumeEntryKind.DIRECTORY: "Directory",
        VolumeEntryKind.FILE: "File",
        VolumeEntryKind.SYMLINK: "Symlink",
        VolumeEntryKind.OTHER: "Other",
    }[kind]


def _format_size(size_bytes: int | None) -> str:
    if size_bytes is None:
        return "-"
    units = ("B", "KiB", "MiB", "GiB", "TiB")
    size = float(size_bytes)
    for unit in units:
        if size < 1024 or unit == units[-1]:
            return f"{size:.0f} {unit}" if unit == "B" else f"{size:.1f} {unit}"
        size /= 1024
    return f"{size_bytes} B"


def _download_result_status(result: DownloadResult) -> str:
    return (
        f"下载完成 · {_format_size(result.size_bytes)} · "
        f"SHA-256 {result.sha256} · {result.path}"
    )
