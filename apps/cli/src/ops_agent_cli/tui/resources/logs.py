from collections import Counter, deque
from collections.abc import Callable, Iterator, Sequence
from datetime import datetime
from enum import StrEnum
from threading import Event
from typing import ClassVar, Literal, Protocol, cast

from ops_agent.monitoring import (
    KubernetesLogFocus,
    KubernetesLogLevel,
    KubernetesLogQuery,
    KubernetesLogRecord,
    KubernetesLogSearch,
    KubernetesLogSearchMatch,
    KubernetesLogSearchResult,
    KubernetesLogSnapshot,
    KubernetesResourceRef,
    apply_kubernetes_log_focus,
    search_kubernetes_log_records,
)
from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, Input, RichLog, Select, Static

from ops_agent_cli.configuration import LogFocusSettings
from ops_agent_cli.tui.resources.contracts import CopyModeController
from ops_agent_cli.tui.resources.log_rules import LogFocusRulesScreen


class LogSnapshotSource(Protocol):
    def pod_log_snapshot(
        self,
        resource: KubernetesResourceRef,
        *,
        query: KubernetesLogQuery,
    ) -> KubernetesLogSnapshot: ...

    def follow_pod_logs(
        self,
        resource: KubernetesResourceRef,
        *,
        container: str | None,
        since_time: datetime,
        stop_event: Event,
    ) -> Iterator[KubernetesLogRecord]: ...

    def stop_following_pod_logs(self) -> None: ...


class LogLineMode(StrEnum):
    WRAP = "wrap"
    TRUNCATE = "truncate"
    FULL = "full"


class LogRangePreset(StrEnum):
    LAST_200_LINES = "last_200_lines"
    LAST_500_LINES = "last_500_lines"
    LAST_1000_LINES = "last_1000_lines"
    LAST_15_MINUTES = "last_15_minutes"
    LAST_1_HOUR = "last_1_hour"

    @property
    def label(self) -> str:
        return {
            self.LAST_200_LINES: "Last 200 lines",
            self.LAST_500_LINES: "Last 500 lines",
            self.LAST_1000_LINES: "Last 1000 lines",
            self.LAST_15_MINUTES: "Last 15 minutes",
            self.LAST_1_HOUR: "Last 1 hour",
        }[self]

    def to_query(self, *, container: str | None) -> KubernetesLogQuery:
        ranges = {
            self.LAST_200_LINES: (200, None),
            self.LAST_500_LINES: (500, None),
            self.LAST_1000_LINES: (1000, None),
            self.LAST_15_MINUTES: (None, 15 * 60),
            self.LAST_1_HOUR: (None, 60 * 60),
        }
        tail_lines, since_seconds = ranges[self]
        return KubernetesLogQuery(
            container=container,
            tail_lines=tail_lines,
            since_seconds=since_seconds,
        )


_RANGE_OPTIONS = tuple((preset.label, preset) for preset in LogRangePreset)
_TRUNCATED_LINE_LENGTH = 160
_MAX_FOLLOW_RECORDS = 10_000
type LogFocusFlag = Literal[
    "hide_info",
    "hide_debug",
    "hide_health_checks",
    "hide_access_logs",
]


class LogWorkbench(Screen[None]):
    """Browse a bounded Pod Log Snapshot without mutating its source data."""

    CSS = """
    LogWorkbench {
        background: $background;
    }

    #log-workbench {
        width: 100%;
        height: 100%;
        background: $surface;
    }

    #log-title {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $accent;
        text-style: bold;
    }

    #log-controls {
        height: 12;
        padding: 0 1;
        background: $panel;
    }

    #log-selectors,
    #log-actions,
    #log-focus-actions,
    #log-search-actions {
        height: 3;
    }

    #log-container {
        width: 1fr;
        margin-right: 1;
    }

    #log-range {
        width: 1fr;
    }

    #log-refresh,
    #log-follow,
    #log-line-mode,
    #log-copy-button {
        width: 1fr;
        min-width: 10;
        margin-right: 1;
    }

    #log-focus-toggle,
    #log-hide-info,
    #log-hide-debug,
    #log-hide-health,
    #log-hide-access,
    #log-focus-rules {
        width: 1fr;
        min-width: 8;
        margin-right: 1;
    }

    #log-search-input {
        width: 1fr;
        margin-right: 1;
    }

    #log-search-regex {
        width: 12;
        min-width: 12;
        margin-right: 1;
    }

    #log-search-prev,
    #log-search-next,
    #log-search-clear {
        width: 8;
        min-width: 8;
        margin-right: 1;
    }

    #log-follow.active,
    #log-focus-toggle.active,
    #log-hide-info.active,
    #log-hide-debug.active,
    #log-hide-health.active,
    #log-hide-access.active,
    #log-search-regex.active {
        background: $accent;
        color: $background;
        text-style: bold;
    }

    #log-content {
        height: 1fr;
        padding: 0 1;
        background: $surface;
        color: $text;
        scrollbar-color: $primary;
        scrollbar-color-hover: $secondary;
        scrollbar-color-active: $accent;
    }

    #log-status {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text;
    }

    #log-query-status {
        height: 2;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
    }

    #log-footer {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "escape", "返回", priority=True),
        Binding("q", "close", "返回"),
        Binding("r", "reload", "刷新"),
        Binding("w", "cycle_line_mode", "长行模式"),
        Binding("f", "toggle_follow", "实时跟随"),
        Binding("/", "focus_search", "搜索"),
        Binding("n", "next_match", "下一个命中"),
        Binding("shift+n", "previous_match", "上一个命中"),
    ]

    def __init__(
        self,
        *,
        source: LogSnapshotSource,
        resource: KubernetesResourceRef,
        containers: Sequence[str],
        save_focus_settings: Callable[[LogFocusSettings], None],
        focus_settings: LogFocusSettings | None = None,
        copy_mode: bool = False,
    ) -> None:
        super().__init__()
        self._source = source
        self._resource = resource
        self._containers = tuple(containers)
        self._focus_settings = focus_settings or LogFocusSettings()
        self._save_focus_settings = save_focus_settings
        self._focus_enabled = False
        self._focus_message = ""
        self._search = KubernetesLogSearch()
        self._search_result = KubernetesLogSearchResult(matches=())
        self._search_cursor: int | None = None
        self._visible_records: tuple[KubernetesLogRecord, ...] = ()
        self._hidden_record_count = 0
        self._record_line_offsets: list[int] = []
        self._snapshot: KubernetesLogSnapshot | None = None
        self._follow_records: deque[KubernetesLogRecord] = deque(
            maxlen=_MAX_FOLLOW_RECORDS
        )
        self._follow_replay_keys: Counter[tuple[str | None, datetime | None, str]] = (
            Counter()
        )
        self._omitted_follow_records = 0
        self._follow_stop_event = Event()
        self._follow_message = ""
        self._requested_query = KubernetesLogQuery(
            container=self._containers[0],
            tail_lines=200,
        )
        self._line_mode = LogLineMode.WRAP
        self._following = False
        self._copy_mode = copy_mode

    def compose(self) -> ComposeResult:
        with Vertical(id="log-workbench"):
            yield Static(f"Logs · Pod/{self._resource.name}", id="log-title")
            with Vertical(id="log-controls"):
                with Horizontal(id="log-selectors"):
                    yield Select(
                        (
                            ("All containers", None),
                            *((name, name) for name in self._containers),
                        ),
                        value=self._containers[0],
                        allow_blank=False,
                        id="log-container",
                    )
                    yield Select(
                        _RANGE_OPTIONS,
                        value=LogRangePreset.LAST_200_LINES,
                        allow_blank=False,
                        id="log-range",
                    )
                with Horizontal(id="log-actions"):
                    yield Button("Refresh", id="log-refresh", compact=True)
                    yield Button("Follow: OFF", id="log-follow", compact=True)
                    yield Button("Lines: WRAP", id="log-line-mode", compact=True)
                    yield Button("复制", id="log-copy-button", compact=True)
                with Horizontal(id="log-focus-actions"):
                    yield Button("Focus: OFF", id="log-focus-toggle", compact=True)
                    yield Button("INFO: SHOW", id="log-hide-info", compact=True)
                    yield Button("DEBUG: SHOW", id="log-hide-debug", compact=True)
                    yield Button("Health: SHOW", id="log-hide-health", compact=True)
                    yield Button("Access: SHOW", id="log-hide-access", compact=True)
                    yield Button("Rules: 0", id="log-focus-rules", compact=True)
                with Horizontal(id="log-search-actions"):
                    yield Input(
                        placeholder="/ 搜索当前视图（默认忽略大小写）",
                        max_length=200,
                        id="log-search-input",
                    )
                    yield Button("Regex: OFF", id="log-search-regex", compact=True)
                    yield Button("Prev N", id="log-search-prev", compact=True)
                    yield Button("Next n", id="log-search-next", compact=True)
                    yield Button("Clear", id="log-search-clear", compact=True)
            yield RichLog(
                max_lines=20_000,
                highlight=False,
                markup=False,
                wrap=True,
                auto_scroll=False,
                id="log-content",
            )
            yield Static("正在读取 Kubernetes API…", id="log-status")
            yield Static("VIEW ALL · SEARCH OFF", id="log-query-status")
            yield Static(
                self._footer_text(),
                id="log-footer",
            )

    def on_mount(self) -> None:
        self._refresh_focus_controls()
        self.query_one("#log-content", RichLog).focus()
        self._load_snapshot(self._requested_query)

    def on_unmount(self) -> None:
        self._stop_follow(message="")

    def on_resize(self, event: events.Resize) -> None:
        if event.size.width and self._snapshot is not None:
            self.call_after_refresh(self._render_snapshot, self._snapshot)

    def action_close(self) -> None:
        self._stop_follow(message="")
        self.dismiss()

    def action_escape(self) -> None:
        search_input = self.query_one("#log-search-input", Input)
        if search_input.has_focus:
            self.query_one("#log-content", RichLog).focus()
            return
        controller = cast(CopyModeController, self.app)
        if self._copy_mode and controller.exit_copy_mode():
            return
        self.action_close()

    def action_reload(self) -> None:
        self._stop_follow(message="")
        query = self._selected_query()
        self._requested_query = query
        self._load_snapshot(query)

    def action_cycle_line_mode(self) -> None:
        modes = tuple(LogLineMode)
        current = modes.index(self._line_mode)
        self._line_mode = modes[(current + 1) % len(modes)]
        self.query_one(
            "#log-line-mode", Button
        ).label = f"Lines: {self._line_mode.value.upper()}"
        if self._snapshot is not None:
            self._render_snapshot(self._snapshot)

    def action_focus_search(self) -> None:
        search_input = self.query_one("#log-search-input", Input)
        search_input.focus()
        search_input.action_select_all()

    def action_next_match(self) -> None:
        self._move_search_cursor(1)

    def action_previous_match(self) -> None:
        self._move_search_cursor(-1)

    def action_toggle_focus(self) -> None:
        self._focus_enabled = not self._focus_enabled
        self._focus_message = ""
        self._search_cursor = 0
        self._refresh_focus_controls()
        if self._snapshot is not None:
            self._render_snapshot(self._snapshot)

    def action_toggle_follow(self) -> None:
        if self._following:
            self._stop_follow(message="Follow 已停止")
            return
        snapshot = self._snapshot
        if snapshot is None:
            self.query_one("#log-status", Static).update(
                "请先等待 Log Snapshot 读取完成"
            )
            return
        if snapshot.query.container is None:
            self.query_one("#log-status", Static).update(
                "Follow 需要选择单个容器；All containers 仍可读取稳定快照"
            )
            return
        since_time = self._follow_since_time(
            snapshot,
            self._follow_records,
        )
        existing_records = snapshot.records + tuple(self._follow_records)
        self._follow_replay_keys = Counter(
            self._record_key(record)
            for record in existing_records
            if record.timestamp == since_time
        )
        self._follow_stop_event = Event()
        self._following = True
        self._follow_message = "实时跟随中"
        self.query_one("#log-follow", Button).label = "Follow: ON"
        self.query_one("#log-follow", Button).add_class("active")
        self._update_status()
        self._follow_logs(
            container=snapshot.query.container,
            since_time=since_time,
            stop_event=self._follow_stop_event,
        )

    @on(Button.Pressed, "#log-refresh")
    def reload_from_button(self) -> None:
        self.action_reload()

    @on(Button.Pressed, "#log-line-mode")
    def cycle_line_mode_from_button(self) -> None:
        self.action_cycle_line_mode()

    @on(Button.Pressed, "#log-follow")
    def toggle_follow_from_button(self) -> None:
        self.action_toggle_follow()

    @on(Button.Pressed, "#log-copy-button")
    def toggle_copy_mode(self) -> None:
        controller = cast(CopyModeController, self.app)
        controller.action_toggle_copy_mode()

    @on(Button.Pressed, "#log-focus-toggle")
    def toggle_focus_from_button(self) -> None:
        self.action_toggle_focus()

    @on(Button.Pressed, "#log-hide-info")
    def toggle_info_from_button(self) -> None:
        self._toggle_focus_setting("hide_info")

    @on(Button.Pressed, "#log-hide-debug")
    def toggle_debug_from_button(self) -> None:
        self._toggle_focus_setting("hide_debug")

    @on(Button.Pressed, "#log-hide-health")
    def toggle_health_from_button(self) -> None:
        self._toggle_focus_setting("hide_health_checks")

    @on(Button.Pressed, "#log-hide-access")
    def toggle_access_from_button(self) -> None:
        self._toggle_focus_setting("hide_access_logs")

    @on(Button.Pressed, "#log-focus-rules")
    def open_focus_rules(self) -> None:
        self.app.push_screen(
            LogFocusRulesScreen(rules=self._focus_settings.hidden_text),
            self._focus_rules_closed,
        )

    @on(Input.Changed, "#log-search-input")
    def search_text_changed(self, event: Input.Changed) -> None:
        self._search = KubernetesLogSearch(
            text=event.value,
            regex=self._search.regex,
        )
        self._search_cursor = 0
        if self._snapshot is not None:
            self._render_snapshot(self._snapshot)

    @on(Input.Submitted, "#log-search-input")
    def search_submitted(self) -> None:
        self.query_one("#log-content", RichLog).focus()
        self._scroll_to_current_match()

    @on(Button.Pressed, "#log-search-regex")
    def toggle_search_regex(self) -> None:
        self._search = KubernetesLogSearch(
            text=self._search.text,
            regex=not self._search.regex,
        )
        self._search_cursor = 0
        button = self.query_one("#log-search-regex", Button)
        button.label = f"Regex: {'ON' if self._search.regex else 'OFF'}"
        button.set_class(self._search.regex, "active")
        if self._snapshot is not None:
            self._render_snapshot(self._snapshot)

    @on(Button.Pressed, "#log-search-prev")
    def previous_search_match(self) -> None:
        self.action_previous_match()

    @on(Button.Pressed, "#log-search-next")
    def next_search_match(self) -> None:
        self.action_next_match()

    @on(Button.Pressed, "#log-search-clear")
    def clear_search(self) -> None:
        self.query_one("#log-search-input", Input).value = ""
        self.query_one("#log-content", RichLog).focus()

    @on(Select.Changed, "#log-container")
    @on(Select.Changed, "#log-range")
    def reload_for_selection(self) -> None:
        if not self.is_mounted:
            return
        query = self._selected_query()
        if self._requested_query == query:
            return
        self._stop_follow(message="")
        self._requested_query = query
        self._load_snapshot(query)

    @work(group="log-snapshot", exclusive=True, exit_on_error=False)
    async def _load_snapshot(self, query: KubernetesLogQuery) -> None:
        self.query_one("#log-status", Static).update(
            f"正在读取 {query.container or 'default'} · {query.range_label}…"
        )
        try:
            snapshot = await self.run_worker_thread(query)
        except Exception as error:  # noqa: BLE001 - 工作台必须恢复 API 错误
            self.query_one("#log-status", Static).update(f"日志读取失败：{error}")
            return
        self._snapshot = snapshot
        self._follow_records.clear()
        self._follow_replay_keys.clear()
        self._omitted_follow_records = 0
        self._follow_message = ""
        self._render_snapshot(snapshot)

    @work(group="log-follow", exclusive=True, exit_on_error=False)
    async def _follow_logs(
        self,
        *,
        container: str | None,
        since_time: datetime,
        stop_event: Event,
    ) -> None:
        import asyncio

        await asyncio.to_thread(
            self._consume_follow,
            container,
            since_time,
            stop_event,
        )

    def _consume_follow(
        self,
        container: str | None,
        since_time: datetime,
        stop_event: Event,
    ) -> None:
        error: str | None = None
        try:
            for record in self._source.follow_pod_logs(
                self._resource,
                container=container,
                since_time=since_time,
                stop_event=stop_event,
            ):
                if stop_event.is_set():
                    break
                self.app.call_from_thread(self._append_follow_record, record)
        except Exception as caught:  # noqa: BLE001 - 流中断应转为可见状态
            error = str(caught)
        finally:
            self.app.call_from_thread(
                self._follow_finished,
                stop_event,
                error,
            )

    async def run_worker_thread(
        self,
        query: KubernetesLogQuery,
    ) -> KubernetesLogSnapshot:
        import asyncio

        return await asyncio.to_thread(
            self._source.pod_log_snapshot,
            self._resource,
            query=query,
        )

    def _selected_query(self) -> KubernetesLogQuery:
        container_value = self.query_one("#log-container", Select).value
        container = None if container_value is None else str(container_value)
        range_value = self.query_one("#log-range", Select).value
        if not isinstance(range_value, LogRangePreset):
            raise TypeError("日志范围选择无效")
        return range_value.to_query(container=container)

    def _render_snapshot(self, snapshot: KubernetesLogSnapshot) -> None:
        self._calculate_view_state(snapshot)
        viewer = self.query_one("#log-content", RichLog)
        viewer.wrap = self._line_mode is LogLineMode.WRAP
        viewer.clear()
        self._record_line_offsets.clear()
        multiple_sources = len(snapshot.sources) > 1
        for source in snapshot.sources:
            if source.error is not None:
                label = source.container or "default"
                viewer.write(
                    Text(
                        f"[{label} unavailable] {source.error}",
                        style=self._level_style(KubernetesLogLevel.ERROR),
                    )
                )
        if self._omitted_follow_records:
            viewer.write(
                Text(
                    f"[{self._omitted_follow_records} newer Follow records were not "
                    f"added after the {_MAX_FOLLOW_RECORDS:,}-record safety limit]",
                    style="dim",
                )
            )
        matches_by_record: dict[int, list[KubernetesLogSearchMatch]] = {}
        for match in self._search_result.matches:
            matches_by_record.setdefault(match.record_index, []).append(match)
        current_search_match = self._current_search_match()
        for record_index, record in enumerate(self._visible_records):
            self._record_line_offsets.append(len(viewer.lines))
            self._write_record(
                viewer,
                record,
                show_container=multiple_sources,
                search_matches=matches_by_record.get(record_index, ()),
                current_search_match=current_search_match,
            )
        if not snapshot.records and not any(
            source.error for source in snapshot.sources
        ):
            viewer.write("（没有返回日志）")
        self._update_status()

    def _render_record(
        self,
        record: KubernetesLogRecord,
        *,
        max_length: int | None = None,
        show_container: bool = False,
        search_matches: Sequence[KubernetesLogSearchMatch] = (),
        current_search_match: KubernetesLogSearchMatch | None = None,
    ) -> Text:
        prefix = f"{record.container or 'default'} │ " if show_container else ""
        content = f"{prefix}{record.raw}"
        if max_length is not None and len(content) > max_length:
            content = f"{content[: max_length - 1]}…"
        rendered = Text(content, style=self._level_style(record.level))
        for search_match in search_matches:
            highlight_style = (
                "bold reverse" if search_match is current_search_match else "underline"
            )
            for start, end in search_match.spans or ((0, len(record.raw)),):
                visible_start = min(len(content), len(prefix) + start)
                visible_end = min(len(content), len(prefix) + end)
                if visible_start < visible_end:
                    rendered.stylize(
                        highlight_style,
                        visible_start,
                        visible_end,
                    )
        return rendered

    def _write_record(
        self,
        viewer: RichLog,
        record: KubernetesLogRecord,
        *,
        show_container: bool = False,
        search_matches: Sequence[KubernetesLogSearchMatch] = (),
        current_search_match: KubernetesLogSearchMatch | None = None,
    ) -> None:
        available_width = max(
            1,
            viewer.scrollable_content_region.width,
        )
        is_full = self._line_mode is LogLineMode.FULL
        max_length = (
            min(_TRUNCATED_LINE_LENGTH, available_width)
            if self._line_mode is LogLineMode.TRUNCATE
            else None
        )
        viewer.write(
            self._render_record(
                record,
                max_length=max_length,
                show_container=show_container,
                search_matches=search_matches,
                current_search_match=current_search_match,
            ),
            width=None if is_full else available_width,
            shrink=not is_full,
        )

    def _update_status(self) -> None:
        snapshot = self._snapshot
        if snapshot is None:
            return
        records = snapshot.records + tuple(self._follow_records)
        error_count = sum(
            record.level is KubernetesLogLevel.ERROR for record in records
        )
        warning_count = sum(
            record.level is KubernetesLogLevel.WARNING for record in records
        )
        follow = "FOLLOW" if self._following else "SNAPSHOT"
        detail = f" · {self._follow_message}" if self._follow_message else ""
        source_errors = sum(source.error is not None for source in snapshot.sources)
        if source_errors:
            detail += (
                f" · SOURCE ERROR {source_errors} · r 重试；"
                "若 Pod 已重建，Esc 返回并选择新 Pod"
            )
        if self._omitted_follow_records:
            detail += f" · FOLLOW OMITTED {self._omitted_follow_records}"
        self.query_one("#log-status", Static).update(
            f"{len(records)} records · ERROR {error_count}"
            f" · WARN {warning_count} · {self._line_mode.value.upper()} · {follow}"
            f"{detail}"
        )
        self.query_one("#log-query-status", Static).update(
            self._query_status_text(len(records))
        )

    def _calculate_view_state(self, snapshot: KubernetesLogSnapshot) -> None:
        records = snapshot.records + tuple(self._follow_records)
        focus_result = apply_kubernetes_log_focus(
            records,
            self._active_focus(),
        )
        self._visible_records = focus_result.records
        self._hidden_record_count = focus_result.hidden_count
        self._search_result = search_kubernetes_log_records(
            self._visible_records,
            self._search,
        )
        match_count = len(self._search_result.matches)
        if match_count == 0:
            self._search_cursor = None
        elif self._search_cursor is None:
            self._search_cursor = 0
        else:
            self._search_cursor %= match_count

    def _active_focus(self) -> KubernetesLogFocus:
        if not self._focus_enabled:
            return KubernetesLogFocus()
        settings = self._focus_settings
        return KubernetesLogFocus(
            hide_info=settings.hide_info,
            hide_debug=settings.hide_debug,
            hide_health_checks=settings.hide_health_checks,
            hide_access_logs=settings.hide_access_logs,
            hidden_text=settings.hidden_text,
        )

    def _query_status_text(self, total_records: int) -> str:
        if self._focus_enabled:
            view = (
                f"FOCUS ON · {len(self._visible_records)}/{total_records} visible"
                f" · HIDDEN {self._hidden_record_count} · {self._focus_summary()}"
            )
        else:
            view = f"VIEW ALL · {len(self._visible_records)}/{total_records} visible"
        if not self._search.text:
            search = "SEARCH OFF"
        elif self._search_result.error is not None:
            search = self._search_result.error
        else:
            position = self._search_cursor + 1 if self._search_cursor is not None else 0
            mode = "REGEX" if self._search.regex else "LITERAL"
            search = (
                f"SEARCH {position}/{len(self._search_result.matches)} · {mode}"
                f' · insensitive · "{self._search.text}"'
            )
        message = f" · {self._focus_message}" if self._focus_message else ""
        return f"{view}{message}\n{search}"

    def _focus_summary(self) -> str:
        settings = self._focus_settings
        filters = [
            label
            for enabled, label in (
                (settings.hide_info, "hide INFO"),
                (settings.hide_debug, "hide DEBUG"),
                (settings.hide_health_checks, "hide Health"),
                (settings.hide_access_logs, "hide Access"),
            )
            if enabled
        ]
        if settings.hidden_text:
            filters.append(f"{len(settings.hidden_text)} text rules")
        return ", ".join(filters) or "no hide rules"

    def _refresh_focus_controls(self) -> None:
        focus_button = self.query_one("#log-focus-toggle", Button)
        focus_button.label = f"Focus: {'ON' if self._focus_enabled else 'OFF'}"
        focus_button.set_class(self._focus_enabled, "active")
        for field, button_id, label in (
            ("hide_info", "#log-hide-info", "INFO"),
            ("hide_debug", "#log-hide-debug", "DEBUG"),
            ("hide_health_checks", "#log-hide-health", "Health"),
            ("hide_access_logs", "#log-hide-access", "Access"),
        ):
            hidden = bool(getattr(self._focus_settings, field))
            button = self.query_one(button_id, Button)
            button.label = f"{label}: {'HIDE' if hidden else 'SHOW'}"
            button.set_class(hidden, "active")
        self.query_one(
            "#log-focus-rules", Button
        ).label = f"Rules: {len(self._focus_settings.hidden_text)}"

    def _toggle_focus_setting(self, field: LogFocusFlag) -> None:
        values = self._focus_settings.model_dump()
        values[field] = not bool(getattr(self._focus_settings, field))
        updated = LogFocusSettings.model_validate(values)
        if not self._persist_focus_settings(updated):
            return
        self._focus_enabled = True
        self._search_cursor = 0
        self._refresh_focus_controls()
        if self._snapshot is not None:
            self._render_snapshot(self._snapshot)

    def _focus_rules_closed(self, rules: tuple[str, ...] | None) -> None:
        if rules is None:
            return
        values = self._focus_settings.model_dump()
        values["hidden_text"] = rules
        updated = LogFocusSettings.model_validate(values)
        if not self._persist_focus_settings(updated):
            return
        self._focus_enabled = True
        self._search_cursor = 0
        self._refresh_focus_controls()
        if self._snapshot is not None:
            self._render_snapshot(self._snapshot)

    def _persist_focus_settings(self, updated: LogFocusSettings) -> bool:
        try:
            self._save_focus_settings(updated)
        except Exception as error:  # noqa: BLE001 - 规则保存失败必须留在工作台
            self._focus_message = f"Project Profile 保存失败：{error}"
            self._update_status()
            return False
        self._focus_settings = updated
        self._focus_message = "Project Profile 已保存"
        return True

    def _move_search_cursor(self, offset: int) -> None:
        if not self._search.text or self._search_result.error is not None:
            self.action_focus_search()
            return
        match_count = len(self._search_result.matches)
        if match_count == 0:
            self._update_status()
            return
        current = self._search_cursor or 0
        self._search_cursor = (current + offset) % match_count
        snapshot = self._snapshot
        if snapshot is not None:
            self._render_snapshot(snapshot)
            self._scroll_to_current_match()

    def _current_match_record_index(self) -> int | None:
        match = self._current_search_match()
        return match.record_index if match is not None else None

    def _current_search_match(self) -> KubernetesLogSearchMatch | None:
        if self._search_cursor is None or not self._search_result.matches:
            return None
        return self._search_result.matches[self._search_cursor]

    def _scroll_to_current_match(self) -> None:
        record_index = self._current_match_record_index()
        if record_index is None or record_index >= len(self._record_line_offsets):
            return
        self.query_one("#log-content", RichLog).scroll_to(
            y=self._record_line_offsets[record_index],
            animate=False,
            force=True,
            immediate=True,
        )

    def set_copy_mode(self, enabled: bool) -> None:
        self._copy_mode = enabled
        self.query_one("#log-footer", Static).update(self._footer_text())

    def _footer_text(self) -> str:
        if self._copy_mode:
            return "COPY MODE · 直接用鼠标拖选复制 · Esc 恢复鼠标控制"
        return (
            "Esc/q 返回 · / 搜索 · n/N 命中 · r 刷新 · f Follow"
            " · w 长行模式 · ↑/↓/PgUp/PgDn 滚动 · F2 备用"
        )

    def _append_follow_record(self, record: KubernetesLogRecord) -> None:
        if not self.is_mounted:
            return
        key = self._record_key(record)
        if self._follow_replay_keys[key] > 0:
            self._follow_replay_keys.subtract((key,))
            if self._follow_replay_keys[key] == 0:
                del self._follow_replay_keys[key]
            return
        if len(self._follow_records) == _MAX_FOLLOW_RECORDS:
            self._omitted_follow_records += 1
            self._stop_follow(
                message=(
                    f"Follow 已停止：达到 {_MAX_FOLLOW_RECORDS:,} 条安全上限；"
                    "请刷新快照或缩小读取范围"
                )
            )
            snapshot = self._snapshot
            if snapshot is not None:
                self._render_snapshot(snapshot)
            return
        previous_visible_count = len(self._visible_records)
        self._follow_records.append(record)
        snapshot = self._snapshot
        if snapshot is None:
            return
        self._calculate_view_state(snapshot)
        if (
            len(self._visible_records) == previous_visible_count + 1
            and self._visible_records[-1] is record
        ):
            record_index = len(self._visible_records) - 1
            search_matches = tuple(
                match
                for match in self._search_result.matches
                if match.record_index == record_index
            )
            viewer = self.query_one("#log-content", RichLog)
            self._record_line_offsets.append(len(viewer.lines))
            self._write_record(
                viewer,
                record,
                show_container=len(snapshot.sources) > 1,
                search_matches=search_matches,
                current_search_match=self._current_search_match(),
            )
            viewer.scroll_end(animate=False)
        self._update_status()

    def _follow_finished(
        self,
        stop_event: Event,
        error: str | None,
    ) -> None:
        if stop_event is not self._follow_stop_event:
            return
        if not self.is_mounted:
            return
        self._following = False
        self.query_one("#log-follow", Button).label = "Follow: OFF"
        self.query_one("#log-follow", Button).remove_class("active")
        if error is not None:
            self._follow_message = (
                f"Follow 中断：{error}；按 f 重连；若 Pod 已重建，Esc 返回并选择新 Pod"
            )
        elif not stop_event.is_set():
            self._follow_message = "Follow 流已结束，可按 f 重连"
        self._update_status()

    def _stop_follow(self, *, message: str) -> None:
        if not self._following:
            return
        self._follow_stop_event.set()
        try:
            self._source.stop_following_pod_logs()
        except Exception as error:  # noqa: BLE001 - 停止失败仍需恢复界面状态
            if message:
                message = f"{message}；关闭流失败：{error}"
        self._following = False
        self._follow_message = message
        if self.is_mounted:
            self.query_one("#log-follow", Button).label = "Follow: OFF"
            self.query_one("#log-follow", Button).remove_class("active")
            self._update_status()

    @staticmethod
    def _follow_since_time(
        snapshot: KubernetesLogSnapshot,
        follow_records: Sequence[KubernetesLogRecord] = (),
    ) -> datetime:
        timestamps = tuple(
            record.timestamp
            for record in snapshot.records + tuple(follow_records)
            if record.timestamp is not None
        )
        return max(timestamps, default=snapshot.observed_at)

    @staticmethod
    def _record_key(
        record: KubernetesLogRecord,
    ) -> tuple[str | None, datetime | None, str]:
        return (record.container, record.timestamp, record.raw)

    def _level_style(self, level: KubernetesLogLevel) -> str:
        theme = self.app.current_theme
        if level is KubernetesLogLevel.ERROR:
            return f"bold {theme.error or '#B42318'}"
        if level is KubernetesLogLevel.WARNING:
            return theme.warning or "#A15C00"
        if level is KubernetesLogLevel.DEBUG:
            return "dim"
        return ""
