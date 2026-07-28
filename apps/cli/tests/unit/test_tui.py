import asyncio
from dataclasses import replace
from datetime import UTC, datetime
from threading import Event, Lock
from types import SimpleNamespace

from ops_agent.agent import (
    AgentEvent,
    AgentStage,
    CapabilityScope,
    InteractionChannel,
)
from ops_agent.kubernetes import KubernetesResourceKind
from ops_agent.monitoring import (
    KubernetesMonitorSnapshot,
    KubernetesResourceCollection,
    KubernetesResourceContent,
    KubernetesResourceRef,
    KubernetesResourceRow,
)
from ops_agent_cli import tui as tui_module
from ops_agent_cli.tui import run_tui
from ops_agent_cli.tui.app import OpsAgentTui
from textual.coordinate import Coordinate
from textual.widgets import DataTable, Input, Markdown, RichLog, Static


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
        self.content_calls: list[tuple[str, object]] = []

    def snapshot(self) -> KubernetesMonitorSnapshot:
        self.calls += 1
        return create_monitor_snapshot()

    def describe(
        self,
        resource: KubernetesResourceRef,
    ) -> KubernetesResourceContent:
        self.content_calls.append(("describe", resource))
        return KubernetesResourceContent(
            title=f"Describe · {resource.kind}/{resource.name}",
            content=f"kind: {resource.kind}\nmetadata:\n  name: {resource.name}",
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


def create_monitor_snapshot() -> KubernetesMonitorSnapshot:
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
                ("NAME", "READY", "STATUS", "RESTARTS"),
                ("sample-api-7f8", "2/2", "Running", "1"),
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
                None,
                ("NAME",),
                None,
            ),
        ),
    )


def create_tui(conversation, *, monitor=None) -> OpsAgentTui:
    return OpsAgentTui(
        conversation=conversation,
        monitor=monitor or FakeMonitor(),
        environment="test",
        namespace="sample",
    )


def test_run_tui_opens_kubernetes_scoped_conversation(
    tmp_path,
    monkeypatch,
) -> None:
    contexts = []
    session = object()
    monitor = object()
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
        ) -> None:
            received.update(
                conversation=conversation,
                monitor=monitor,
                environment=environment,
                namespace=namespace,
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
            environment="test",
            namespace="sample",
        ),
    )
    monkeypatch.setattr(tui_module, "OpsAgentTui", FakeTui)

    run_tui(tmp_path / "test.toml")

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
        "ran": True,
        "run_options": {"mouse": False},
    }


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
            ] == ["1", "1", "1", "1", "1", "1", "0", "0", "0", "0"]
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
        app = create_tui(
            FakeAgent(answer="unused"),
            monitor=monitor,
        )
        app.MONITOR_REFRESH_SECONDS = 0.02

        async with app.run_test():
            await _wait_until(lambda: monitor.calls >= 2)

        assert monitor.calls >= 2

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
            assert "Overview · 6 resources" in str(
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
