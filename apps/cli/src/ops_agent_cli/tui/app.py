import asyncio
from collections.abc import Iterator
from typing import ClassVar, Protocol

from ops_agent.agent import AgentEvent, AgentStage, ApplicationError
from ops_agent.monitoring import KubernetesMonitorSnapshot
from textual import events, on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.widgets import Input, Static

from ops_agent_cli.tui.chat import ChatTranscript
from ops_agent_cli.tui.monitor import MonitorPane, MonitorView


class Conversation(Protocol):
    def stream(self, question: str) -> Iterator[AgentEvent]: ...


class Monitor(Protocol):
    def snapshot(self) -> KubernetesMonitorSnapshot: ...


class QuestionInput(Input):
    """保留普通文本输入，同时让全局帮助键优先于输入框。"""

    def check_consume_key(self, key: str, character: str | None) -> bool:
        if key == "question_mark":
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
        Binding("1", "show_pods", "Pods", show=False),
        Binding("2", "show_deployments", "Deployments", show=False),
        Binding("3", "show_services", "Services", show=False),
        Binding("q", "quit", "退出"),
        Binding("i", "focus_question", "输入"),
        Binding(
            "escape",
            "command_mode",
            "命令模式",
            show=False,
            priority=True,
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
            "全局：Ctrl+C 退出 · F1/? 帮助 · Ctrl+R 刷新监盘 · Ctrl+L 清空显示\n"
            "输入模式：Enter 提交 · Esc 进入命令模式\n"
            "命令模式：1/2/3 切换资源 · q 退出 · i 返回输入",
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
            " 1 Pods  2 Deploy  3 Services  │  Ctrl+R 刷新"
            "  │  Enter 发送  │  F1/? 帮助  │  Ctrl+C 退出",
            id="hotkeys",
        )
        yield Static(
            " 1/2/3 资源 │ Enter 发送 │ F1 帮助 │ ^R 刷新 │ ^C 退出",
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

    def action_show_pods(self) -> None:
        self.query_one("#monitor-pane", MonitorPane).show_view(MonitorView.PODS)

    def action_show_deployments(self) -> None:
        self.query_one("#monitor-pane", MonitorPane).show_view(MonitorView.DEPLOYMENTS)

    def action_show_services(self) -> None:
        self.query_one("#monitor-pane", MonitorPane).show_view(MonitorView.SERVICES)

    def action_command_mode(self) -> None:
        self.set_focus(None)
        if not self._busy:
            self.query_one("#status", Static).update(
                "命令模式 · 1/2/3 资源 · q 退出 · i 输入"
            )

    def action_focus_question(self) -> None:
        self.query_one("#question", Input).focus()

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
