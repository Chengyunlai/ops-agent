import asyncio
from collections.abc import Callable, Iterator
from enum import StrEnum
from typing import ClassVar, Protocol

from ops_agent.agent import AgentEvent, AgentStage, ApplicationError
from ops_agent.monitoring import (
    KubernetesMonitorSnapshot,
    KubernetesResourceContent,
    KubernetesResourceKind,
    KubernetesResourceRef,
    VolumeDirectory,
)
from ops_agent.settings import Settings, TuiSettings
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Button, DataTable, Input, Static

from ops_agent_cli.pod_access import (
    DownloadResult,
    InteractiveSessionResult,
    KubectlPodAccess,
)
from ops_agent_cli.tui.chat import ChatTranscript
from ops_agent_cli.tui.monitor import MonitorPane, ResourceViewer, VolumeBrowser
from ops_agent_cli.tui.pod_access import (
    PodAccessDialog,
    PodAccessRequest,
    PodAction,
)
from ops_agent_cli.tui.settings import SettingsScreen
from ops_agent_cli.tui.terminal import set_terminal_mouse_capture
from ops_agent_cli.tui.themes import build_theme


class Conversation(Protocol):
    def stream(self, question: str) -> Iterator[AgentEvent]: ...


class Monitor(Protocol):
    def snapshot(self) -> KubernetesMonitorSnapshot: ...

    def describe(
        self,
        resource: KubernetesResourceRef,
    ) -> KubernetesResourceContent: ...

    def pod_logs(
        self,
        resource: KubernetesResourceRef,
        *,
        tail_lines: int = 200,
    ) -> KubernetesResourceContent: ...

    def pod_containers(
        self,
        resource: KubernetesResourceRef,
    ) -> tuple[str, ...]: ...

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


class PodAccess(Protocol):
    def download_pod_file(
        self,
        *,
        pod_name: str,
        container_name: str,
        remote_path: str,
    ) -> DownloadResult: ...

    def download_pvc_file(
        self,
        *,
        claim_name: str,
        pod_name: str,
        container_name: str,
        mount_path: str,
        relative_path: str,
    ) -> DownloadResult: ...

    def interactive_session(
        self,
        *,
        pod_name: str,
        container_name: str,
    ) -> InteractiveSessionResult: ...


class ResourceOperation(StrEnum):
    DESCRIBE = "describe"
    LOGS = "logs"


class QuestionInput(Input):
    """保留普通文本输入，同时让全局帮助键优先于输入框。"""

    def check_consume_key(self, key: str, character: str | None) -> bool:
        if key in {"escape", "question_mark"}:
            return False
        return super().check_consume_key(key, character)


class OpsAgentTui(App[None]):
    """左侧实时监盘与人工操作、右侧受控对话的运维终端。"""

    TITLE = "Ops Agent"
    SUB_TITLE = "Kubernetes 监盘与受控诊断"
    MONITOR_REFRESH_SECONDS = 5.0
    NARROW_WIDTH = 100

    CSS = """
    Screen {
        layout: vertical;
        background: $background;
        color: $text;
    }

    #topbar {
        height: 1;
        background: $primary;
    }

    #context {
        height: 1;
        width: 1fr;
        padding: 0 1;
        background: $primary;
        color: $primary-background;
        text-style: bold;
    }

    #copy-button,
    #settings-button {
        height: 1;
        min-height: 1;
        padding: 0 1;
        border: none;
        background: $primary;
        color: $primary-background;
        text-style: bold;
    }

    #copy-button {
        width: 9;
        min-width: 9;
    }

    #settings-button {
        width: 12;
        min-width: 12;
    }

    #copy-button:hover,
    #copy-button:focus,
    #settings-button:hover,
    #settings-button:focus {
        background: $accent;
        color: $background;
    }

    #help {
        display: none;
        height: auto;
        max-height: 7;
        padding: 0 1;
        background: $panel;
        color: $text;
        border-top: solid $accent;
        border-bottom: solid $accent;
    }

    #help.visible {
        display: block;
    }

    #copy-mode-banner {
        display: none;
        height: 1;
        padding: 0 1;
        background: $warning;
        color: $background;
        text-style: bold;
    }

    #copy-mode-banner.visible {
        display: block;
    }

    #workspace {
        height: 1fr;
        layout: horizontal;
    }

    #monitor-pane {
        width: 3fr;
        height: 1fr;
        background: $surface;
        border-right: solid $panel-lighten-1;
    }

    #monitor-title,
    #chat-title {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $accent;
        text-style: bold;
    }

    #monitor-tabs {
        height: 1;
        padding: 0 1;
        background: $surface-darken-1;
    }

    #monitor-table {
        height: 1fr;
        background: $surface;
        color: $text;
        scrollbar-color: $primary;
        scrollbar-color-hover: $secondary;
        scrollbar-color-active: $accent;
    }

    #monitor-table > .datatable--header {
        background: $panel;
        color: $secondary;
    }

    #monitor-table > .datatable--even-row {
        background: $surface-darken-1;
    }

    #monitor-table > .datatable--cursor {
        background: $primary-muted;
        color: $text;
    }

    #monitor-status {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
    }

    ResourceViewer {
        align: center middle;
        background: $background 70%;
    }

    #resource-viewer {
        width: 92%;
        height: 88%;
        background: $surface;
        border: solid $primary;
    }

    #resource-title {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $accent;
        text-style: bold;
    }

    #resource-content {
        height: 1fr;
        padding: 0 1;
        background: $surface;
        color: $text;
        scrollbar-color: $primary;
        scrollbar-color-hover: $secondary;
        scrollbar-color-active: $accent;
    }

    #resource-footer {
        height: 1;
        background: $primary;
        color: $primary-background;
    }

    #resource-footer-text {
        width: 1fr;
        height: 1;
        padding: 0 1;
        background: $primary;
        color: $primary-background;
        text-style: bold;
    }

    #resource-copy-button {
        width: 9;
        min-width: 9;
        height: 1;
        min-height: 1;
        padding: 0 1;
        border: none;
        background: $primary;
        color: $primary-background;
        text-style: bold;
    }

    #resource-copy-button:hover,
    #resource-copy-button:focus {
        background: $accent;
        color: $background;
    }

    VolumeBrowser {
        align: center middle;
        background: $background 70%;
    }

    #volume-browser {
        width: 92%;
        height: 88%;
        background: $surface;
        border: solid $primary;
    }

    #volume-browser-title {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $accent;
        text-style: bold;
    }

    #volume-browser-target,
    #volume-browser-path,
    #volume-browser-status {
        height: 1;
        padding: 0 1;
    }

    #volume-browser-target {
        color: $text-muted;
    }

    #volume-browser-path {
        background: $panel;
        color: $text;
        text-style: bold;
    }

    #volume-browser-table {
        height: 1fr;
        background: $surface;
        scrollbar-color: $primary;
        scrollbar-color-hover: $secondary;
        scrollbar-color-active: $accent;
    }

    #volume-browser-status {
        color: $text-muted;
    }

    #volume-browser-footer {
        height: 1;
        padding: 0 1;
        background: $primary;
        color: $primary-background;
        text-style: bold;
    }

    #chat-pane {
        width: 2fr;
        height: 1fr;
        background: $background;
    }

    #result {
        height: 1fr;
        padding: 0 1;
        background: $background;
        color: $text;
        overflow-y: auto;
        scrollbar-color: $primary;
        scrollbar-color-hover: $secondary;
        scrollbar-color-active: $accent;
    }

    #result MarkdownH1,
    #result MarkdownH2,
    #result MarkdownH3 {
        color: $secondary;
        text-style: bold;
    }

    #result MarkdownTable {
        background: $surface;
    }

    #result MarkdownTableContent {
        color: $text;
    }

    #status {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-primary;
    }

    #question {
        height: 3;
        padding: 0 1;
        border: tall $primary;
        background: $surface;
        color: $text;
    }

    #question:focus {
        border: tall $accent;
    }

    #question > .input--placeholder,
    #question > .input--suggestion {
        color: $text-muted;
    }

    #hotkeys,
    #hotkeys-compact {
        height: 1;
        padding: 0 1;
        background: $primary;
        color: $primary-background;
        text-style: bold;
    }

    #hotkeys-compact {
        display: none;
    }

    Screen.narrow #workspace {
        layout: vertical;
    }

    Screen.narrow #monitor-pane {
        width: 1fr;
        height: 1fr;
        border-right: none;
        border-bottom: solid $panel-lighten-1;
    }

    Screen.narrow #chat-pane {
        width: 1fr;
        height: 1fr;
    }

    Screen.narrow #hotkeys {
        display: none;
    }

    Screen.narrow #hotkeys-compact {
        display: block;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("ctrl+c", "quit", "退出", priority=True),
        Binding("f1", "toggle_help", "帮助", priority=True),
        Binding(
            "question_mark",
            "toggle_help",
            "帮助",
            priority=True,
        ),
        Binding("ctrl+l", "clear_display", "清空显示", priority=True),
        Binding("ctrl+r", "refresh_monitor", "刷新监盘", priority=True),
        Binding("ctrl+k", "focus_monitor", "资源监盘", priority=True),
        Binding("ctrl+comma", "open_settings", "设置", priority=True),
        Binding("f2", "toggle_copy_mode", "复制模式", priority=True),
        Binding("0", "show_overview", "Overview", show=False),
        Binding("1", "show_pods", "Pods", show=False),
        Binding("2", "show_deployments", "Deployments", show=False),
        Binding("3", "show_stateful_sets", "StatefulSets", show=False),
        Binding("4", "show_daemon_sets", "DaemonSets", show=False),
        Binding("5", "show_services", "Services", show=False),
        Binding("6", "show_replica_sets", "ReplicaSets", show=False),
        Binding("7", "show_storage", "Storage", show=False),
        Binding("d", "describe_resource", "Describe", show=False),
        Binding("l", "show_logs", "Logs", show=False),
        Binding("f", "browse_storage", "Browse PVC", show=False),
        Binding("g", "download_pod_file", "Pod Download", show=False),
        Binding("x", "interactive_pod_session", "Pod Shell", show=False),
        Binding("q", "quit", "退出"),
        Binding("i", "focus_question", "输入"),
        Binding(
            "escape",
            "command_mode",
            "命令模式",
            show=False,
        ),
    ]

    def __init__(
        self,
        *,
        conversation: Conversation,
        monitor: Monitor,
        environment: str,
        namespace: str,
        settings: Settings,
        save_settings: Callable[[Settings], None],
        pod_access: PodAccess | None = None,
    ) -> None:
        super().__init__()
        self._conversation = conversation
        self._monitor = monitor
        self._environment = environment
        self._namespace = namespace
        self._settings = settings
        self._save_settings = save_settings
        self._pod_access = pod_access or KubectlPodAccess(settings.kubernetes)
        self._busy = False
        self._monitor_refresh_in_progress = False
        self._monitor_refresh_pending = False
        self._copy_mode = False

    def compose(self) -> ComposeResult:
        with Horizontal(id="topbar"):
            yield Static(
                self._context_text(),
                id="context",
            )
            yield Button("复制", id="copy-button", compact=True, flat=True)
            yield Button("⚙ Settings", id="settings-button", compact=True, flat=True)
        yield Static(
            "全局：Ctrl+C 退出 · F1/? 帮助 · 顶部“复制”（F2 备用）· Ctrl+, 设置"
            " · Ctrl+R 刷新 · Ctrl+K 聚焦监盘\n"
            "聊天：Enter 提交 · i 返回输入 · Ctrl+L 清空右侧显示\n"
            "监盘：0 总览 · 1~7 切换资源 · Enter 打开 · d 详情"
            " · l Pod 日志 · f PVC 目录 · g Pod 文件下载"
            " · x 人工 Pod Shell · PVC 浏览器内 s 下载 · q 退出",
            id="help",
        )
        yield Static(
            " COPY MODE · 现在直接用鼠标拖选复制 · 按 Esc 恢复仪表盘鼠标控制",
            id="copy-mode-banner",
        )
        with Horizontal(id="workspace"):
            yield MonitorPane(id="monitor-pane")
            with Vertical(id="chat-pane"):
                yield Static(" OPS COPILOT · CONTROLLED CHAT", id="chat-title")
                yield ChatTranscript(namespace=self._namespace, id="result")
                yield Static("就绪", id="status")
                yield QuestionInput(
                    placeholder="› 询问当前环境、资源状态或故障原因",
                    id="question",
                )
        yield Static(
            " ^K 监盘  0 总览  1 Pods  2 Deploy  3 Stateful"
            "  4 Daemon  5 Services  6 Replica  7 Storage"
            "  │ d 详情  l 日志  f 目录  g 下载  x Shell  i 聊天",
            id="hotkeys",
        )
        yield Static(
            " ^K 监盘 │ 0~7 资源 │ d 详情 │ l 日志 │ f 目录 │ g 下载 │ x Shell"
            " │ i 聊天 │ 顶部复制 │ F1 帮助",
            id="hotkeys-compact",
        )

    def on_mount(self) -> None:
        self._apply_theme(self._settings.tui)
        self.query_one("#question", Input).focus()
        self._apply_responsive_layout(self.size.width)
        self._request_monitor_refresh()
        self.set_interval(
            self.MONITOR_REFRESH_SECONDS,
            self._request_monitor_refresh,
            name="monitor-refresh",
        )

    def on_resize(self, event: events.Resize) -> None:
        self._apply_responsive_layout(event.size.width)

    @on(Input.Submitted, "#question")
    def submit_question(self, event: Input.Submitted) -> None:
        question = event.value.strip()
        if self._busy or not question:
            return

        self._busy = True
        event.input.disabled = True
        self.query_one("#status", Static).update("诊断中…")
        self.query_one("#result", ChatTranscript).begin_exchange(question)
        self._ask_agent(question)

    @work(
        exclusive=True,
        group="conversation",
        exit_on_error=False,
    )
    async def _ask_agent(self, question: str) -> None:
        try:
            events = iter(self._conversation.stream(question))
            while True:
                event = await asyncio.to_thread(_next_event, events)
                if event is None:
                    raise ApplicationError("Agent 事件流未返回最终回答")
                if event.stage is AgentStage.COMPLETED:
                    if event.answer is None:
                        raise ApplicationError("Agent 完成事件缺少回答")
                    self._finish_with_answer(event.answer)
                    return
                self.query_one("#status", Static).update(event.message)
        except Exception as error:  # noqa: BLE001 - TUI 必须恢复应用边界异常
            self._finish_with_error(str(error))

    @work(
        group="monitor",
        exit_on_error=False,
    )
    async def _refresh_monitor(self) -> None:
        pane = self.query_one("#monitor-pane", MonitorPane)
        try:
            snapshot = await asyncio.to_thread(self._monitor.snapshot)
        except Exception as error:  # noqa: BLE001 - 周期刷新必须可恢复
            pane.display_error(str(error))
        else:
            pane.display_snapshot(snapshot)
        finally:
            self._monitor_refresh_in_progress = False
            if self._monitor_refresh_pending and self.is_running:
                self._monitor_refresh_pending = False
                self.call_next(self._request_monitor_refresh)

    def _finish_with_answer(self, answer: str) -> None:
        self.query_one("#result", ChatTranscript).complete_exchange(answer)
        self.query_one("#status", Static).update("完成")
        self._reset_question()

    def _finish_with_error(self, message: str) -> None:
        self.query_one("#result", ChatTranscript).fail_exchange(message)
        self.query_one("#status", Static).update("失败")
        self._reset_question()

    def _reset_question(self) -> None:
        self._busy = False
        question = self.query_one("#question", Input)
        question.disabled = False
        question.value = ""
        question.focus()

    def _apply_responsive_layout(self, width: int) -> None:
        self.screen.set_class(width < self.NARROW_WIDTH, "narrow")

    def action_toggle_help(self) -> None:
        self.query_one("#help", Static).toggle_class("visible")

    def action_clear_display(self) -> None:
        self.query_one("#result", ChatTranscript).reset_transcript()
        if not self._busy:
            self.query_one("#status", Static).update("显示已清空 · 会话上下文仍保留")

    def action_refresh_monitor(self) -> None:
        self._request_monitor_refresh()

    def action_focus_monitor(self) -> None:
        self.query_one("#monitor-pane", MonitorPane).focus_table()
        if not self._busy:
            self.query_one("#status", Static).update(
                "监盘模式 · ↑/↓ 选择 · Enter/d 详情 · l Pod 日志 · i 聊天"
            )

    def action_open_settings(self) -> None:
        self.push_screen(
            SettingsScreen(
                settings=self._settings,
                preview_theme=self._apply_theme,
            ),
            self._settings_closed,
        )

    def action_toggle_copy_mode(self) -> None:
        self._set_copy_mode(not self._copy_mode)

    def exit_copy_mode(self) -> bool:
        """恢复终端鼠标，并报告是否消费了本次退出操作。"""
        if not self._copy_mode:
            return False
        return self._set_copy_mode(False)

    def _set_copy_mode(self, enabled: bool) -> bool:
        if not set_terminal_mouse_capture(
            self._driver,
            enabled=not enabled,
        ):
            self.query_one("#status", Static).update(
                "当前终端驱动不支持运行时切换复制模式"
            )
            return False
        self._copy_mode = enabled
        self.query_one("#copy-mode-banner", Static).set_class(
            self._copy_mode,
            "visible",
        )
        if isinstance(self.screen, ResourceViewer):
            self.screen.set_copy_mode(self._copy_mode)
        self.query_one("#status", Static).update(
            "复制模式 · 现在直接鼠标拖选并复制 · Esc 恢复鼠标控制"
            if self._copy_mode
            else "已退出复制模式 · 鼠标控制已恢复"
        )
        return True

    def action_show_overview(self) -> None:
        self.query_one("#monitor-pane", MonitorPane).show_overview()

    def action_show_pods(self) -> None:
        self._show_monitor_kind(KubernetesResourceKind.POD)

    def action_show_deployments(self) -> None:
        self._show_monitor_kind(KubernetesResourceKind.DEPLOYMENT)

    def action_show_stateful_sets(self) -> None:
        self._show_monitor_kind(KubernetesResourceKind.STATEFUL_SET)

    def action_show_daemon_sets(self) -> None:
        self._show_monitor_kind(KubernetesResourceKind.DAEMON_SET)

    def action_show_services(self) -> None:
        self._show_monitor_kind(KubernetesResourceKind.SERVICE)

    def action_show_replica_sets(self) -> None:
        self._show_monitor_kind(KubernetesResourceKind.REPLICA_SET)

    def action_show_storage(self) -> None:
        self._show_monitor_kind(KubernetesResourceKind.PERSISTENT_VOLUME_CLAIM)

    def action_describe_resource(self) -> None:
        pane = self.query_one("#monitor-pane", MonitorPane)
        if pane.open_selected_overview_kind():
            return
        resource = pane.selected_resource()
        if resource is None:
            self.query_one("#status", Static).update("请先在左侧选择一个资源")
            return
        self._open_resource_viewer(
            loading_title=f"Describe · {resource.kind}/{resource.name}",
            operation=ResourceOperation.DESCRIBE,
            resource=resource,
        )

    def action_show_logs(self) -> None:
        resource = self.query_one(
            "#monitor-pane",
            MonitorPane,
        ).selected_resource()
        if resource is None or resource.kind is not KubernetesResourceKind.POD:
            self.query_one("#status", Static).update(
                "Logs 仅适用于 Pod；请切换到 Pods 并选择一行"
            )
            return
        self._open_resource_viewer(
            loading_title=f"Logs · Pod/{resource.name}",
            operation=ResourceOperation.LOGS,
            resource=resource,
        )

    def action_browse_storage(self) -> None:
        resource = self.query_one(
            "#monitor-pane",
            MonitorPane,
        ).selected_resource()
        if (
            resource is None
            or resource.kind is not KubernetesResourceKind.PERSISTENT_VOLUME_CLAIM
        ):
            self.query_one("#status", Static).update(
                "目录浏览仅适用于 PVC；请按 7 进入 Storage 并选择一行"
            )
            return
        self.push_screen(
            VolumeBrowser(
                source=self._monitor,
                downloader=self._pod_access,
                resource=resource,
            )
        )

    def action_download_pod_file(self) -> None:
        self._prepare_pod_action(PodAction.DOWNLOAD)

    def action_interactive_pod_session(self) -> None:
        if not self._settings.kubernetes.interactive_exec.enabled:
            self.query_one("#status", Static).update(
                "Interactive Pod Session 未启用 · 可在 Settings 中配置后重启"
            )
            return
        self._prepare_pod_action(PodAction.INTERACTIVE_SESSION)

    def action_command_mode(self) -> None:
        if self.exit_copy_mode():
            return
        self.action_focus_monitor()

    def action_focus_question(self) -> None:
        self.query_one("#question", Input).focus()
        if not self._busy:
            self.query_one("#status", Static).update("聊天模式 · Enter 发送")

    @on(DataTable.RowSelected, "#monitor-table")
    def open_selected_resource(self) -> None:
        pane = self.query_one("#monitor-pane", MonitorPane)
        if pane.open_selected_overview_kind():
            return
        resource = pane.selected_resource()
        if (
            resource is not None
            and resource.kind is KubernetesResourceKind.PERSISTENT_VOLUME_CLAIM
        ):
            self.action_browse_storage()
            return
        self.action_describe_resource()

    @on(Button.Pressed, "#settings-button")
    def open_settings(self) -> None:
        self.action_open_settings()

    @on(Button.Pressed, "#copy-button")
    def toggle_copy_mode(self) -> None:
        self.action_toggle_copy_mode()

    def _show_monitor_kind(self, kind: KubernetesResourceKind) -> None:
        pane = self.query_one("#monitor-pane", MonitorPane)
        pane.show_kind(kind)
        if not self.query_one("#question", Input).has_focus:
            pane.focus_table()

    def _open_resource_viewer(
        self,
        *,
        loading_title: str,
        operation: ResourceOperation,
        resource: KubernetesResourceRef,
    ) -> None:
        viewer = ResourceViewer(
            loading_title=loading_title,
            copy_mode=self._copy_mode,
        )
        self.push_screen(viewer)
        self.call_after_refresh(
            self._load_resource_content,
            viewer=viewer,
            operation=operation,
            resource=resource,
        )

    @work(
        group="pod-action",
        exclusive=True,
        exit_on_error=False,
    )
    async def _prepare_pod_action(self, action: PodAction) -> None:
        resource = self.query_one(
            "#monitor-pane",
            MonitorPane,
        ).selected_resource()
        if resource is None or resource.kind is not KubernetesResourceKind.POD:
            self.query_one("#status", Static).update(
                "该操作仅适用于 Pod；请按 1 进入 Pods 并选择一行"
            )
            return
        self.query_one("#status", Static).update(
            f"正在读取 Pod/{resource.name} 的容器列表…"
        )
        try:
            containers = await asyncio.to_thread(
                self._monitor.pod_containers,
                resource,
            )
        except Exception as error:  # noqa: BLE001 - 人工操作必须恢复查询异常
            self.query_one("#status", Static).update(f"容器列表读取失败：{error}")
            return
        if not containers:
            self.query_one("#status", Static).update(
                f"Pod/{resource.name} 没有可选择的容器"
            )
            return
        self.push_screen(
            PodAccessDialog(
                action=action,
                environment=self._environment,
                namespace=self._namespace,
                pod_name=resource.name,
                containers=containers,
            ),
            self._pod_access_requested,
        )

    def _pod_access_requested(self, request: PodAccessRequest | None) -> None:
        if request is None:
            self.query_one("#status", Static).update("已取消人工 Pod 操作")
            return
        if request.action is PodAction.DOWNLOAD:
            if request.remote_path is None:
                self.query_one("#status", Static).update("Pod 下载路径缺失")
                return
            self.query_one("#status", Static).update(
                f"正在从 Pod/{request.pod_name} 下载 {request.remote_path}…"
            )
            self._download_pod_file(request)
            return
        self.call_after_refresh(self._run_interactive_session, request)

    @work(
        group="pod-download",
        exclusive=True,
        exit_on_error=False,
    )
    async def _download_pod_file(self, request: PodAccessRequest) -> None:
        try:
            result = await asyncio.to_thread(
                self._pod_access.download_pod_file,
                pod_name=request.pod_name,
                container_name=request.container_name,
                remote_path=request.remote_path,
            )
        except Exception as error:  # noqa: BLE001 - 下载错误必须回显
            self.query_one("#status", Static).update(f"下载失败：{error}")
        else:
            self.query_one("#status", Static).update(_download_completion(result))

    def _run_interactive_session(self, request: PodAccessRequest) -> None:
        self.exit_copy_mode()
        self._render_context(mode="INTERACTIVE POD SESSION · MANUAL WRITE ACCESS")
        self.query_one("#status", Static).update(
            f"人工终端已接管 · {request.pod_name}/{request.container_name}"
        )
        self.refresh()
        try:
            with self.suspend():
                result = self._pod_access.interactive_session(
                    pod_name=request.pod_name,
                    container_name=request.container_name,
                )
        except Exception as error:  # noqa: BLE001 - 终端恢复后必须显示错误
            message = f"Interactive Pod Session 启动失败：{error}"
        else:
            message = f"Interactive Pod Session 已结束 · exit {result.exit_code}"
        finally:
            self._render_context()
        self.query_one("#status", Static).update(message)

    @work(
        group="resource-content",
        exclusive=True,
        exit_on_error=False,
    )
    async def _load_resource_content(
        self,
        *,
        viewer: ResourceViewer,
        operation: ResourceOperation,
        resource: KubernetesResourceRef,
    ) -> None:
        try:
            if operation is ResourceOperation.LOGS:
                content = await asyncio.to_thread(
                    self._monitor.pod_logs,
                    resource,
                    tail_lines=200,
                )
            else:
                content = await asyncio.to_thread(
                    self._monitor.describe,
                    resource,
                )
        except Exception as error:  # noqa: BLE001 - 资源弹窗必须恢复 API 异常
            if viewer.is_mounted:
                viewer.display_error(str(error))
        else:
            if viewer.is_mounted:
                viewer.display_content(content)

    def _request_monitor_refresh(self) -> None:
        if self._monitor_refresh_in_progress:
            self._monitor_refresh_pending = True
            return
        self._monitor_refresh_in_progress = True
        self._refresh_monitor()

    def _apply_theme(self, settings: TuiSettings) -> None:
        theme = build_theme(settings)
        self.register_theme(theme)
        self.theme = theme.name
        if self.is_mounted:
            self.query_one("#monitor-pane", MonitorPane).refresh_theme()

    def _context_text(
        self,
        *,
        mode: str = "MONITOR / AI READ-ONLY / AI只读",
    ) -> str:
        return (
            f" OPS AGENT  Project: {self._settings.project.name}"
            f"  Context: {self._environment}"
            f"  Namespace: {self._namespace}  Mode: {mode}"
        )

    def _render_context(
        self,
        *,
        mode: str = "MONITOR / AI READ-ONLY / AI只读",
    ) -> None:
        self.query_one("#context", Static).update(self._context_text(mode=mode))

    def _settings_closed(self, updated: Settings | None) -> None:
        if updated is None:
            return
        previous = self._settings
        requires_restart = (
            updated.project != previous.project
            or updated.kubernetes != previous.kubernetes
            or updated.model != previous.model
        )
        try:
            self._save_settings(updated)
        except Exception as error:  # noqa: BLE001 - TUI 必须恢复持久化边界异常
            self._apply_theme(previous.tui)
            self.query_one("#status", Static).update(f"配置保存失败：{error}")
            return
        self._settings = updated
        self._apply_theme(updated.tui)
        message = "配置已保存 · 主题已生效"
        if requires_restart:
            message += " · Project Profile 重启生效"
        self.query_one("#status", Static).update(message)


def _next_event(events: Iterator[AgentEvent]) -> AgentEvent | None:
    try:
        return next(events)
    except StopIteration:
        return None


def _download_completion(result: DownloadResult) -> str:
    return (
        f"下载完成 · {result.size_bytes} bytes · "
        f"SHA-256 {result.sha256} · {result.path}"
    )
