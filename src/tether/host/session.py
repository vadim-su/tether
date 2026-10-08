"""The session host: owns history, runs turns, and publishes events on the bus.

The agent is rebuilt from config and plugins on demand, while the history lives
here, so a rebuilt agent (new config, reloaded plugin) continues the conversation.
Requests that need a person (tool approvals, `AskUser` questions) are published on
the bus and wait until some frontend calls `answer`.
"""

import asyncio
import inspect
from collections.abc import Callable
from typing import Any

from pydantic_ai import Agent, AgentRunResultEvent
from pydantic_ai.messages import ModelMessage
from pydantic_ai.usage import RunUsage

from tether.api.events import (
    Approval,
    ApprovalRequested,
    AskUserRequest,
    AskUserResponse,
    Notice,
    QuestionAsked,
    Request,
    RequestResolved,
    TurnCancelled,
    TurnFailed,
    TurnFinished,
    TurnStarted,
)
from tether.api.plugin import Command
from tether.host.bus import Bus

__all__ = ["Notice", "Session", "TurnError", "TurnFailed", "TurnFinished", "TurnStarted"]


class TurnError(Exception):
    """A turn failed; the cause was already published as `TurnFailed`."""


class Session:
    def __init__(
        self,
        agent_factory: Callable[[], Agent[Any, Any]],
        *,
        commands: dict[str, Command] | None = None,
        bus: Bus | None = None,
    ) -> None:
        self._agent_factory = agent_factory
        self._agent: Agent[Any, Any] | None = None
        self.commands = dict(commands or {})
        self.bus = bus or Bus()
        self.history: list[ModelMessage] = []
        self.usage = RunUsage()
        """Token usage summed over the session's turns."""
        self.always_approved: set[str] = set()
        """Tools the user approved for the rest of the session."""
        self._pending: dict[str, asyncio.Future[Any]] = {}
        self._turn: asyncio.Task[Any] | None = None

    @property
    def agent(self) -> Agent[Any, Any]:
        if self._agent is None:
            self._agent = self._agent_factory()
        return self._agent

    def invalidate_agent(self) -> None:
        """Rebuild the agent before the next turn; history is kept."""
        self._agent = None

    @property
    def busy(self) -> bool:
        return self._turn is not None

    async def handle(self, text: str) -> Any:
        """Entry point for frontends: run a slash command or a prompt."""
        if text.startswith("/"):
            name, _, args = text.partition(" ")
            if (command := self.commands.get(name)) is not None:
                result = command.handler(self, args.strip())
                if inspect.isawaitable(result):
                    await result
                return None
        return await self.send(text)

    async def send(self, prompt: str) -> Any:
        await self.bus.publish(TurnStarted(prompt))
        output: Any = None
        self._turn = asyncio.current_task()
        try:
            async with self.agent.run_stream_events(prompt, message_history=self.history) as events:
                async for event in events:
                    await self.bus.publish(event)
                    if isinstance(event, AgentRunResultEvent):
                        self.history = event.result.all_messages()
                        self.usage = self.usage + event.result.usage
                        output = event.result.output
        except asyncio.CancelledError:
            await self.bus.publish(TurnCancelled())
            raise
        except Exception as e:
            await self.bus.publish(TurnFailed(e))
            raise TurnError(str(e)) from e
        finally:
            self._turn = None
        await self.bus.publish(TurnFinished(output))
        return output

    def cancel(self) -> bool:
        """Cancel the running turn, if any. History stays as it was before the turn."""
        if self._turn is None or self._turn.done():
            return False
        self._turn.cancel()
        return True

    async def notify(self, text: str) -> None:
        await self.bus.publish(Notice(text))

    # Requests that wait for a person.

    async def request(self, request: Request) -> Any:
        """Publish a request and wait until a frontend answers it."""
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        self._pending[request.id] = future
        answer: Any = None
        try:
            await self.bus.publish(request)
            answer = await future
            return answer
        finally:
            self._pending.pop(request.id, None)
            await self.bus.publish(RequestResolved(request.id, answer))

    def answer(self, request_id: str, answer: Any) -> bool:
        """Answer a pending request. Returns False if it was already answered or abandoned."""
        future = self._pending.get(request_id)
        if future is None or future.done():
            return False
        future.set_result(answer)
        return True

    @property
    def pending(self) -> list[str]:
        return list(self._pending)

    async def approve(self, tool_name: str, args: dict[str, Any], tool_call_id: str) -> Approval:
        if tool_name in self.always_approved:
            return Approval(approved=True)
        answer = await self.request(ApprovalRequested(tool_name, args, tool_call_id))
        if not isinstance(answer, Approval):
            answer = Approval(approved=bool(answer))
        if answer.approved and answer.always:
            self.always_approved.add(tool_name)
        return answer

    async def ask_user(self, request: AskUserRequest) -> AskUserResponse:
        """An `AskUser` answerer bound to this session's frontends."""
        answer = await self.request(QuestionAsked(request))
        return answer if isinstance(answer, AskUserResponse) else AskUserResponse(cancelled=True)
