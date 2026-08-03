import asyncio
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path
from threading import Event, Lock
from types import SimpleNamespace

from ops_agent.agent import (
    AgentEvent,
    AgentStage,
    CapabilityScope,
    InteractionChannel,
)
from ops_agent.kubernetes import (
    KubernetesChangeSignal,
    KubernetesResourceKind,
    KubernetesWatchOutcome,
    KubernetesWatchResult,
    PersistentVolumeMountSummary,
)
from ops_agent.monitoring import (
    KubernetesMonitorSnapshot,
    KubernetesResourceCollection,
    KubernetesResourceContent,
    KubernetesResourceRef,
    KubernetesResourceRow,
    VolumeDirectory,
    VolumeEntry,
    VolumeEntryKind,
)
from ops_agent_cli import tui as tui_module
from ops_agent_cli.configuration import (
    InteractiveExecSettings,
    KubernetesSettings,
    KubernetesWatchSettings,
    ModelSettings,
    ProjectSettings,
    Settings,
    ThemeName,
    TuiColorSettings,
    TuiSettings,
)
from ops_agent_cli.manual_access import DownloadResult, InteractiveSessionResult
from ops_agent_cli.tui import run_tui
from ops_agent_cli.tui.app import OpsAgentTui
from textual.color import Color
from textual.coordinate import Coordinate
from textual.widgets import Button, DataTable, Input, Markdown, RichLog, Select, Static


class FakeAgent:
    def __init__(self, *, answer: str) -> None:
        self.answer = answer
        self.questions: list[str] = []

    def stream(self, question: str):
        self.questions.append(question)
        yield AgentEvent(
            stage=AgentStage.COMPLETED,
            message="回答已完成",
            answer=self.answer,
        )


class FakeConversation:
    def __init__(self, *, answer: str) -> None:
        self.answer = answer
        self.questions: list[str] = []

    def stream(self, question: str):
        self.questions.append(question)
        yield AgentEvent(
            stage=AgentStage.INTENT_INTERPRETED,
            message="已识别 Kubernetes Service 数量查询",
        )
        yield AgentEvent(
            stage=AgentStage.COMPLETED,
            message="回答已完成",
            answer=self.answer,
        )


class FakeMonitor:
    def __init__(self) -> None:
        self.calls = 0
        self.stop_watch_calls = 0
        self.content_calls: list[tuple[str, object]] = []

    def snapshot(self) -> KubernetesMonitorSnapshot:
        self.calls += 1
        return create_monitor_snapshot()

    def stop_waiting_for_change(self) -> None:
        self.stop_watch_calls += 1

    def describe(
        self,
        resource: KubernetesResourceRef,
    ) -> KubernetesResourceContent:
        self.content_calls.append(("describe", resource))
        return KubernetesResourceContent(
            title=f"Describe · {resource.kind}/{resource.name}",
            content=f"kind: {resource.kind}\nmetadata:\n  name: {resource.name}",
        )

    def diagnostics(
        self,
        resource: KubernetesResourceRef,
    ) -> KubernetesResourceContent:
        self.content_calls.append(("diagnostics", resource))
        return KubernetesResourceContent(
            title=f"Health · {resource.kind}/{resource.name}",
            content=(
                "Findings (1):\n"
                "  ! Deployment rollout 超过进度期限\n\n"
                "Rollout:\n"
                "  Generation: 7 · Observed: 7 · Revision: 3\n"
                "  Topology:\n"
                "    ReplicaSet/sample-api-7f8 · desired 2 · ready 1\n"
                "      Pod/sample-api-7f8-x1 · phase Running"
            ),
        )

    def pod_logs(
        self,
        resource: KubernetesResourceRef,
        *,
        tail_lines: int = 200,
    ) -> KubernetesResourceContent:
        self.content_calls.append(("logs", (resource, tail_lines)))
        return KubernetesResourceContent(
            title=f"Logs · Pod/{resource.name} · last {tail_lines} lines",
            content="2026-07-27T10:30:00Z server started",
        )

    def pod_containers(
        self,
        resource: KubernetesResourceRef,
    ) -> tuple[str, ...]:
        self.content_calls.append(("pod_containers", resource))
        return ("api", "sidecar")

    def browse_pvc(
        self,
        resource: KubernetesResourceRef,
        *,
        path: str = ".",
    ) -> VolumeDirectory:
        self.content_calls.append(("browse_pvc", (resource, path)))
        entries = (
            (
                VolumeEntry(
                    name="backups",
                    kind=VolumeEntryKind.DIRECTORY,
                    size_bytes=None,
                ),
                VolumeEntry(
                    name="README.txt",
                    kind=VolumeEntryKind.FILE,
                    size_bytes=128,
                ),
            )
            if path == "."
            else (
                VolumeEntry(
                    name="daily.sql",
                    kind=VolumeEntryKind.FILE,
                    size_bytes=2048,
                ),
            )
        )
        return VolumeDirectory(
            claim_name=resource.name,
            path=path,
            target=PersistentVolumeMountSummary(
                claim_name=resource.name,
                pod_name="mysql-0",
                pod_phase="Running",
                container_name="mysql",
                mount_path="/var/lib/mysql",
                read_only=False,
            ),
            entries=entries,
        )

    def preview_pvc_file(
        self,
        resource: KubernetesResourceRef,
        *,
        path: str,
        max_bytes: int = 64 * 1024,
    ) -> KubernetesResourceContent:
        self.content_calls.append(("preview_pvc", (resource, path, max_bytes)))
        return KubernetesResourceContent(
            title=f"PVC/{resource.name} · {path}",
            content="backup contents",
        )


class FakePodAccess:
    def __init__(self, download_root: Path) -> None:
        self.download_root = download_root
        self.calls: list[tuple[str, object]] = []

    def download_pod_file(
        self,
        *,
        pod_name: str,
        container_name: str,
        remote_path: str,
    ) -> DownloadResult:
        self.calls.append(("pod", (pod_name, container_name, remote_path)))
        return DownloadResult(
            path=self.download_root / "pod.log",
            size_bytes=321,
            sha256="a" * 64,
        )

    def download_pvc_file(
        self,
        *,
        claim_name: str,
        pod_name: str,
        container_name: str,
        mount_path: str,
        relative_path: str,
    ) -> DownloadResult:
        self.calls.append(
            (
                "pvc",
                (
                    claim_name,
                    pod_name,
                    container_name,
                    mount_path,
                    relative_path,
                ),
            )
        )
        return DownloadResult(
            path=self.download_root / "daily.sql",
            size_bytes=2048,
            sha256="b" * 64,
        )

    def interactive_session(
        self,
        *,
        pod_name: str,
        container_name: str,
    ) -> InteractiveSessionResult:
        self.calls.append(("shell", (pod_name, container_name)))
        return InteractiveSessionResult(exit_code=0)


def create_monitor_snapshot(
    *,
    deployment_health_reasons: tuple[str, ...] = (),
) -> KubernetesMonitorSnapshot:
    def collection(
        kind: KubernetesResourceKind,
        label: str,
        shortcut: str | None,
        columns: tuple[str, ...],
        values: tuple[str, ...] | None,
        *,
        healthy: bool | None = True,
    ) -> KubernetesResourceCollection:
        rows = (
            (
                KubernetesResourceRow(
                    ref=KubernetesResourceRef(kind=kind, name=values[0]),
                    values=values,
                    healthy=healthy,
                    health_reasons=(
                        deployment_health_reasons
                        if kind is KubernetesResourceKind.DEPLOYMENT
                        else ()
                    ),
                ),
            )
            if values is not None
            else ()
        )
        return KubernetesResourceCollection(
            kind=kind,
            label=label,
            shortcut=shortcut,
            columns=columns,
            rows=rows,
        )

    return KubernetesMonitorSnapshot(
        namespace="sample",
        observed_at=datetime(2026, 7, 27, 10, 30, tzinfo=UTC),
        resources=(
            collection(
                KubernetesResourceKind.POD,
                "Pods",
                "1",
                ("NAME", "READY", "STATUS", "RESTARTS", "AGE"),
                ("sample-api-7f8", "2/2", "Running", "1", "2d"),
            ),
            collection(
                KubernetesResourceKind.DEPLOYMENT,
                "Deployments",
                "2",
                ("NAME", "READY", "AVAILABLE", "UPDATED"),
                ("sample-api", "2/2", "2", "2"),
            ),
            collection(
                KubernetesResourceKind.STATEFUL_SET,
                "StatefulSets",
                "3",
                ("NAME", "READY", "CURRENT", "UPDATED"),
                ("mysql", "1/1", "1", "1"),
            ),
            collection(
                KubernetesResourceKind.DAEMON_SET,
                "DaemonSets",
                "4",
                ("NAME", "READY", "CURRENT", "AVAILABLE"),
                ("log-agent", "2/2", "2", "2"),
            ),
            collection(
                KubernetesResourceKind.SERVICE,
                "Services",
                "5",
                ("NAME", "TYPE", "CLUSTER-IP", "PORTS"),
                ("sample-api", "ClusterIP", "10.43.0.10", "80/TCP"),
                healthy=None,
            ),
            collection(
                KubernetesResourceKind.REPLICA_SET,
                "ReplicaSets",
                "6",
                ("NAME", "READY", "CURRENT", "DESIRED"),
                ("sample-api-7f8", "2", "2", "2"),
            ),
            collection(KubernetesResourceKind.JOB, "Jobs", None, ("NAME",), None),
            collection(
                KubernetesResourceKind.CRON_JOB,
                "CronJobs",
                None,
                ("NAME",),
                None,
            ),
            collection(
                KubernetesResourceKind.INGRESS,
                "Ingresses",
                None,
                ("NAME",),
                None,
            ),
            collection(
                KubernetesResourceKind.PERSISTENT_VOLUME_CLAIM,
                "PVCs",
                "7",
                (
                    "NAME",
                    "STATUS",
                    "VOLUME",
                    "CAPACITY",
                    "STORAGECLASS",
                    "BACKEND",
                    "MOUNTED BY",
                    "MOUNT PATHS",
                ),
                (
                    "mysql-data",
                    "Bound",
                    "pvc-123",
                    "10Gi",
                    "fast",
                    "CSI/disk.example.com",
                    "mysql-0/mysql",
                    "/var/lib/mysql (rw)",
                ),
            ),
        ),
    )


def create_app_settings() -> Settings:
    return Settings(
        project=ProjectSettings(name="Testing"),
        kubernetes=KubernetesSettings(
            environment="test",
            namespace="sample",
            kubeconfig_path="/tmp/ops-agent-kubeconfig",
            request_timeout_seconds=10,
            watch=KubernetesWatchSettings(enabled=False),
        ),
        model=ModelSettings(
            provider="openai",
            model="test-model",
        ),
        tui=TuiSettings(),
    )


def create_tui(
    conversation,
    *,
    monitor=None,
    settings: Settings | None = None,
    save_settings=None,
    pod_access=None,
) -> OpsAgentTui:
    return OpsAgentTui(
        conversation=conversation,
        monitor=monitor or FakeMonitor(),
        environment="test",
        namespace="sample",
        settings=settings or create_app_settings(),
        save_settings=save_settings or (lambda _: None),
        pod_access=pod_access,
    )


def _contrast_ratio(foreground: Color, background: Color) -> float:
    def relative_luminance(color: Color) -> float:
        def linearize(channel: int) -> float:
            value = channel / 255
            return (
                value / 12.92 if value <= 0.04045 else ((value + 0.055) / 1.055) ** 2.4
            )

        return (
            0.2126 * linearize(color.r)
            + 0.7152 * linearize(color.g)
            + 0.0722 * linearize(color.b)
        )

    lighter, darker = sorted(
        (relative_luminance(foreground), relative_luminance(background)),
        reverse=True,
    )
    return (lighter + 0.05) / (darker + 0.05)


def test_tui_topbar_buttons_are_readable_without_hover() -> None:
    async def exercise() -> None:
        app = create_tui(FakeAgent(answer="unused"))

        async with app.run_test(size=(140, 34)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()

            for selector in ("#settings-button", "#quit-button"):
                button = app.query_one(selector, Button)
                foreground = button.styles.color
                background = button.styles.background

                assert foreground is not None
                assert background is not None
                assert _contrast_ratio(foreground, background) >= 4.5

    asyncio.run(exercise())


def test_tui_quit_button_and_footer_exit_hint() -> None:
    async def exercise() -> None:
        app = create_tui(FakeAgent(answer="unused"))

        async with app.run_test(size=(140, 34)) as pilot:
            await app.workers.wait_for_complete()

            assert "Esc q 退出" in str(
                app.query_one("#hotkeys", Static).content,
            )
            assert "Esc q 退出" in str(
                app.query_one("#hotkeys-compact", Static).content,
            )
            await pilot.click("#quit-button")

        assert app.is_running is False

        narrow_app = create_tui(FakeAgent(answer="unused"))
        async with narrow_app.run_test(size=(80, 24)) as pilot:
            await narrow_app.workers.wait_for_complete()
            await pilot.pause()

            hotkeys = narrow_app.query_one("#hotkeys", Static)
            compact_hotkeys = narrow_app.query_one("#hotkeys-compact", Static)
            assert hotkeys.display is False
            assert compact_hotkeys.display is True
            assert "Esc q 退出" in str(compact_hotkeys.content)

    asyncio.run(exercise())


def test_run_tui_opens_kubernetes_scoped_conversation(
    tmp_path,
    monkeypatch,
) -> None:
    contexts = []
    session = object()
    monitor = object()
    settings = create_app_settings()
    saved: list[tuple[object, Settings]] = []
    received: dict[str, object] = {}

    class FakeOpsAgent:
        def open_session(self, context):
            contexts.append(context)
            return session

    class FakeTui:
        def __init__(
            self,
            *,
            conversation,
            monitor,
            environment,
            namespace,
            settings,
            save_settings,
        ) -> None:
            received.update(
                conversation=conversation,
                monitor=monitor,
                environment=environment,
                namespace=namespace,
                settings=settings,
                save_settings=save_settings,
            )

        def run(self, **kwargs) -> None:
            received["ran"] = True
            received["run_options"] = kwargs

    monkeypatch.setattr(
        tui_module,
        "create_runtime",
        lambda _: SimpleNamespace(
            agent=FakeOpsAgent(),
            monitor=monitor,
            settings=settings,
            environment="test",
            namespace="sample",
        ),
    )
    monkeypatch.setattr(tui_module, "OpsAgentTui", FakeTui)
    monkeypatch.setattr(
        tui_module,
        "save_settings",
        lambda path, updated: saved.append((path, updated)),
    )

    config_path = tmp_path / "test.toml"
    run_tui(config_path)
    save_callback = received.pop("save_settings")
    assert callable(save_callback)
    save_callback(settings)

    assert len(contexts) == 1
    assert contexts[0].channel is InteractionChannel.TUI
    assert contexts[0].scope is CapabilityScope.KUBERNETES
    assert contexts[0].environment == "test"
    assert contexts[0].namespace == "sample"
    assert received == {
        "conversation": session,
        "monitor": monitor,
        "environment": "test",
        "namespace": "sample",
        "settings": settings,
        "ran": True,
        "run_options": {"mouse": True},
    }
    assert saved == [(config_path, settings)]


def test_tui_monitor_accepts_mouse_focus_arrows_and_resource_shortcuts() -> None:
    async def exercise() -> None:
        app = create_tui(FakeAgent(answer="unused"))

        async with app.run_test(size=(140, 34)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()

            question = app.query_one("#question", Input)
            table = app.query_one("#monitor-table", DataTable)
            assert question.has_focus

            await pilot.click("#monitor-table", offset=(2, 2))
            assert table.has_focus

            initial_row = table.cursor_row
            await pilot.press("down")
            assert table.cursor_row == initial_row + 1

            await pilot.press("2")
            assert "Deployments" in str(app.query_one("#monitor-title", Static).content)
            assert table.has_focus

            await pilot.press("i", "0", "1", "2", "3")
            assert question.has_focus
            assert question.value == "0123"

    asyncio.run(exercise())


def test_tui_copy_mode_releases_and_restores_terminal_mouse_capture() -> None:
    async def exercise() -> None:
        app = create_tui(FakeAgent(answer="unused"))
        mouse_capture_calls: list[str] = []

        async with app.run_test(size=(140, 34)) as pilot:
            await app.workers.wait_for_complete()
            app._driver._disable_mouse_support = (  # type: ignore[attr-defined]
                lambda: mouse_capture_calls.append("disable")
            )
            app._driver._enable_mouse_support = (  # type: ignore[attr-defined]
                lambda: mouse_capture_calls.append("enable")
            )

            await pilot.click("#copy-button")
            assert mouse_capture_calls == ["disable"]
            assert "复制模式" in str(app.query_one("#status", Static).content)

            await pilot.press("escape")
            assert mouse_capture_calls == ["disable", "enable"]
            assert "鼠标控制已恢复" in str(app.query_one("#status", Static).content)

            # F2 remains available as a fallback shortcut.
            await pilot.press("f2", "f2")
            assert mouse_capture_calls == [
                "disable",
                "enable",
                "disable",
                "enable",
            ]

            await pilot.press("ctrl+k", "1", "l")
            await app.workers.wait_for_complete()
            await pilot.click("#resource-copy-button")
            assert mouse_capture_calls == [
                "disable",
                "enable",
                "disable",
                "enable",
                "disable",
            ]
            assert "COPY MODE" in str(
                app.screen.query_one("#resource-footer-text", Static).content
            )

            viewer = app.screen
            await pilot.press("escape")
            assert mouse_capture_calls == [
                "disable",
                "enable",
                "disable",
                "enable",
                "disable",
                "enable",
            ]
            assert app.screen is viewer
            assert "F2 备用" in str(
                app.screen.query_one("#resource-footer-text", Static).content
            )

            await pilot.press("escape")
            assert app.screen is not viewer

    asyncio.run(exercise())


def test_tui_settings_persist_project_and_apply_theme_immediately() -> None:
    async def exercise() -> None:
        saved: list[Settings] = []
        app = create_tui(
            FakeAgent(answer="unused"),
            save_settings=saved.append,
        )

        async with app.run_test(size=(140, 36)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.click("#settings-button")

            screen = app.screen
            assert screen.query_one("#setting-project-name", Input).value == "Testing"
            assert screen.query_one("#setting-namespace", Input).value == "sample"
            assert (
                screen.query_one("#setting-watch-enabled", Select).value == "disabled"
            )
            assert screen.query_one("#setting-watch-timeout", Input).value == "10"
            assert screen.query_one("#setting-poll-interval", Input).value == "5.0"
            assert (
                screen.query_one("#setting-interactive-exec", Select).value
                == "disabled"
            )
            assert (
                screen.query_one("#setting-interactive-locale", Input).value == "auto"
            )
            assert (
                screen.query_one(
                    "#setting-interactive-terminal-type",
                    Input,
                ).value
                == "xterm-256color"
            )
            assert (
                screen.query_one("#setting-interactive-color", Select).value
                == "enabled"
            )
            assert (
                screen.query_one("#setting-download-directory", Input).value
                == "~/Downloads/ops-agent"
            )
            assert (
                screen.query_one("#setting-pod-transfer-strategy", Select).value
                == "auto"
            )
            assert (
                screen.query_one("#setting-pod-transfer-max-size", Input).value == "512"
            )

            screen.query_one("#setting-project-name", Input).value = "Sample Platform"
            screen.query_one("#setting-namespace", Input).value = "sample-next"
            screen.query_one("#setting-watch-enabled", Select).value = "enabled"
            screen.query_one("#setting-watch-timeout", Input).value = "12"
            screen.query_one("#setting-poll-interval", Input).value = "7.5"
            screen.query_one("#setting-interactive-exec", Select).value = "enabled"
            screen.query_one(
                "#setting-interactive-locale",
                Input,
            ).value = "C.UTF-8"
            screen.query_one(
                "#setting-interactive-terminal-type",
                Input,
            ).value = "screen-256color"
            screen.query_one(
                "#setting-interactive-color",
                Select,
            ).value = "disabled"
            screen.query_one(
                "#setting-download-directory", Input
            ).value = "/tmp/sample-downloads"
            screen.query_one(
                "#setting-pod-transfer-strategy",
                Select,
            ).value = "exec-dd"
            screen.query_one(
                "#setting-pod-transfer-max-size",
                Input,
            ).value = "64"
            screen.query_one("#setting-theme", Select).value = ThemeName.LIGHT.value
            await pilot.pause()
            assert app.current_theme.dark is False

            await pilot.click("#settings-save")
            await pilot.pause()

            assert len(saved) == 1
            assert saved[0].project.name == "Sample Platform"
            assert saved[0].kubernetes.namespace == "sample-next"
            assert saved[0].kubernetes.watch.enabled
            assert saved[0].kubernetes.watch.timeout_seconds == 12
            assert saved[0].kubernetes.watch.poll_interval_seconds == 7.5
            assert saved[0].kubernetes.interactive_exec.enabled
            assert saved[0].kubernetes.interactive_exec.locale == "C.UTF-8"
            assert (
                saved[0].kubernetes.interactive_exec.terminal_type == "screen-256color"
            )
            assert not saved[0].kubernetes.interactive_exec.color
            assert saved[0].kubernetes.downloads.directory == Path(
                "/tmp/sample-downloads"
            )
            assert saved[0].kubernetes.pod_transfer.strategy.value == "exec-dd"
            assert saved[0].kubernetes.pod_transfer.max_file_size_mb == 64
            assert saved[0].tui.theme is ThemeName.LIGHT
            assert "重启生效" in str(app.query_one("#status", Static).content)

    asyncio.run(exercise())


def test_tui_settings_can_restore_theme_default_colors() -> None:
    async def exercise() -> None:
        saved: list[Settings] = []
        settings = create_app_settings().model_copy(
            update={
                "tui": TuiSettings(
                    theme=ThemeName.OPS_DARK,
                    colors=TuiColorSettings(
                        primary="#AA00AA",
                        background="#101010",
                    ),
                )
            }
        )
        app = create_tui(
            FakeAgent(answer="unused"),
            settings=settings,
            save_settings=saved.append,
        )

        async with app.run_test(size=(140, 36)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.click("#settings-button")

            assert (
                app.screen.query_one("#setting-color-primary", Input).value == "#AA00AA"
            )
            app.screen.query_one("#settings-scroll").scroll_end(
                animate=False,
                force=True,
            )
            await pilot.pause()
            await pilot.click("#settings-reset-theme")
            assert app.screen.query_one("#setting-color-primary", Input).value == ""
            assert app.screen.query_one("#setting-color-background", Input).value == ""

            await pilot.click("#settings-save")

            assert len(saved) == 1
            assert saved[0].tui.colors == TuiColorSettings()

    asyncio.run(exercise())


def test_tui_displays_context_and_agent_answer() -> None:
    async def exercise() -> None:
        agent = FakeAgent(answer="sample-api 正在运行")
        monitor = FakeMonitor()
        app = create_tui(agent, monitor=monitor)

        async with app.run_test(size=(120, 30)) as pilot:
            context = app.query_one("#context", Static)
            question = app.query_one("#question", Input)

            assert "test" in str(context.content)
            assert "sample" in str(context.content)
            assert "只读" in str(context.content)

            question.value = "检查所有 Pod"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

            result = app.query_one("#result", Markdown)
            status = app.query_one("#status", Static)
            assert agent.questions == ["检查所有 Pod"]
            assert "**YOU**" in result.source
            assert "检查所有 Pod" in result.source
            assert "**OPS AGENT**" in result.source
            assert "sample-api 正在运行" in result.source
            assert str(status.content) == "完成"
            assert question.disabled is False
            assert monitor.calls == 1
            assert app.query_one("#monitor-table", DataTable).row_count == 10
            assert (
                app.query_one("#monitor-pane").region.x
                < app.query_one("#chat-pane").region.x
            )

            await pilot.press("escape", "5")
            assert (
                str(
                    app.query_one("#monitor-table", DataTable).get_cell_at(
                        Coordinate(0, 0)
                    )
                )
                == "sample-api"
            )

    asyncio.run(exercise())


def test_tui_overview_names_every_monitored_resource_type() -> None:
    async def exercise() -> None:
        app = create_tui(FakeAgent(answer="unused"))

        async with app.run_test(size=(140, 34)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()

            table = app.query_one("#monitor-table", DataTable)
            assert table.row_count == 10
            assert [
                table.get_cell_at(Coordinate(row, 0)) for row in range(table.row_count)
            ] == [
                "Pods",
                "Deployments",
                "StatefulSets",
                "DaemonSets",
                "Services",
                "ReplicaSets",
                "Jobs",
                "CronJobs",
                "Ingresses",
                "PVCs",
            ]
            assert [
                table.get_cell_at(Coordinate(row, 1)) for row in range(table.row_count)
            ] == ["1", "1", "1", "1", "1", "1", "0", "0", "0", "1"]
            title = str(app.query_one("#monitor-title", Static).content)
            assert "Namespace sample" in title
            assert "Overview" in title

    asyncio.run(exercise())


def test_tui_opens_describe_and_pod_logs_for_selected_resource() -> None:
    async def exercise() -> None:
        monitor = FakeMonitor()
        app = create_tui(FakeAgent(answer="unused"), monitor=monitor)

        async with app.run_test(size=(140, 34)) as pilot:
            await app.workers.wait_for_complete()

            await pilot.press("ctrl+k", "5", "d")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "Describe · Service/sample-api" in str(
                app.screen.query_one("#resource-title", Static).content
            )
            assert "kind: Service" in "\n".join(
                line.text
                for line in app.screen.query_one(
                    "#resource-content",
                    RichLog,
                ).lines
            )
            await pilot.press("escape")

            await pilot.press("1", "l")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "Logs · Pod/sample-api-7f8" in str(
                app.screen.query_one("#resource-title", Static).content
            )
            assert monitor.content_calls == [
                (
                    "describe",
                    KubernetesResourceRef(
                        kind=KubernetesResourceKind.SERVICE,
                        name="sample-api",
                    ),
                ),
                (
                    "logs",
                    (
                        KubernetesResourceRef(
                            kind=KubernetesResourceKind.POD,
                            name="sample-api-7f8",
                        ),
                        200,
                    ),
                ),
            ]

    asyncio.run(exercise())


def test_tui_shows_health_reason_and_opens_diagnostic_details() -> None:
    class DiagnosticMonitor(FakeMonitor):
        def snapshot(self) -> KubernetesMonitorSnapshot:
            self.calls += 1
            return create_monitor_snapshot(
                deployment_health_reasons=("Deployment rollout 超过进度期限",)
            )

    async def exercise() -> None:
        monitor = DiagnosticMonitor()
        app = create_tui(FakeAgent(answer="unused"), monitor=monitor)

        async with app.run_test(size=(140, 34)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("escape", "2")
            await pilot.pause()

            table = app.query_one("#monitor-table", DataTable)
            diagnosis = str(table.get_cell_at(Coordinate(0, 1)))
            assert diagnosis.startswith("WARN")
            assert "rollout 超过进度期限" in diagnosis

            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()
            assert "Health · Deployment/sample-api" in str(
                app.screen.query_one("#resource-title", Static).content
            )
            assert "ReplicaSet/sample-api-7f8" in "\n".join(
                line.text
                for line in app.screen.query_one("#resource-content", RichLog).lines
            )
            assert monitor.content_calls[-1][0] == "diagnostics"

    asyncio.run(exercise())


def test_tui_browses_pvc_directories_and_previews_files() -> None:
    async def exercise() -> None:
        monitor = FakeMonitor()
        app = create_tui(FakeAgent(answer="unused"), monitor=monitor)

        async with app.run_test(size=(140, 34)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("ctrl+k", "7", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert "PVC/mysql-data" in str(
                app.screen.query_one("#volume-browser-title", Static).content
            )
            assert "mysql-0" in str(
                app.screen.query_one("#volume-browser-target", Static).content
            )
            table = app.screen.query_one("#volume-browser-table", DataTable)
            assert table.row_count == 2
            assert str(table.get_cell_at(Coordinate(0, 0))) == "backups"

            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert "backups" in str(
                app.screen.query_one("#volume-browser-path", Static).content
            )

            await pilot.press("enter")
            await app.workers.wait_for_complete()
            assert "PVC/mysql-data · backups/daily.sql" in str(
                app.screen.query_one("#resource-title", Static).content
            )
            assert "backup contents" in "\n".join(
                line.text
                for line in app.screen.query_one(
                    "#resource-content",
                    RichLog,
                ).lines
            )

    asyncio.run(exercise())


def test_tui_unifies_interactive_pod_session_and_downloads(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        disabled_app = create_tui(
            FakeAgent(answer="unused"),
            pod_access=FakePodAccess(tmp_path),
        )
        async with disabled_app.run_test(size=(140, 34)) as pilot:
            await disabled_app.workers.wait_for_complete()
            await pilot.press("ctrl+k", "1", "x")
            assert "未启用" in str(disabled_app.query_one("#status", Static).content)

        base_settings = create_app_settings()
        enabled_settings = base_settings.model_copy(
            update={
                "kubernetes": base_settings.kubernetes.model_copy(
                    update={"interactive_exec": InteractiveExecSettings(enabled=True)}
                )
            }
        )
        enabled_app = create_tui(
            FakeAgent(answer="unused"),
            settings=enabled_settings,
            pod_access=(pod_access := FakePodAccess(tmp_path)),
        )
        async with enabled_app.run_test(size=(140, 34)) as pilot:
            await enabled_app.workers.wait_for_complete()
            timer_calls: list[str] = []
            enabled_app._monitor_timer = type(
                "FakeTimer",
                (),
                {
                    "pause": lambda self: timer_calls.append("pause"),
                    "resume": lambda self: timer_calls.append("resume"),
                },
            )()
            enabled_app.suspend = lambda: nullcontext()  # type: ignore[method-assign]
            await pilot.press("ctrl+k", "1", "x")
            await enabled_app.workers.wait_for_complete()
            await pilot.pause()

            assert "INTERACTIVE POD SESSION" in str(
                enabled_app.screen.query_one(
                    "#pod-access-title",
                    Static,
                ).content
            )
            warning = str(
                enabled_app.screen.query_one(
                    "#pod-access-warning",
                    Static,
                ).content
            )
            assert "写能力" in warning
            assert "不会经过 AI" in warning
            assert "download <文件>" in warning

            enabled_app.screen.query_one(
                "#pod-access-container",
                Select,
            ).value = "sidecar"
            await pilot.click("#pod-access-confirm")
            await enabled_app.workers.wait_for_complete()
            await pilot.pause()

            assert pod_access.calls == [("shell", ("sample-api-7f8", "sidecar"))]
            assert timer_calls == ["pause", "resume"]
            assert "已结束" in str(enabled_app.query_one("#status", Static).content)

    asyncio.run(exercise())


def test_tui_downloads_selected_pvc_file_with_s(
    tmp_path: Path,
) -> None:
    async def exercise() -> None:
        pod_access = FakePodAccess(tmp_path)
        app = create_tui(
            FakeAgent(answer="unused"),
            monitor=FakeMonitor(),
            pod_access=pod_access,
        )

        async with app.run_test(size=(140, 34)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("ctrl+k", "7", "enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

            await pilot.press("down", "s")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert pod_access.calls == [
                (
                    "pvc",
                    (
                        "mysql-data",
                        "mysql-0",
                        "mysql",
                        "/var/lib/mysql",
                        "README.txt",
                    ),
                )
            ]
            status = str(
                app.screen.query_one(
                    "#volume-browser-status",
                    Static,
                ).content
            )
            assert "下载完成" in status
            assert str(tmp_path / "daily.sql") in status
            assert f"SHA-256 {'b' * 64}" in status

    asyncio.run(exercise())


def test_tui_renders_each_log_record_on_its_own_line() -> None:
    class MultilineLogMonitor(FakeMonitor):
        def pod_logs(
            self,
            resource: KubernetesResourceRef,
            *,
            tail_lines: int = 200,
        ) -> KubernetesResourceContent:
            return KubernetesResourceContent(
                title=f"Logs · Pod/{resource.name}",
                content="first record\nsecond record\nthird record\n",
            )

    async def exercise() -> None:
        app = create_tui(
            FakeAgent(answer="unused"),
            monitor=MultilineLogMonitor(),
        )

        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("ctrl+k", "1", "l")
            await app.workers.wait_for_complete()
            await pilot.pause()

            viewer = app.screen.query_one("#resource-content", RichLog)
            assert [line.text for line in viewer.lines] == [
                "first record",
                "second record",
                "third record",
            ]

    asyncio.run(exercise())


def test_tui_distinguishes_unavailable_resource_type_from_empty() -> None:
    class PartiallyUnavailableMonitor(FakeMonitor):
        def snapshot(self) -> KubernetesMonitorSnapshot:
            self.calls += 1
            snapshot = create_monitor_snapshot()
            resources = tuple(
                replace(
                    resource,
                    rows=(),
                    error="services is forbidden",
                )
                if resource.kind is KubernetesResourceKind.SERVICE
                else resource
                for resource in snapshot.resources
            )
            return replace(snapshot, resources=resources)

    async def exercise() -> None:
        app = create_tui(
            FakeAgent(answer="unused"),
            monitor=PartiallyUnavailableMonitor(),
        )

        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("ctrl+k", "5")

            assert "Services · Unavailable" in str(
                app.query_one("#monitor-title", Static).content
            )
            table = app.query_one("#monitor-table", DataTable)
            assert str(table.get_cell_at(Coordinate(0, 0))) == "Unavailable"
            assert table.get_cell_at(Coordinate(0, 1)) == "services is forbidden"

    asyncio.run(exercise())


def test_tui_consumes_stable_conversation_events() -> None:
    async def exercise() -> None:
        conversation = FakeConversation(answer="sample 中有 4 个 Service")
        app = create_tui(conversation)

        async with app.run_test() as pilot:
            question = app.query_one("#question", Input)
            question.value = "sample现在几个服务"
            await pilot.press("enter")
            await app.workers.wait_for_complete()

            question.value = "那 Pod 呢"
            await pilot.press("enter")
            await app.workers.wait_for_complete()

            assert conversation.questions == ["sample现在几个服务", "那 Pod 呢"]
            transcript = app.query_one("#result", Markdown).source
            assert "sample现在几个服务" in transcript
            assert "那 Pod 呢" in transcript
            assert transcript.count("**YOU**") == 2
            assert "sample 中有 4 个 Service" in transcript
            assert str(app.query_one("#status", Static).content) == "完成"

    asyncio.run(exercise())


def test_tui_renders_and_normalizes_markdown_answer() -> None:
    async def exercise() -> None:
        answer = (
            "当前共有 **3 个服务**：\n\n"
            "| 服务名 | 类型 |\n"
            "| ====== | ==== |\n"
            "| **sample-api** | `ClusterIP` |\n\n"
            "| 名称 |\n"
            "| ==== |\n"
            "| sample |\n\n"
            "````text\n"
            "```text\n"
            "| ==== | ==== |\n"
            "```\n"
            "| ==== | ==== |\n"
            "````"
        )
        app = create_tui(FakeAgent(answer=answer))

        async with app.run_test() as pilot:
            question = app.query_one("#question", Input)
            question.value = "sample现在几个服务"
            await pilot.press("enter")
            await app.workers.wait_for_complete()
            await pilot.pause()

            result = app.query_one("#result", Markdown)
            assert "| ------ | ---- |" in result.source
            assert "\n| ---- |\n| sample |" in result.source
            assert (
                "````text\n```text\n| ==== | ==== |\n```\n| ==== | ==== |\n````"
            ) in result.source
            assert "**3 个服务**" in result.source
            assert len(result.query("MarkdownTable")) == 2

    asyncio.run(exercise())


def test_tui_help_and_exit_work_from_input_mode() -> None:
    async def exercise() -> None:
        app = create_tui(FakeAgent(answer="unused"))

        async with app.run_test() as pilot:
            question = app.query_one("#question", Input)
            assert question.has_focus

            await pilot.press("q")
            assert question.value == "q"
            assert app.is_running is True
            question.value = ""

            await pilot.press("?")
            assert app.query_one("#help", Static).has_class("visible")
            assert question.value == ""

            await pilot.press("f1")
            assert not app.query_one("#help", Static).has_class("visible")

            result = app.query_one("#result", Markdown)
            await result.update("临时结果")
            await pilot.press("ctrl+l")
            assert "输入问题后按 Enter 开始诊断。" in result.source
            assert str(app.query_one("#status", Static).content) == (
                "显示已清空 · 会话上下文仍保留"
            )

            await pilot.press("ctrl+c")

        assert app.is_running is False

    asyncio.run(exercise())


def test_tui_recovers_after_agent_error() -> None:
    class FailingAgent:
        def stream(self, question: str):
            raise RuntimeError(f"无法处理：{question}")
            yield

    async def exercise() -> None:
        app = create_tui(FailingAgent())

        async with app.run_test() as pilot:
            question = app.query_one("#question", Input)
            question.value = "检查所有 Pod"
            await pilot.press("enter")
            await app.workers.wait_for_complete()

            result = app.query_one("#result", Markdown)
            status = app.query_one("#status", Static)
            assert "诊断失败：无法处理：检查所有 Pod" in result.source
            assert str(status.content) == "失败"
            assert question.disabled is False

    asyncio.run(exercise())


def test_tui_ignores_empty_question_and_toggles_help() -> None:
    async def exercise() -> None:
        agent = FakeAgent(answer="不应调用")
        app = create_tui(agent)

        async with app.run_test() as pilot:
            await pilot.press("enter")
            assert agent.questions == []

            await pilot.press("escape", "?")
            assert app.query_one("#help", Static).has_class("visible")
            await pilot.press("?", "i")
            assert not app.query_one("#help", Static).has_class("visible")
            assert app.query_one("#question", Input).has_focus
            await pilot.press("escape", "q")

        assert app.is_running is False

    asyncio.run(exercise())


def test_tui_remains_responsive_while_agent_is_running() -> None:
    started = Event()
    release = Event()
    questions: list[str] = []

    class BlockingAgent:
        def stream(self, question: str):
            questions.append(question)
            started.set()
            if not release.wait(timeout=2):
                raise RuntimeError("测试中的 Agent 未被释放")
            yield AgentEvent(
                stage=AgentStage.COMPLETED,
                message="回答已完成",
                answer=f"完成：{question}",
            )

    async def exercise() -> None:
        app = create_tui(BlockingAgent())

        try:
            async with app.run_test() as pilot:
                question = app.query_one("#question", Input)
                question.value = "检查所有 Pod"
                await pilot.press("enter")
                assert await asyncio.to_thread(started.wait, 1)

                assert question.disabled is True
                assert str(app.query_one("#status", Static).content) == "诊断中…"

                await pilot.press("enter")
                assert questions == ["检查所有 Pod"]

                await pilot.press("escape", "?")
                assert app.query_one("#help", Static).has_class("visible")

                release.set()
                await app.workers.wait_for_complete()
                assert str(app.query_one("#status", Static).content) == "完成"
        finally:
            release.set()

    asyncio.run(exercise())


def test_tui_ignores_late_answer_after_exit() -> None:
    started = Event()
    release = Event()
    finished = Event()

    class SlowAgent:
        def stream(self, question: str):
            started.set()
            release.wait(timeout=2)
            finished.set()
            yield AgentEvent(
                stage=AgentStage.COMPLETED,
                message="回答已完成",
                answer=f"迟到结果：{question}",
            )

    async def exercise() -> None:
        app = create_tui(SlowAgent())

        try:
            async with app.run_test() as pilot:
                question = app.query_one("#question", Input)
                result = app.query_one("#result", Markdown)
                question.value = "检查所有 Pod"
                await pilot.press("enter")
                assert await asyncio.to_thread(started.wait, 1)
                await pilot.press("escape", "q")

            release.set()
            assert await asyncio.to_thread(finished.wait, 1)
            await asyncio.sleep(0)
            assert "正在获取实时证据，请稍候。" in result.source
        finally:
            release.set()

    asyncio.run(exercise())


def test_tui_stacks_monitor_and_chat_in_narrow_terminal() -> None:
    async def exercise() -> None:
        app = create_tui(FakeAgent(answer="unused"))

        async with app.run_test(size=(80, 24)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.pause()

            monitor = app.query_one("#monitor-pane")
            chat = app.query_one("#chat-pane")
            assert monitor.region.y < chat.region.y

    asyncio.run(exercise())


def test_tui_periodically_refreshes_monitor() -> None:
    async def exercise() -> None:
        monitor = FakeMonitor()
        base_settings = create_app_settings()
        settings = base_settings.model_copy(
            update={
                "kubernetes": base_settings.kubernetes.model_copy(
                    update={
                        "watch": KubernetesWatchSettings(
                            enabled=False,
                            poll_interval_seconds=0.02,
                        )
                    }
                )
            }
        )
        app = create_tui(
            FakeAgent(answer="unused"),
            monitor=monitor,
            settings=settings,
        )

        async with app.run_test():
            await _wait_until(lambda: monitor.calls >= 2)

        assert monitor.calls >= 2

    asyncio.run(exercise())


def test_tui_falls_back_to_polling_when_watch_is_unavailable() -> None:
    class UnavailableWatchMonitor(FakeMonitor):
        def __init__(self) -> None:
            super().__init__()
            self.watch_calls = 0

        def wait_for_change(
            self,
            *,
            timeout_seconds: int,
            stop_event: Event | None = None,
        ) -> KubernetesWatchResult:
            self.watch_calls += 1
            return KubernetesWatchResult(
                outcome=KubernetesWatchOutcome.UNAVAILABLE,
                unavailable_reason="403 Forbidden",
            )

    async def exercise() -> None:
        monitor = UnavailableWatchMonitor()
        base_settings = create_app_settings()
        settings = base_settings.model_copy(
            update={
                "kubernetes": base_settings.kubernetes.model_copy(
                    update={
                        "watch": KubernetesWatchSettings(
                            enabled=True,
                            timeout_seconds=1,
                            poll_interval_seconds=0.02,
                        )
                    }
                )
            }
        )
        app = create_tui(
            FakeAgent(answer="unused"),
            monitor=monitor,
            settings=settings,
        )

        async with app.run_test():
            await _wait_until(lambda: monitor.calls >= 2)

        assert monitor.calls >= 2
        assert monitor.watch_calls >= 1
        assert monitor.stop_watch_calls >= 1

    asyncio.run(exercise())


def test_tui_refreshes_monitor_when_watch_reports_change() -> None:
    class WatchMonitor(FakeMonitor):
        def __init__(self) -> None:
            super().__init__()
            self.snapshot_ready = Event()
            self.watch_calls = 0

        def snapshot(self) -> KubernetesMonitorSnapshot:
            snapshot = super().snapshot()
            self.snapshot_ready.set()
            return snapshot

        def wait_for_change(
            self,
            *,
            timeout_seconds: int,
            stop_event: Event | None = None,
        ) -> KubernetesWatchResult:
            self.watch_calls += 1
            if self.watch_calls == 1:
                self.snapshot_ready.wait(timeout=1)
                return KubernetesWatchResult(
                    outcome=KubernetesWatchOutcome.CHANGED,
                    change=KubernetesChangeSignal(
                        resource_kind=KubernetesResourceKind.POD,
                        event_type="MODIFIED",
                        resource_name="sample-api-7f8",
                    ),
                )
            if stop_event is not None:
                stop_event.wait(timeout=timeout_seconds)
                if stop_event.is_set():
                    return KubernetesWatchResult(
                        outcome=KubernetesWatchOutcome.STOPPED,
                    )
            return KubernetesWatchResult(
                outcome=KubernetesWatchOutcome.TIMED_OUT,
            )

    async def exercise() -> None:
        monitor = WatchMonitor()
        base_settings = create_app_settings()
        settings = base_settings.model_copy(
            update={
                "kubernetes": base_settings.kubernetes.model_copy(
                    update={
                        "watch": KubernetesWatchSettings(
                            enabled=True,
                            timeout_seconds=1,
                            poll_interval_seconds=60.0,
                        )
                    }
                )
            }
        )
        app = create_tui(
            FakeAgent(answer="unused"),
            monitor=monitor,
            settings=settings,
        )

        async with app.run_test():
            await _wait_until(lambda: monitor.calls >= 2, timeout=0.5)

        assert monitor.calls >= 2
        assert monitor.watch_calls >= 1

    asyncio.run(exercise())


def test_tui_watch_refresh_preserves_selected_monitor_resource() -> None:
    class ReorderingWatchMonitor(FakeMonitor):
        def __init__(self) -> None:
            super().__init__()
            self.watch_started = Event()
            self.release_change = Event()
            self.watch_calls = 0

        def snapshot(self) -> KubernetesMonitorSnapshot:
            self.calls += 1
            snapshot = create_monitor_snapshot()
            pods = snapshot.resources[0]
            names = (
                ("sample-api-0", "sample-worker-0", "sample-frontend-0")
                if self.calls == 1
                else ("sample-frontend-0", "sample-api-0", "sample-worker-0")
            )
            rows = tuple(
                KubernetesResourceRow(
                    ref=KubernetesResourceRef(
                        kind=KubernetesResourceKind.POD,
                        name=name,
                    ),
                    values=(name, "1/1", "Running", "0", "1h"),
                    healthy=True,
                )
                for name in names
            )
            return replace(
                snapshot,
                resources=(replace(pods, rows=rows), *snapshot.resources[1:]),
            )

        def wait_for_change(
            self,
            *,
            timeout_seconds: int,
            stop_event: Event | None = None,
        ) -> KubernetesWatchResult:
            self.watch_calls += 1
            self.watch_started.set()
            if self.watch_calls == 1:
                self.release_change.wait(timeout=1)
                return KubernetesWatchResult(
                    outcome=KubernetesWatchOutcome.CHANGED,
                    change=KubernetesChangeSignal(
                        resource_kind=KubernetesResourceKind.POD,
                        event_type="MODIFIED",
                        resource_name="sample-worker-0",
                    ),
                )
            if stop_event is not None:
                stop_event.wait(timeout=timeout_seconds)
            return KubernetesWatchResult(
                outcome=KubernetesWatchOutcome.STOPPED,
            )

    async def exercise() -> None:
        monitor = ReorderingWatchMonitor()
        base_settings = create_app_settings()
        settings = base_settings.model_copy(
            update={
                "kubernetes": base_settings.kubernetes.model_copy(
                    update={
                        "watch": KubernetesWatchSettings(
                            enabled=True,
                            timeout_seconds=1,
                            poll_interval_seconds=60.0,
                        )
                    }
                )
            }
        )
        app = create_tui(
            FakeAgent(answer="unused"),
            monitor=monitor,
            settings=settings,
        )

        async with app.run_test(size=(140, 34)) as pilot:
            await _wait_until(
                lambda: monitor.calls == 1 and monitor.watch_started.is_set()
            )
            await pilot.press("ctrl+k", "1", "down")
            table = app.query_one("#monitor-table", DataTable)
            assert str(table.get_cell_at(Coordinate(table.cursor_row, 0))) == (
                "sample-worker-0"
            )

            monitor.release_change.set()
            await _wait_until(lambda: monitor.calls >= 2)
            await pilot.pause()

            assert str(table.get_cell_at(Coordinate(table.cursor_row, 0))) == (
                "sample-worker-0"
            )

    asyncio.run(exercise())


def test_tui_refresh_preserves_selected_monitor_resource() -> None:
    class MultiPodMonitor(FakeMonitor):
        def snapshot(self) -> KubernetesMonitorSnapshot:
            self.calls += 1
            snapshot = create_monitor_snapshot()
            pods = snapshot.resources[0]
            pod_names = (
                ("sample-api-0", "sample-worker-0", "sample-frontend-0")
                if self.calls == 1
                else ("sample-frontend-0", "sample-api-0", "sample-worker-0")
            )
            pod_rows = tuple(
                KubernetesResourceRow(
                    ref=KubernetesResourceRef(
                        kind=KubernetesResourceKind.POD,
                        name=name,
                    ),
                    values=(name, "1/1", "Running", "0", "1h"),
                    healthy=True,
                )
                for name in pod_names
            )
            return replace(
                snapshot,
                resources=(
                    replace(pods, rows=pod_rows),
                    *snapshot.resources[1:],
                ),
            )

    async def exercise() -> None:
        monitor = MultiPodMonitor()
        app = create_tui(
            FakeAgent(answer="unused"),
            monitor=monitor,
        )

        async with app.run_test(size=(140, 34)) as pilot:
            await app.workers.wait_for_complete()
            await pilot.press("ctrl+k", "1", "down")

            table = app.query_one("#monitor-table", DataTable)
            assert (
                str(table.get_cell_at(Coordinate(table.cursor_row, 0)))
                == "sample-worker-0"
            )

            await pilot.press("ctrl+r")
            await app.workers.wait_for_complete()
            await pilot.pause()

            assert (
                str(table.get_cell_at(Coordinate(table.cursor_row, 0)))
                == "sample-worker-0"
            )

    asyncio.run(exercise())


def test_tui_monitor_failure_can_recover_with_manual_refresh() -> None:
    class FlakyMonitor(FakeMonitor):
        def snapshot(self) -> KubernetesMonitorSnapshot:
            self.calls += 1
            if self.calls == 1:
                raise RuntimeError("cluster unavailable")
            return create_monitor_snapshot()

    async def exercise() -> None:
        monitor = FlakyMonitor()
        app = create_tui(
            FakeAgent(answer="unused"),
            monitor=monitor,
        )

        async with app.run_test() as pilot:
            await app.workers.wait_for_complete()
            assert "暂时不可用" in str(app.query_one("#monitor-title", Static).content)

            await pilot.press("ctrl+r")
            await app.workers.wait_for_complete()

            assert monitor.calls == 2
            assert "Overview · 7 resources" in str(
                app.query_one("#monitor-title", Static).content
            )
            assert app.query_one("#monitor-table", DataTable).row_count == 10

    asyncio.run(exercise())


def test_tui_coalesces_slow_refresh_and_ignores_late_snapshot_after_exit() -> None:
    started = Event()
    release = Event()
    finished = Event()
    state_lock = Lock()

    class SlowMonitor(FakeMonitor):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.maximum_active = 0

        def snapshot(self) -> KubernetesMonitorSnapshot:
            with state_lock:
                self.calls += 1
                self.active += 1
                self.maximum_active = max(self.maximum_active, self.active)
            started.set()
            try:
                release.wait(timeout=2)
                return create_monitor_snapshot()
            finally:
                with state_lock:
                    self.active -= 1
                finished.set()

    async def exercise() -> None:
        monitor = SlowMonitor()
        app = create_tui(
            FakeAgent(answer="unused"),
            monitor=monitor,
        )

        try:
            async with app.run_test() as pilot:
                title = app.query_one("#monitor-title", Static)
                assert await asyncio.to_thread(started.wait, 1)

                await pilot.press("ctrl+r", "ctrl+r")
                await asyncio.sleep(0.05)
                assert monitor.calls == 1
                assert monitor.maximum_active == 1

                await pilot.press("ctrl+c")

            release.set()
            assert await asyncio.to_thread(finished.wait, 1)
            await asyncio.sleep(0)
            assert str(title.content) == " LIVE · 正在连接 Kubernetes…"
            assert monitor.maximum_active == 1
        finally:
            release.set()

    asyncio.run(exercise())


async def _wait_until(predicate, *, timeout: float = 1.0) -> None:
    async with asyncio.timeout(timeout):
        while not predicate():
            await asyncio.sleep(0.01)
