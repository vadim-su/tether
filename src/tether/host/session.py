"""The session host: owns history, runs turns, and publishes events on the bus.

The agent is rebuilt from config and plugins on demand, while the history lives
here, so a rebuilt agent (new config, reloaded plugin) continues the conversation.
"""

import inspect
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from pydantic_ai import Agent, AgentRunResultEvent
from pydantic_ai.messages import ModelMessage

from tether.api.plugin import Command
from tether.host.bus import Bus


@dataclass
class TurnStarted:
    prompt: str


@dataclass
class TurnFinished:
    output: Any


@dataclass
class TurnFailed:
    error: BaseException


@dataclass
class Notice:
    """A host message for the user (command output, warnings)."""

    text: str


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

    @property
    def agent(self) -> Agent[Any, Any]:
        if self._agent is None:
            self._agent = self._agent_factory()
        return self._agent

    def invalidate_agent(self) -> None:
        """Rebuild the agent before the next turn; history is kept."""
        self._agent = None

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
        try:
            async with self.agent.run_stream_events(prompt, message_history=self.history) as events:
                async for event in events:
                    await self.bus.publish(event)
                    if isinstance(event, AgentRunResultEvent):
                        self.history = event.result.all_messages()
                        output = event.result.output
        except Exception as e:
            await self.bus.publish(TurnFailed(e))
            raise
        await self.bus.publish(TurnFinished(output))
        return output

    async def notify(self, text: str) -> None:
        await self.bus.publish(Notice(text))
