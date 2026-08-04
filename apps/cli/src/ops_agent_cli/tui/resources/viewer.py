from typing import ClassVar, cast

from ops_agent.monitoring import KubernetesResourceContent
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, RichLog, Static

from ops_agent_cli.tui.resources.contracts import CopyModeController


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
            " 复制模式 · 直接用鼠标拖选复制 · Esc 恢复鼠标控制"
            if self._copy_mode
            else " Esc/q 返回 · ↑/↓/PgUp/PgDn 滚动 · F2 备用"
        )
