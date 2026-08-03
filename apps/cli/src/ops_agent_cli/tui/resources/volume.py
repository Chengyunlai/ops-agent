import asyncio
import posixpath
from typing import ClassVar, Protocol

from ops_agent.monitoring import (
    KubernetesResourceContent,
    KubernetesResourceRef,
    VolumeDirectory,
    VolumeEntry,
    VolumeEntryKind,
)
from textual import on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import DataTable, Static

from ops_agent_cli.manual_access import DownloadResult
from ops_agent_cli.tui.resources.viewer import ResourceViewer


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

    @work(group="volume-download", exclusive=True, exit_on_error=False)
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

    @work(group="volume-directory", exit_on_error=False)
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
        viewer = ResourceViewer(loading_title=f"PVC/{self._resource.name} · {path}")
        self.app.push_screen(viewer)
        self.app.call_after_refresh(
            self._start_file_preview,
            viewer=viewer,
            path=path,
        )

    def _start_file_preview(self, *, viewer: ResourceViewer, path: str) -> None:
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
