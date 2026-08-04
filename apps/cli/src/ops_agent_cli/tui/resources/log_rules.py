from typing import ClassVar

from textual import on
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, DataTable, Input, Static


class LogFocusRulesScreen(Screen[tuple[str, ...] | None]):
    """Maintain explicit literal hide rules for the current Project Profile."""

    CSS = """
    LogFocusRulesScreen {
        background: $background;
    }

    #log-rules-workbench {
        width: 100%;
        height: 100%;
        background: $surface;
    }

    #log-rules-title {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $accent;
        text-style: bold;
    }

    #log-rules-help,
    #log-rules-status {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
    }

    #log-rule-editor,
    #log-rules-actions {
        height: 3;
        padding: 0 1;
        background: $panel;
    }

    #log-rule-input {
        width: 1fr;
        margin-right: 1;
    }

    #log-rule-add,
    #log-rule-delete,
    #log-rules-cancel,
    #log-rules-save {
        min-width: 12;
        margin-right: 1;
    }

    #log-rules-table {
        height: 1fr;
        background: $surface;
        color: $text;
    }

    #log-rules-actions {
        align-horizontal: right;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "cancel", "取消", priority=True),
        Binding("ctrl+s", "save", "保存", priority=True),
        Binding("delete", "delete_rule", "删除规则"),
    ]

    def __init__(self, *, rules: tuple[str, ...]) -> None:
        super().__init__()
        self._rules = list(rules)

    def compose(self) -> ComposeResult:
        with Vertical(id="log-rules-workbench"):
            yield Static("Log Focus · Project Profile 隐藏规则", id="log-rules-title")
            yield Static(
                "大小写不敏感的原文包含规则；只由操作员维护，AI 不参与。",
                id="log-rules-help",
            )
            with Horizontal(id="log-rule-editor"):
                yield Input(
                    placeholder="输入要隐藏的明确文本（最多 200 字符）",
                    max_length=200,
                    id="log-rule-input",
                )
                yield Button("添加", id="log-rule-add", compact=True)
                yield Button("删除选中", id="log-rule-delete", compact=True)
            yield DataTable(
                cursor_type="row",
                zebra_stripes=True,
                id="log-rules-table",
            )
            yield Static(id="log-rules-status")
            with Horizontal(id="log-rules-actions"):
                yield Button("取消", id="log-rules-cancel", compact=True)
                yield Button(
                    "保存到 Project Profile",
                    id="log-rules-save",
                    variant="primary",
                    compact=True,
                )

    def on_mount(self) -> None:
        table = self.query_one("#log-rules-table", DataTable)
        table.add_column("明确隐藏文本", key="rule")
        self._render_rules()
        self.query_one("#log-rule-input", Input).focus()

    @on(Input.Submitted, "#log-rule-input")
    def add_submitted_rule(self) -> None:
        self._add_rule()

    @on(Button.Pressed)
    def handle_button(self, event: Button.Pressed) -> None:
        if event.button.id == "log-rule-add":
            self._add_rule()
        elif event.button.id == "log-rule-delete":
            self.action_delete_rule()
        elif event.button.id == "log-rules-save":
            self.action_save()
        elif event.button.id == "log-rules-cancel":
            self.action_cancel()

    def action_delete_rule(self) -> None:
        table = self.query_one("#log-rules-table", DataTable)
        if not self._rules or table.cursor_row >= len(self._rules):
            self._set_status("没有可删除的规则")
            return
        removed = self._rules.pop(table.cursor_row)
        self._render_rules()
        self._set_status(f"已移除：{removed}；保存后生效")

    def action_save(self) -> None:
        self.dismiss(tuple(self._rules))

    def action_cancel(self) -> None:
        self.dismiss(None)

    def _add_rule(self) -> None:
        field = self.query_one("#log-rule-input", Input)
        rule = field.value.strip()
        if not rule:
            self._set_status("规则不能为空")
            return
        if len(self._rules) >= 50:
            self._set_status("最多保存 50 条隐藏规则")
            return
        if rule.casefold() in {existing.casefold() for existing in self._rules}:
            self._set_status("该规则已经存在")
            return
        self._rules.append(rule)
        field.value = ""
        self._render_rules()
        self._set_status(f"已添加：{rule}；保存后生效")
        field.focus()

    def _render_rules(self) -> None:
        table = self.query_one("#log-rules-table", DataTable)
        table.clear(columns=False)
        for index, rule in enumerate(self._rules):
            table.add_row(rule, key=str(index))
        self._set_status(f"{len(self._rules)}/50 rules")

    def _set_status(self, message: str) -> None:
        self.query_one("#log-rules-status", Static).update(message)
