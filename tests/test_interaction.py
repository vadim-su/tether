"""Approvals, AskUser and cancellation through the session's frontends."""

import asyncio
import json

import pytest
from pydantic_ai import Agent
from pydantic_ai.models.function import DeltaToolCall, FunctionModel
from pydantic_ai.models.test import TestModel

from tether.api.events import (
    Approval,
    ApprovalRequested,
    AskUserAnswer,
    AskUserResponse,
    QuestionAsked,
    RequestResolved,
    TurnCancelled,
)
from tether.cli import make_session
from tether.host import frontends
from tether.host.approvals import Approvals, decide
from tether.host.config import HostConfig, TetherConfig
from tether.host.loader import PluginSet
from tether.host.session import Session


def tool_agent(session: Session, policy, calls: list[str]) -> Agent:
    agent = Agent(TestModel(), capabilities=[Approvals(session, policy)])

    @agent.tool_plain
    def shell(cmd: str) -> str:
        calls.append(cmd)
        return "ran"

    return agent


def answering(session: Session, answer):
    """A fake frontend that answers every request with `answer`."""
    seen = []

    def on_event(event):
        seen.append(event)
        if isinstance(event, ApprovalRequested | QuestionAsked):
            session.answer(event.id, answer(event) if callable(answer) else answer)

    session.bus.subscribe(on_event)
    return seen


def test_policy_matching():
    policy = {"shell": "ask", "write_*": "deny", "*": "allow"}
    assert decide(policy, "shell") == "ask"
    assert decide(policy, "write_file") == "deny"
    assert decide(policy, "read_file") == "allow"
    assert decide({}, "anything") == "allow"


@pytest.mark.parametrize(
    ("answer", "ran"),
    [(Approval(True), True), (Approval(False, message="nope"), False)],
)
def test_ask_routes_to_frontend(answer, ran):
    calls: list[str] = []
    session = Session(lambda: tool_agent(session, {"shell": "ask"}, calls))
    seen = answering(session, answer)
    output = asyncio.run(session.send("go"))
    assert bool(calls) is ran
    requests = [e for e in seen if isinstance(e, ApprovalRequested)]
    assert len(requests) == 1 and requests[0].tool_name == "shell"
    assert any(isinstance(e, RequestResolved) and e.id == requests[0].id for e in seen)
    if not ran:
        assert "nope" in output


def test_always_approves_for_the_session():
    calls: list[str] = []
    session = Session(lambda: tool_agent(session, {"shell": "ask"}, calls))
    seen = answering(session, Approval(True, always=True))

    async def go():
        await session.send("one")
        session.history = []  # TestModel only calls tools on a fresh conversation
        await session.send("two")

    asyncio.run(go())
    assert len(calls) == 2
    assert sum(isinstance(e, ApprovalRequested) for e in seen) == 1


def test_deny_never_asks():
    calls: list[str] = []
    session = Session(lambda: tool_agent(session, {"shell": "deny"}, calls))
    seen = answering(session, Approval(True))
    output = asyncio.run(session.send("go"))
    assert calls == []
    assert "forbidden" in output
    assert not any(isinstance(e, ApprovalRequested) for e in seen)


def test_default_policy_asks_before_coder_shell(tmp_path):
    config = TetherConfig(
        agent={"capabilities": [{"LocalWorkspace": {"working_dir": str(tmp_path)}}, "Coder"]},
        host=HostConfig(),
    )
    session = make_session(config, PluginSet(), model=TestModel(call_tools=["shell"]))
    seen = answering(session, Approval(False))
    asyncio.run(session.send("run something"))
    assert [e.tool_name for e in seen if isinstance(e, ApprovalRequested)] == ["shell"]


def test_ask_user_from_config_is_answered_by_frontend():
    async def model(messages, info):
        if len(messages) == 1:
            options = [{"label": "Red"}, {"label": "Blue"}]
            question = {"header": "Color", "question": "Which?", "options": options}
            yield {0: DeltaToolCall("ask_user_question", json.dumps({"questions": [question]}))}
        else:
            yield str(messages[-1].parts[0].content)

    config = TetherConfig(agent={"capabilities": ["AskUser"]}, host=HostConfig())
    session = make_session(config, PluginSet(), model=FunctionModel(stream_function=model))

    def pick_first(event: QuestionAsked) -> AskUserResponse:
        q = event.request.questions[0]
        return AskUserResponse(answers=(AskUserAnswer(header=q.header, selected=(q.options[0].label,)),))

    seen = answering(session, pick_first)
    output = asyncio.run(session.send("ask me"))
    assert any(isinstance(e, QuestionAsked) for e in seen)
    assert "Red" in output


def test_cancel_abandons_pending_request():
    calls: list[str] = []
    session = Session(lambda: tool_agent(session, {"shell": "ask"}, calls))
    seen: list = []

    def on_event(event):
        seen.append(event)
        if isinstance(event, ApprovalRequested):
            asyncio.get_running_loop().call_soon(session.cancel)

    session.bus.subscribe(on_event)

    async def go():
        with pytest.raises(asyncio.CancelledError):
            await asyncio.create_task(session.send("go"))

    asyncio.run(go())
    assert calls == [] and not session.busy and session.pending == []
    assert any(isinstance(e, TurnCancelled) for e in seen)
    assert any(isinstance(e, RequestResolved) and e.answer is None for e in seen)


def test_frontend_registry():
    assert "headless" in frontends.available()
    with pytest.raises(LookupError, match="unknown frontend"):
        frontends.create("nope")


def test_first_frontend_to_exit_ends_the_session():
    stopped = []

    class Quick:
        async def run(self, session):
            return None

    class Forever:
        async def run(self, session):
            try:
                await asyncio.Event().wait()
            finally:
                stopped.append(True)

    session = Session(lambda: Agent(TestModel()))
    asyncio.run(frontends.run_frontends(session, [Forever(), Quick()]))
    assert stopped == [True]
