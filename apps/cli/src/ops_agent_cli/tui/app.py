import asyncio
from collections.abc import Iterator
from enum import StrEnum
from typing import ClassVar, Protocol

from ops_agent.agent import AgentEvent, AgentStage, ApplicationError
from ops_agent.monitoring import (
    KubernetesMonitorSnapshot,
    KubernetesResourceContent,
    KubernetesResourceKind,
    KubernetesResourceRef,
)
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import DataTable, Input, Static

from ops_agent_cli.tui.chat import ChatTranscript
from ops_agent_cli.tui.monitor import MonitorPane, ResourceViewer


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
    """左侧实时监盘、右侧受控对话的只读运维终端。"""

    TITLE = "Ops Agent"
    SUB_TITLE = "Kubernetes 只读诊断"
    MONITOR_REFRESH_SECONDS = 5.0
    NARROW_WIDTH = 100

    CSS = """
    Screen {
        layout: vertical;
        background: #070a0d;
        color: #d7dee7;
    }

    #context {
        height: 1;
        padding: 0 1;
        background: #1fb5ad;
        color: #001c1a;
        text-style: bold;
    }

    #help {
        display: none;
        height: auto;
        max-height: 7;
        padding: 0 1;
        background: #21182a;
        color: #f0d7ff;
        border-top: solid #cf79ff;
        border-bottom: solid #cf79ff;
    }

    #help.visible {
        display: block;
    }

    #workspace {
        height: 1fr;
        layout: horizontal;
    }

    #monitor-pane {
        width: 3fr;
        height: 1fr;
        background: #090e13;
        border-right: solid #2a3440;
    }

    #monitor-title,
    #chat-title {
        height: 1;
        padding: 0 1;
        background: #172029;
        color: #ffcc66;
        text-style: bold;
    }

    #monitor-tabs {
        height: 1;
        padding: 0 1;
        background: #0d1319;
    }

    #monitor-table {
        height: 1fr;
        background: #090e13;
        color: #d7dee7;
        scrollbar-color: #1fb5ad;
        scrollbar-color-hover: #51d8d0;
        scrollbar-color-active: #ffcc66;
    }

    #monitor-table > .datatable--header {
        background: #111a22;
        color: #51d8d0;
    }

    #monitor-table > .datatable--even-row {
        background: #0d1319;
    }

    #monitor-table > .datatable--cursor {
        background: #24323e;
        color: #f2f6fa;
    }

    #monitor-status {
        height: 1;
        padding: 0 1;
        background: #111820;
        color: #8fa1b3;
    }

    ResourceViewer {
        align: center middle;
        background: #000000 70%;
    }

    #resource-viewer {
        width: 92%;
        height: 88%;
        background: #090e13;
        border: solid #1fb5ad;
    }

    #resource-title {
        height: 1;
        padding: 0 1;
        background: #172029;
        color: #ffcc66;
        text-style: bold;
    }

    #resource-content {
        height: 1fr;
        padding: 0 1;
        background: #090e13;
        color: #d7dee7;
        scrollbar-color: #1fb5ad;
        scrollbar-color-hover: #51d8d0;
        scrollbar-color-active: #ffcc66;
    }

    #resource-footer {
        height: 1;
        padding: 0 1;
        background: #1fb5ad;
        color: #001c1a;
        text-style: bold;
    }

    #chat-pane {
        width: 2fr;
        height: 1fr;
        background: #070a0d;
    }

    #result {
        height: 1fr;
        padding: 0 1;
        background: #070a0d;
        color: #d7dee7;
        overflow-y: auto;
        scrollbar-color: #1fb5ad;
        scrollbar-color-hover: #51d8d0;
        scrollbar-color-active: #ffcc66;
    }

    #result MarkdownH1,
    #result MarkdownH2,
    #result MarkdownH3 {
        color: #51d8d0;
        text-style: bold;
    }

    #result MarkdownTable {
        background: #0d1319;
    }

    #result MarkdownTableContent {
        color: #d7dee7;
    }

    #status {
        height: 1;
        padding: 0 1;
        background: #172029;
        color: #8ee7e1;
    }

    #question {
        height: 3;
        padding: 0 1;
        border: tall #1fb5ad;
        background: #0d1319;
        color: #f2f6fa;
    }

    #question:focus {
        border: tall #ffcc66;
    }

    #question > .input--placeholder,
    #question > .input--suggestion {
        color: #9aa9b8;
    }

    #hotkeys,
    #hotkeys-compact {
        height: 1;
        padding: 0 1;
        background: #1fb5ad;
        color: #001c1a;
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
        border-bottom: solid #2a3440;
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
        Binding("0", "show_overview", "Overview", show=False),
        Binding("1", "show_pods", "Pods", show=False),
        Binding("2", "show_deployments", "Deployments", show=False),
        Binding("3", "show_stateful_sets", "StatefulSets", show=False),
        Binding("4", "show_daemon_sets", "DaemonSets", show=False),
        Binding("5", "show_services", "Services", show=False),
        Binding("6", "show_replica_sets", "ReplicaSets", show=False),
        Binding("d", "describe_resource", "Describe", show=False),
        Binding("l", "show_logs", "Logs", show=False),
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
    ) -> None:
        super().__init__()
        self._conversation = conversation
        self._monitor = monitor
        self._environment = environment
        self._namespace = namespace
        self._busy = False
        self._monitor_refresh_in_progress = False
        self._monitor_refresh_pending = False

    def compose(self) -> ComposeResult:
        yield Static(
            f" OPS AGENT  Context: {self._environment}"
            f"  Namespace: {self._namespace}  Mode: READ-ONLY / 只读",
            id="context",
        )
        yield Static(
            "全局：Ctrl+C 退出 · F1/? 帮助 · Ctrl+R 刷新 · Ctrl+K 聚焦监盘\n"
            "聊天：Enter 提交 · i 返回输入 · Ctrl+L 清空右侧显示\n"
            "监盘：0 总览 · 1~6 切换资源 · Enter/d 详情 · l Pod 日志 · q 退出",
            id="help",
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
            "  4 Daemon  5 Services  6 Replica  │  d 详情  l 日志  i 聊天",
            id="hotkeys",
        )
        yield Static(
            " ^K 监盘 │ 0~6 资源 │ d 详情 │ l 日志 │ i 聊天 │ F1 帮助",
            id="hotkeys-compact",
        )

    def on_mount(self) -> None:
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

    def action_command_mode(self) -> None:
        self.action_focus_monitor()

    def action_focus_question(self) -> None:
        self.query_one("#question", Input).focus()
        if not self._busy:
            self.query_one("#status", Static).update("聊天模式 · Enter 发送")

    @on(DataTable.RowSelected, "#monitor-table")
    def open_selected_resource(self) -> None:
        self.action_describe_resource()

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
        viewer = ResourceViewer(loading_title=loading_title)
        self.push_screen(viewer)
        self.call_after_refresh(
            self._load_resource_content,
            viewer=viewer,
            operation=operation,
            resource=resource,
        )

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


def _next_event(events: Iterator[AgentEvent]) -> AgentEvent | None:
    try:
        return next(events)
    except StopIteration:
        return None
