import re
from dataclasses import dataclass

from textual.widgets import Markdown

_TABLE_SEPARATOR_CELL = re.compile(r":?[=-]{3,}:?")
_FENCE_OPENING = re.compile(r"(`{3,}|~{3,})(.*)")


@dataclass
class _ChatMessage:
    role: str
    content: str
    pending: bool = False


class ChatTranscript(Markdown):
    """保存并渲染一次 TUI 会话中的用户与 Agent 消息。"""

    def __init__(self, *, namespace: str, id: str | None = None) -> None:
        self._namespace = namespace
        self._messages: list[_ChatMessage] = []
        super().__init__(self._render_transcript(), id=id)

    def begin_exchange(self, question: str) -> None:
        self._messages.extend(
            [
                _ChatMessage(role="你", content=question),
                _ChatMessage(
                    role="OPS AGENT",
                    content="正在获取实时证据，请稍候。",
                    pending=True,
                ),
            ]
        )
        self._refresh_transcript()

    def complete_exchange(self, answer: str) -> None:
        self._replace_pending(_normalize_markdown(answer))

    def fail_exchange(self, message: str) -> None:
        self._replace_pending(f"诊断失败：{message}")

    def reset_transcript(self) -> None:
        self._messages.clear()
        self._refresh_transcript()

    def _replace_pending(self, content: str) -> None:
        for message in reversed(self._messages):
            if message.pending:
                message.content = content
                message.pending = False
                break
        else:
            self._messages.append(_ChatMessage(role="OPS AGENT", content=content))
        self._refresh_transcript()

    def _refresh_transcript(self) -> None:
        update = self.update(self._render_transcript())

        async def scroll_after_update() -> None:
            await update
            self.scroll_end(animate=False)

        self.run_worker(
            scroll_after_update(),
            group="transcript-render",
            exclusive=True,
            exit_on_error=False,
        )

    def _render_transcript(self) -> str:
        welcome = (
            "**OPS AGENT**\n\n"
            f"已连接到 `{self._namespace}`。"
            "输入问题后按 Enter 开始诊断。"
        )
        messages = [
            f"**{message.role}**\n\n{message.content}" for message in self._messages
        ]
        return "\n\n---\n\n".join([welcome, *messages])


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
    return len(marker) >= minimum_length and all(
        character == fence_character for character in marker
    )


def _is_table_separator(line: str) -> bool:
    if "=" not in line or "|" not in line:
        return False

    cells = line.strip().strip("|").split("|")
    return bool(cells) and all(
        _TABLE_SEPARATOR_CELL.fullmatch(cell.strip()) is not None for cell in cells
    )
