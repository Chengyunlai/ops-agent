from dataclasses import dataclass
from enum import StrEnum
from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static


class PodAction(StrEnum):
    DOWNLOAD = "download"
    INTERACTIVE_SESSION = "interactive-session"


@dataclass(frozen=True)
class PodAccessRequest:
    action: PodAction
    pod_name: str
    container_name: str
    remote_path: str | None = None


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

    #pod-access-error {
        height: auto;
        min-height: 1;
        color: $text-error;
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
        action: PodAction,
        environment: str,
        namespace: str,
        pod_name: str,
        containers: tuple[str, ...],
    ) -> None:
        super().__init__()
        self._action = action
        self._environment = environment
        self._namespace = namespace
        self._pod_name = pod_name
        self._containers = containers

    def compose(self) -> ComposeResult:
        is_shell = self._action is PodAction.INTERACTIVE_SESSION
        title = (
            "INTERACTIVE POD SESSION · 人工终端"
            if is_shell
            else "POD ARTIFACT DOWNLOAD · 只读传输"
        )
        with Vertical(id="pod-access-dialog"):
            yield Static(f" {title}", id="pod-access-title")
            with Vertical(id="pod-access-content"):
                yield Static(
                    f"Environment: {self._environment}\n"
                    f"Namespace: {self._namespace}\n"
                    f"Pod: {self._pod_name}"
                )
                yield Label("Container")
                yield Select(
                    tuple((name, name) for name in self._containers),
                    value=self._containers[0],
                    allow_blank=False,
                    id="pod-access-container",
                )
                if is_shell:
                    yield Static(
                        "⚠ 进入后拥有该容器中的实际写能力；命令不会经过 AI，"
                        "也不会被只读策略保护。仅在确认目标无误后继续。",
                        id="pod-access-warning",
                    )
                else:
                    yield Label("容器内绝对文件路径")
                    yield Input(
                        placeholder="/var/log/app.log",
                        id="pod-access-remote-path",
                    )
                yield Static(id="pod-access-error")
            with Horizontal(id="pod-access-actions"):
                yield Button("取消", id="pod-access-cancel")
                yield Button(
                    "确认进入" if is_shell else "下载",
                    id="pod-access-confirm",
                    variant="warning" if is_shell else "primary",
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
        remote_path = None
        if self._action is PodAction.DOWNLOAD:
            remote_path = self.query_one(
                "#pod-access-remote-path",
                Input,
            ).value.strip()
            if not remote_path.startswith("/"):
                self.query_one("#pod-access-error", Static).update(
                    "请输入容器内绝对文件路径，例如 /var/log/app.log"
                )
                return
        self.dismiss(
            PodAccessRequest(
                action=self._action,
                pod_name=self._pod_name,
                container_name=container,
                remote_path=remote_path,
            )
        )
