from collections.abc import Callable
from typing import ClassVar

from pydantic import ValidationError
from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from ops_agent_cli.configuration import (
    DownloadSettings,
    InteractiveExecSettings,
    KubernetesSettings,
    PodTransferSettings,
    PodTransferStrategy,
    ProjectSettings,
    Settings,
    ThemeName,
    TuiColorSettings,
    TuiSettings,
)


class SettingsScreen(ModalScreen[Settings | None]):
    """编辑配置文件中的 Project Profile 与 TUI 外观。"""

    CSS = """
    SettingsScreen {
        align: center middle;
        background: $background 75%;
    }

    #settings-dialog {
        width: 92%;
        max-width: 110;
        height: 92%;
        background: $surface;
        border: solid $primary;
    }

    #settings-title {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $accent;
        text-style: bold;
    }

    #settings-scroll {
        height: 1fr;
        padding: 1 2;
    }

    .settings-section {
        height: auto;
        margin-bottom: 1;
        color: $secondary;
        text-style: bold;
    }

    .settings-field {
        height: auto;
        margin-bottom: 1;
    }

    .settings-field Label {
        height: 1;
        color: $text-muted;
    }

    .settings-field Input,
    .settings-field Select {
        width: 1fr;
    }

    #settings-note {
        height: auto;
        margin-bottom: 1;
        color: $text-warning;
    }

    #settings-error {
        height: auto;
        min-height: 1;
        color: $text-error;
    }

    #settings-actions {
        height: 3;
        padding: 0 1;
        align-horizontal: right;
        background: $panel;
    }

    #settings-actions Button {
        margin-left: 1;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "取消", priority=True),
    ]

    def __init__(
        self,
        *,
        settings: Settings,
        preview_theme: Callable[[TuiSettings], None],
    ) -> None:
        super().__init__()
        self._settings = settings
        self._preview_theme = preview_theme

    def compose(self) -> ComposeResult:
        kubernetes = self._settings.kubernetes
        colors = self._settings.tui.colors
        with Vertical(id="settings-dialog"):
            yield Static(" SETTINGS · 项目与界面配置", id="settings-title")
            with VerticalScroll(id="settings-scroll"):
                yield Static("Project Profile", classes="settings-section")
                yield from _field(
                    "项目名称",
                    Input(
                        value=self._settings.project.name,
                        id="setting-project-name",
                    ),
                )
                yield from _field(
                    "环境",
                    Input(value=kubernetes.environment, id="setting-environment"),
                )
                yield from _field(
                    "Kubernetes Namespace",
                    Input(value=kubernetes.namespace, id="setting-namespace"),
                )
                yield from _field(
                    "Kubeconfig 路径",
                    Input(
                        value=str(kubernetes.kubeconfig_path),
                        id="setting-kubeconfig",
                    ),
                )
                yield from _field(
                    "API 代理（可留空）",
                    Input(
                        value=(
                            str(kubernetes.proxy_url)
                            if kubernetes.proxy_url is not None
                            else ""
                        ),
                        id="setting-proxy",
                    ),
                )
                yield from _field(
                    "请求超时（秒）",
                    Input(
                        value=str(kubernetes.request_timeout_seconds),
                        id="setting-timeout",
                    ),
                )
                yield Static("人工 Pod 访问", classes="settings-section")
                yield from _field(
                    "Interactive Pod Session",
                    Select(
                        (
                            ("禁用（默认）", "disabled"),
                            ("启用", "enabled"),
                        ),
                        value=(
                            "enabled"
                            if kubernetes.interactive_exec.enabled
                            else "disabled"
                        ),
                        allow_blank=False,
                        id="setting-interactive-exec",
                    ),
                )
                yield from _field(
                    "Pod Shell UTF-8 Locale",
                    Input(
                        value=kubernetes.interactive_exec.locale,
                        id="setting-interactive-locale",
                    ),
                )
                yield from _field(
                    "Pod Shell TERM",
                    Input(
                        value=kubernetes.interactive_exec.terminal_type,
                        id="setting-interactive-terminal-type",
                    ),
                )
                yield from _field(
                    "Pod Shell 彩色 ls",
                    Select(
                        (
                            ("启用（默认）", "enabled"),
                            ("禁用", "disabled"),
                        ),
                        value=(
                            "enabled"
                            if kubernetes.interactive_exec.color
                            else "disabled"
                        ),
                        allow_blank=False,
                        id="setting-interactive-color",
                    ),
                )
                yield from _field(
                    "Artifact Download 本机目录",
                    Input(
                        value=str(kubernetes.downloads.directory),
                        id="setting-download-directory",
                    ),
                )
                yield from _field(
                    "Pod 文件传输策略",
                    Select(
                        (
                            ("自动探测（推荐）", PodTransferStrategy.AUTO.value),
                            ("exec + cat", PodTransferStrategy.EXEC_CAT.value),
                            ("exec + dd", PodTransferStrategy.EXEC_DD.value),
                        ),
                        value=kubernetes.pod_transfer.strategy.value,
                        allow_blank=False,
                        id="setting-pod-transfer-strategy",
                    ),
                )
                yield from _field(
                    "Pod 单文件下载上限（MiB）",
                    Input(
                        value=str(kubernetes.pod_transfer.max_file_size_mb),
                        id="setting-pod-transfer-max-size",
                    ),
                )
                yield Static(
                    "环境、集群连接与人工访问配置保存后，重启应用生效。"
                    " Interactive Pod Session 可修改容器，请谨慎启用。",
                    id="settings-note",
                )

                yield Static("主题与颜色", classes="settings-section")
                yield from _field(
                    "预设主题",
                    Select(
                        (
                            ("Ops Dark", ThemeName.OPS_DARK.value),
                            ("Light", ThemeName.LIGHT.value),
                            ("High Contrast", ThemeName.HIGH_CONTRAST.value),
                        ),
                        value=self._settings.tui.theme.value,
                        allow_blank=False,
                        id="setting-theme",
                    ),
                )
                yield from _color_field("主色", "primary", colors.primary)
                yield from _color_field("强调色", "accent", colors.accent)
                yield from _color_field("背景色", "background", colors.background)
                yield from _color_field("文字色", "foreground", colors.foreground)
                yield from _color_field("警告色", "warning", colors.warning)
                yield Button(
                    "恢复主题默认颜色",
                    id="settings-reset-theme",
                    variant="default",
                )
                yield Static(id="settings-error")
            with Horizontal(id="settings-actions"):
                yield Button("取消", id="settings-cancel")
                yield Button("保存", id="settings-save", variant="primary")

    @on(Select.Changed, "#setting-theme")
    @on(Input.Changed, ".theme-color")
    def preview_theme(self) -> None:
        try:
            self._preview_theme(self._read_tui_settings())
        except (ValidationError, ValueError):
            return

    @on(Button.Pressed)
    def handle_button(self, event: Button.Pressed) -> None:
        if event.button.id == "settings-save":
            self._save()
        elif event.button.id == "settings-cancel":
            self.action_cancel()
        elif event.button.id == "settings-reset-theme":
            for color_input in self.query(".theme-color").nodes:
                if isinstance(color_input, Input):
                    color_input.value = ""
            self._preview_theme(self._read_tui_settings())

    def action_cancel(self) -> None:
        self._preview_theme(self._settings.tui)
        self.dismiss(None)

    def _save(self) -> None:
        try:
            timeout = int(self.query_one("#setting-timeout", Input).value)
            project = ProjectSettings(
                name=self.query_one("#setting-project-name", Input).value,
            )
            proxy_value = self.query_one("#setting-proxy", Input).value.strip()
            interactive_exec = InteractiveExecSettings(
                enabled=(
                    self.query_one(
                        "#setting-interactive-exec",
                        Select,
                    ).value
                    == "enabled"
                ),
                locale=self.query_one(
                    "#setting-interactive-locale",
                    Input,
                ).value,
                terminal_type=self.query_one(
                    "#setting-interactive-terminal-type",
                    Input,
                ).value,
                color=(
                    self.query_one(
                        "#setting-interactive-color",
                        Select,
                    ).value
                    == "enabled"
                ),
            )
            downloads = DownloadSettings(
                directory=self.query_one(
                    "#setting-download-directory",
                    Input,
                ).value,
            )
            pod_transfer = PodTransferSettings(
                strategy=PodTransferStrategy(
                    str(
                        self.query_one(
                            "#setting-pod-transfer-strategy",
                            Select,
                        ).value
                    )
                ),
                max_file_size_mb=int(
                    self.query_one(
                        "#setting-pod-transfer-max-size",
                        Input,
                    ).value
                ),
            )
            kubernetes = KubernetesSettings(
                environment=self.query_one("#setting-environment", Input).value,
                namespace=self.query_one("#setting-namespace", Input).value,
                kubeconfig_path=self.query_one("#setting-kubeconfig", Input).value,
                request_timeout_seconds=timeout,
                proxy_url=proxy_value or None,
                interactive_exec=interactive_exec,
                downloads=downloads,
                pod_transfer=pod_transfer,
            )
            updated = Settings(
                project=project,
                kubernetes=kubernetes,
                model=self._settings.model,
                tui=self._read_tui_settings(),
            )
        except (ValidationError, ValueError) as error:
            self.query_one("#settings-error", Static).update(
                f"配置无效：{_first_error(error)}"
            )
            return
        self.dismiss(updated)

    def _read_tui_settings(self) -> TuiSettings:
        selected_theme = self.query_one("#setting-theme", Select).value
        color_values: dict[str, str | None] = {}
        for field in (
            "primary",
            "accent",
            "background",
            "foreground",
            "warning",
        ):
            value = self.query_one(
                f"#setting-color-{field}",
                Input,
            ).value.strip()
            color_values[field] = value or None
        colors = TuiColorSettings(**color_values)
        return TuiSettings(
            theme=ThemeName(str(selected_theme)),
            colors=colors,
        )


def _field(label: str, widget: Input | Select[str]):
    with Vertical(classes="settings-field"):
        yield Label(label)
        yield widget


def _color_field(label: str, field: str, value: str | None):
    yield from _field(
        f"{label}（#RRGGBB，可留空）",
        Input(
            value=value or "",
            placeholder="#RRGGBB",
            id=f"setting-color-{field}",
            classes="theme-color",
        ),
    )


def _first_error(error: ValidationError | ValueError) -> str:
    if isinstance(error, ValidationError):
        issue = error.errors(include_url=False)[0]
        location = ".".join(str(part) for part in issue["loc"])
        return f"{location}: {issue['msg']}"
    return str(error)
