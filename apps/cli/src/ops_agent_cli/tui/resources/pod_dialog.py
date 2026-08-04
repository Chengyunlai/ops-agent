from dataclasses import dataclass
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Label, Select, Static


@dataclass(frozen=True)
class PodAccessRequest:
    pod_name: str
    container_name: str


class PodAccessDialog(ModalScreen[PodAccessRequest | None]):
    """为人工 Pod 操作选择容器，并在写操作前明确确认风险。"""

    CSS = """
    PodAccessDialog {
        align: center middle;
        background: $background 75%;
    }

    #pod-access-dialog {
        width: 72;
        height: auto;
        max-height: 24;
        background: $surface;
        border: solid $primary;
    }

    #pod-access-title {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $accent;
        text-style: bold;
    }

    #pod-access-content {
        height: auto;
        padding: 1 2;
    }

    #pod-access-content Label {
        height: 1;
        margin-top: 1;
        color: $text-muted;
    }

    #pod-access-warning {
        height: auto;
        margin-top: 1;
        color: $warning;
        text-style: bold;
    }

    #pod-access-actions {
        height: 3;
        padding: 0 1;
        align-horizontal: right;
        background: $panel;
    }

    #pod-access-actions Button {
        margin-left: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "取消", priority=True),
    ]

    def __init__(
        self,
        *,
        environment: str,
        namespace: str,
        pod_name: str,
        containers: tuple[str, ...],
    ) -> None:
        super().__init__()
        self._environment = environment
        self._namespace = namespace
        self._pod_name = pod_name
        self._containers = containers

    def compose(self) -> ComposeResult:
        with Vertical(id="pod-access-dialog"):
            yield Static(
                " 交互式 Pod 会话 · Shell + 下载",
                id="pod-access-title",
            )
            with Vertical(id="pod-access-content"):
                yield Static(
                    f"环境：{self._environment}\n"
                    f"Namespace：{self._namespace}\n"
                    f"Pod：{self._pod_name}",
                    id="pod-access-context",
                )
                yield Label("容器")
                yield Select(
                    tuple((name, name) for name in self._containers),
                    value=self._containers[0],
                    allow_blank=False,
                    id="pod-access-container",
                )
                yield Static(
                    "⚠ 进入后拥有该容器中的实际写能力；命令不会经过 AI，"
                    "也不会被只读策略保护。\n"
                    "进入后先用 cd/ls/find 定位文件，再执行 "
                    "download <文件> 下载；支持相对路径，本机按 y 确认后传输，"
                    "且不会退出 Shell。",
                    id="pod-access-warning",
                )
            with Horizontal(id="pod-access-actions"):
                yield Button("取消", id="pod-access-cancel")
                yield Button(
                    "确认进入",
                    id="pod-access-confirm",
                    variant="warning",
                )

    def action_cancel(self) -> None:
        self.dismiss(None)

    @on(Button.Pressed)
    def handle_button(self, event: Button.Pressed) -> None:
        if event.button.id == "pod-access-cancel":
            self.action_cancel()
            return
        if event.button.id != "pod-access-confirm":
            return
        container = str(self.query_one("#pod-access-container", Select).value)
        self.dismiss(
            PodAccessRequest(
                pod_name=self._pod_name,
                container_name=container,
            )
        )
