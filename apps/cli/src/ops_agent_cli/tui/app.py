import asyncio
import re
from collections.abc import Iterator
from typing import ClassVar, Protocol

from ops_agent.agent import AgentEvent, AgentStage, ApplicationError
from textual import on, work
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.widgets import Input, Markdown, Static

_INITIAL_RESULT = "输入问题后按 Enter 开始诊断。"
_TABLE_SEPARATOR_CELL = re.compile(r":?[=-]{3,}:?")
_FENCE_OPENING = re.compile(r"(`{3,}|~{3,})(.*)")


class Conversation(Protocol):
    def stream(self, question: str) -> Iterator[AgentEvent]: ...


class QuestionInput(Input):
    """保留普通文本输入，同时让全局帮助键优先于输入框。"""

    def check_consume_key(self, key: str, character: str | None) -> bool:
        if key == "question_mark":
            return False
        return super().check_consume_key(key, character)


class OpsAgentTui(App[None]):
    """键盘驱动的只读运维诊断终端。"""

    TITLE = "Ops Agent"
    SUB_TITLE = "Kubernetes 只读诊断"

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

    #view-title {
        height: 1;
        padding: 0 1;
        background: #172029;
        color: #ffcc66;
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

    #hotkeys {
        height: 1;
        padding: 0 1;
        background: #1fb5ad;
        color: #001c1a;
        text-style: bold;
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
        Binding("ctrl+l", "clear_result", "清空", priority=True),
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
            f" OPS AGENT  Context: {self._environment}"
            f"  Namespace: {self._namespace}  Mode: READ-ONLY / 只读",
            id="context",
        )
        yield Static(" DIAGNOSTIC ANSWER", id="view-title")
        yield Static(
            "全局：Ctrl+C 退出 · F1/? 帮助 · Ctrl+L 清空\n"
            "输入模式：Enter 提交 · Esc 进入命令模式\n"
            "命令模式：q 退出 · i 返回输入",
            id="help",
        )
        yield Markdown(_INITIAL_RESULT, id="result")
        yield Static("就绪", id="status")
        yield QuestionInput(
            placeholder="› 例如：sample 现在有几个服务？",
            id="question",
        )
        yield Static(
            " Enter 提交  │  Esc 命令  │  F1/? 帮助  │  Ctrl+L 清空  │  Ctrl+C 退出",
            id="hotkeys",
        )

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
        self.query_one("#result", Markdown).update("正在获取实时证据，请稍候。")
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
        self.query_one("#result", Markdown).update(_normalize_markdown(answer))
        self.query_one("#status", Static).update("完成")
        self._reset_question()

    def _finish_with_error(self, message: str) -> None:
        self.query_one("#result", Markdown).update(f"诊断失败：{message}")
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

    def action_clear_result(self) -> None:
        self.query_one("#result", Markdown).update(_INITIAL_RESULT)
        if not self._busy:
            self.query_one("#status", Static).update("结果已清空")

    def action_command_mode(self) -> None:
        self.set_focus(None)
        if not self._busy:
            self.query_one("#status", Static).update("命令模式 · q 退出 · i 返回输入")

    def action_focus_question(self) -> None:
        self.query_one("#question", Input).focus()


def _next_event(events: Iterator[AgentEvent]) -> AgentEvent | None:
    try:
        return next(events)
    except StopIteration:
        return None


def _normalize_markdown(markdown: str) -> str:
    """修正常见的 Markdown 表格分隔符，同时保持代码块原样。"""
    normalized: list[str] = []
    fence: tuple[str, int] | None = None

    for line in markdown.splitlines():
        if fence is None:
            fence = _opening_fence(line)
            if (
                fence is None
                and not line.startswith(("    ", "\t"))
                and _is_table_separator(line)
            ):
                line = line.replace("=", "-")
        elif _closes_fence(line, fence):
            fence = None
        normalized.append(line)

    return "\n".join(normalized)


def _opening_fence(line: str) -> tuple[str, int] | None:
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return None

    match = _FENCE_OPENING.match(stripped)
    if match is None:
        return None

    marker, info = match.groups()
    if marker[0] == "`" and "`" in info:
        return None
    return marker[0], len(marker)


def _closes_fence(line: str, fence: tuple[str, int]) -> bool:
    stripped = line.lstrip(" ")
    if len(line) - len(stripped) > 3:
        return False

    marker = stripped.rstrip(" \t")
    fence_character, minimum_length = fence
    return (
        len(marker) >= minimum_length
        and marker
        and all(character == fence_character for character in marker)
    )


def _is_table_separator(line: str) -> bool:
    if "=" not in line or "|" not in line:
        return False

    cells = line.strip().strip("|").split("|")
    return bool(cells) and all(
        _TABLE_SEPARATOR_CELL.fullmatch(cell.strip()) is not None for cell in cells
    )
