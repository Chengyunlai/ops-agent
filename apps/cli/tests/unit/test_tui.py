import asyncio
from threading import Event

from ops_agent_cli.tui.app import OpsAgentTui
from textual.widgets import Input, Static


class FakeAgent:
    def __init__(self, *, answer: str) -> None:
        self.answer = answer
        self.questions: list[str] = []

    def ask(self, question: str) -> str:
        self.questions.append(question)
        return self.answer


def test_tui_displays_context_and_agent_answer() -> None:
    async def exercise() -> None:
        agent = FakeAgent(answer="sample-api 正在运行")
        app = OpsAgentTui(
            agent=agent,
            environment="test",
            namespace="sample",
        )

        async with app.run_test() as pilot:
            context = app.query_one("#context", Static)
            question = app.query_one("#question", Input)

            assert "test" in str(context.content)
            assert "sample" in str(context.content)
            assert "只读" in str(context.content)

            question.value = "检查所有 Pod"
            await pilot.press("enter")
            await app.workers.wait_for_complete()

            result = app.query_one("#result", Static)
            status = app.query_one("#status", Static)
            assert agent.questions == ["检查所有 Pod"]
            assert str(result.content) == "sample-api 正在运行"
            assert str(status.content) == "完成"
            assert question.disabled is False

    asyncio.run(exercise())


def test_tui_recovers_after_agent_error() -> None:
    class FailingAgent:
        def ask(self, question: str) -> str:
            raise RuntimeError(f"无法处理：{question}")

    async def exercise() -> None:
        app = OpsAgentTui(
            agent=FailingAgent(),
            environment="test",
            namespace="sample",
        )

        async with app.run_test() as pilot:
            question = app.query_one("#question", Input)
            question.value = "检查所有 Pod"
            await pilot.press("enter")
            await app.workers.wait_for_complete()

            result = app.query_one("#result", Static)
            status = app.query_one("#status", Static)
            assert str(result.content) == "诊断失败：无法处理：检查所有 Pod"
            assert str(status.content) == "失败"
            assert question.disabled is False

    asyncio.run(exercise())


def test_tui_ignores_empty_question_and_toggles_help() -> None:
    async def exercise() -> None:
        agent = FakeAgent(answer="不应调用")
        app = OpsAgentTui(
            agent=agent,
            environment="test",
            namespace="sample",
        )

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
        def ask(self, question: str) -> str:
            questions.append(question)
            started.set()
            if not release.wait(timeout=2):
                raise RuntimeError("测试中的 Agent 未被释放")
            return f"完成：{question}"

    async def exercise() -> None:
        app = OpsAgentTui(
            agent=BlockingAgent(),
            environment="test",
            namespace="sample",
        )

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
        def ask(self, question: str) -> str:
            started.set()
            release.wait(timeout=2)
            finished.set()
            return f"迟到结果：{question}"

    async def exercise() -> None:
        app = OpsAgentTui(
            agent=SlowAgent(),
            environment="test",
            namespace="sample",
        )

        try:
            async with app.run_test() as pilot:
                question = app.query_one("#question", Input)
                result = app.query_one("#result", Static)
                question.value = "检查所有 Pod"
                await pilot.press("enter")
                assert await asyncio.to_thread(started.wait, 1)
                await pilot.press("escape", "q")

            release.set()
            assert await asyncio.to_thread(finished.wait, 1)
            await asyncio.sleep(0)
            assert str(result.content) == "正在获取实时证据，请稍候。"
        finally:
            release.set()

    asyncio.run(exercise())
