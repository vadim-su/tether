import asyncio
import json

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel
from textual.widgets import Markdown, RadioSet

from tether.api.events import Approval
from tether.cli import make_session
from tether.host import frontends
from tether.host.approvals import Approvals
from tether.host.config import HostConfig, TetherConfig
from tether.host.loader import PluginSet
from tether.host.session import Session
from tether_plugin_tui import TuiFrontend
from tether_plugin_tui.app import TetherApp
from tether_plugin_tui.dialogs import ApprovalDialog, QuestionDialog
from tether_plugin_tui.widgets import NoticeMessage, ToolCall, UserMessage


def shell_session(calls: list[str], policy=None) -> Session:
    def factory():
        agent = Agent(
            TestModel(), capabilities=[Approvals(session, {"shell": "ask"} if policy is None else policy)]
        )

        @agent.tool_plain
        def shell(command: str) -> str:
            calls.append(command)
            return "ran"

        return agent

    session = Session(factory)
    return session


async def submit(pilot, text: str) -> None:
    pilot.app.query_one("#prompt").value = text
    await pilot.press("enter")


async def settle(pilot, session: Session) -> None:
    for _ in range(100):
        await pilot.pause()
        if not session.busy:
            return
    raise AssertionError("turn did not finish")


def test_registered_as_frontend():
    assert "tui" in frontends.available()
    assert isinstance(frontends.create("tui"), TuiFrontend)


def test_chat_turn_renders_text_and_tools():
    calls: list[str] = []
    session = shell_session(calls, policy={})

    async def go():
        app = TetherApp(session)
        async with app.run_test() as pilot:
            await submit(pilot, "hello")
            await settle(pilot, session)
            assert len(app.query(UserMessage)) == 1
            tools = list(app.query(ToolCall))
            assert len(tools) == 1 and tools[0].has_class("success")
            text = "".join(m.source for m in app.query(Markdown) if m.has_class("assistant"))
            assert "ran" in text
            assert "out" in str(app.query_one("#status").render())

    asyncio.run(go())
    assert calls


@pytest.mark.parametrize(("key", "ran"), [("y", True), ("n", False)])
def test_approval_dialog_allows_and_denies(key, ran):
    calls: list[str] = []
    session = shell_session(calls)

    async def go():
        app = TetherApp(session)
        async with app.run_test() as pilot:
            await submit(pilot, "do it")
            for _ in range(50):
                await pilot.pause()
                if isinstance(app.screen, ApprovalDialog):
                    break
            assert isinstance(app.screen, ApprovalDialog)
            assert app.screen.request.tool_name == "shell"
            await pilot.press(key)
            await settle(pilot, session)
            assert not isinstance(app.screen, ApprovalDialog)

    asyncio.run(go())
    assert bool(calls) is ran


def test_question_dialog_answers_ask_user():
    async def model(messages, info):
        if len(messages) == 1:
            options = [{"label": "Red"}, {"label": "Blue", "description": "the calm one"}]
            question = {"header": "Color", "question": "Which?", "options": options}
            yield {0: DeltaToolCall("ask_user_question", json.dumps({"questions": [question]}))}
        else:
            yield "picked " + str(messages[-1].parts[0].content)

    config = TetherConfig(agent={"capabilities": ["AskUser"]}, host=HostConfig())
    session = make_session(config, PluginSet(), model=FunctionModel(stream_function=model))

    async def go():
        app = TetherApp(session)
        async with app.run_test() as pilot:
            await submit(pilot, "ask me")
            for _ in range(50):
                await pilot.pause()
                if isinstance(app.screen, QuestionDialog):
                    break
            assert isinstance(app.screen, QuestionDialog)
            radio = app.screen.query_one(RadioSet)
            radio.focus()
            await pilot.press("down", "enter")  # move to Blue, select it
            await pilot.press("ctrl+s")
            await settle(pilot, session)
            text = "".join(m.source for m in app.query(Markdown) if m.has_class("assistant"))
            assert "Blue" in text

    asyncio.run(go())


def test_escape_cancels_running_turn():
    async def slow(messages, info):
        await asyncio.sleep(30)
        yield "never"

    session = Session(lambda: Agent(FunctionModel(stream_function=slow)))

    async def go():
        app = TetherApp(session)
        async with app.run_test() as pilot:
            await submit(pilot, "wait")
            await pilot.pause()
            assert session.busy
            await pilot.press("escape")
            await settle(pilot, session)
            assert any("cancelled" in str(n.render()) for n in app.query(NoticeMessage))

    asyncio.run(go())


def test_answer_elsewhere_closes_dialog():
    calls: list[str] = []
    session = shell_session(calls)

    async def go():
        app = TetherApp(session)
        async with app.run_test() as pilot:
            await submit(pilot, "do it")
            for _ in range(50):
                await pilot.pause()
                if isinstance(app.screen, ApprovalDialog):
                    break
            session.answer(app.screen.request.id, Approval(True))  # e.g. from a web frontend
            await settle(pilot, session)
            assert not isinstance(app.screen, ApprovalDialog)

    asyncio.run(go())
    assert calls
