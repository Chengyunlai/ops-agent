import asyncio
from collections.abc import Iterator
from typing import ClassVar, Protocol

from ops_agent.agent import AgentEvent, AgentStage, ApplicationError
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Footer, Input, Static


class Conversation(Protocol):
    def stream(self, question: str) -> Iterator[AgentEvent]: ...


class OpsAgentTui(App[None]):
    """键盘驱动的只读运维诊断终端。"""

    TITLE = "Ops Agent"
    SUB_TITLE = "Kubernetes 只读诊断"

    CSS = """
    Screen {
        layout: vertical;
    }

    #context {
        height: 3;
        padding: 1 2;
        background: $primary;
        color: $text;
        text-style: bold;
    }

    #result {
        height: 1fr;
        margin: 1 2;
        padding: 1 2;
        border: round $primary;
        overflow-y: auto;
    }

    #help {
        display: none;
        height: auto;
        margin: 0 2 1 2;
        padding: 1 2;
        border: round $accent;
    }

    #help.visible {
        display: block;
    }

    #status {
        height: 1;
        margin: 0 2;
        color: $text-muted;
    }

    #question {
        margin: 1 2;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("q", "quit", "退出"),
        Binding("question_mark", "toggle_help", "帮助"),
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
        environment: str,
        namespace: str,
    ) -> None:
        super().__init__()
        self._conversation = conversation
        self._environment = environment
        self._namespace = namespace
        self._busy = False

    def compose(self) -> ComposeResult:
        yield Static(
            f"环境: {self._environment}  |  Namespace: {self._namespace}  |  只读诊断",
            id="context",
        )
        yield Static("输入问题后按 Enter 开始诊断。", id="result")
        yield Static(
            "Enter：提交诊断  Esc：命令模式  ?：显示帮助  q：退出  i：输入",
            id="help",
        )
        yield Static("就绪", id="status")
        yield Input(
            placeholder="例如：检查所有 Pod 并分析异常",
            id="question",
        )
        yield Footer()

    def on_mount(self) -> None:
        self.query_one("#question", Input).focus()

    @on(Input.Submitted, "#question")
    def submit_question(self, event: Input.Submitted) -> None:
        question = event.value.strip()
        if self._busy or not question:
            return

        self._busy = True
        event.input.disabled = True
        self.query_one("#status", Static).update("诊断中…")
        self.query_one("#result", Static).update("正在获取实时证据，请稍候。")
        self._ask_agent(question)

    @work(exclusive=True, exit_on_error=False)
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

    def _finish_with_answer(self, answer: str) -> None:
        self.query_one("#result", Static).update(answer)
        self.query_one("#status", Static).update("完成")
        self._reset_question()

    def _finish_with_error(self, message: str) -> None:
        self.query_one("#result", Static).update(f"诊断失败：{message}")
        self.query_one("#status", Static).update("失败")
        self._reset_question()

    def _reset_question(self) -> None:
        self._busy = False
        question = self.query_one("#question", Input)
        question.disabled = False
        question.value = ""
        question.focus()

    def action_toggle_help(self) -> None:
        self.query_one("#help", Static).toggle_class("visible")

    def action_command_mode(self) -> None:
        self.set_focus(None)

    def action_focus_question(self) -> None:
        self.query_one("#question", Input).focus()


def _next_event(events: Iterator[AgentEvent]) -> AgentEvent | None:
    try:
        return next(events)
    except StopIteration:
        return None
