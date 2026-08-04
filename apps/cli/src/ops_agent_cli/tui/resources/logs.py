from collections import Counter, deque
from collections.abc import Iterator, Sequence
from datetime import datetime
from enum import StrEnum
from threading import Event
from typing import ClassVar, Protocol, cast

from ops_agent.monitoring import (
    KubernetesLogLevel,
    KubernetesLogQuery,
    KubernetesLogRecord,
    KubernetesLogSnapshot,
    KubernetesResourceRef,
)
from rich.text import Text
from textual import events, on, work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import Screen
from textual.widgets import Button, RichLog, Select, Static

from ops_agent_cli.tui.resources.contracts import CopyModeController


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
        height: 6;
        padding: 0 1;
        background: $panel;
    }

    #log-selectors,
    #log-actions {
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

    #log-follow.active {
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

    #log-footer {
        height: 1;
        padding: 0 1;
        background: $panel;
        color: $text-muted;
    }
    """

    BINDINGS: ClassVar[list[Binding]] = [
        Binding("escape", "escape", "返回", priority=True),
        Binding("q", "close", "返回", priority=True),
        Binding("r", "reload", "刷新", priority=True),
        Binding("w", "cycle_line_mode", "长行模式", priority=True),
        Binding("f", "toggle_follow", "实时跟随", priority=True),
    ]

    def __init__(
        self,
        *,
        source: LogSnapshotSource,
        resource: KubernetesResourceRef,
        containers: Sequence[str],
        copy_mode: bool = False,
    ) -> None:
        super().__init__()
        self._source = source
        self._resource = resource
        self._containers = tuple(containers)
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
            yield RichLog(
                max_lines=20_000,
                highlight=False,
                markup=False,
                wrap=True,
                auto_scroll=False,
                id="log-content",
            )
            yield Static("正在读取 Kubernetes API…", id="log-status")
            yield Static(
                self._footer_text(),
                id="log-footer",
            )

    def on_mount(self) -> None:
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
        viewer = self.query_one("#log-content", RichLog)
        viewer.wrap = self._line_mode is LogLineMode.WRAP
        viewer.clear()
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
                continue
            for record in source.records:
                self._write_record(
                    viewer,
                    record,
                    show_container=multiple_sources,
                )
        if self._omitted_follow_records:
            viewer.write(
                Text(
                    f"[{self._omitted_follow_records} newer Follow records were not "
                    f"added after the {_MAX_FOLLOW_RECORDS:,}-record safety limit]",
                    style="dim",
                )
            )
        for record in self._follow_records:
            self._write_record(viewer, record)
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
    ) -> Text:
        prefix = f"{record.container or 'default'} │ " if show_container else ""
        content = f"{prefix}{record.raw}"
        if max_length is not None and len(content) > max_length:
            content = f"{content[: max_length - 1]}…"
        return Text(content, style=self._level_style(record.level))

    def _write_record(
        self,
        viewer: RichLog,
        record: KubernetesLogRecord,
        *,
        show_container: bool = False,
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

    def set_copy_mode(self, enabled: bool) -> None:
        self._copy_mode = enabled
        self.query_one("#log-footer", Static).update(self._footer_text())

    def _footer_text(self) -> str:
        if self._copy_mode:
            return "COPY MODE · 直接用鼠标拖选复制 · Esc 恢复鼠标控制"
        return (
            "Esc/q 返回 · r 刷新 · f Follow · w 长行模式 · ↑/↓/PgUp/PgDn 滚动 · F2 备用"
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
        self._follow_records.append(record)
        viewer = self.query_one("#log-content", RichLog)
        self._write_record(viewer, record)
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
