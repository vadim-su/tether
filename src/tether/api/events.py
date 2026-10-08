"""Host events: what a session publishes on its bus besides native Pydantic AI stream events.

A frontend subscribes to `session.bus` and receives, in order:

- `TurnStarted`, then the agent's own events (`PartStartEvent`, `PartDeltaEvent`,
  `FunctionToolCallEvent`, `FunctionToolResultEvent`, `AgentRunResultEvent`, ...),
  then `TurnFinished`, `TurnFailed` or `TurnCancelled`;
- `Notice` for command output and host messages;
- requests that wait for a person: `ApprovalRequested` and `QuestionAsked`.
  Any frontend may answer with `session.answer(request.id, ...)`; the first answer wins
  and `RequestResolved` tells the others to close their dialogs.
"""

from dataclasses import dataclass, field
from typing import Any
from uuid import uuid4

from pydantic_ai_harness.ask_user import AskUserAnswer, AskUserRequest, AskUserResponse


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
class TurnCancelled:
    pass


@dataclass
class Notice:
    """A host message for the user (command output, warnings)."""

    text: str


def _new_id() -> str:
    return uuid4().hex


@dataclass
class ApprovalRequested:
    """A tool call waits for approval; answer with an `Approval`."""

    tool_name: str
    args: dict[str, Any]
    tool_call_id: str
    id: str = field(default_factory=_new_id)


@dataclass
class Approval:
    approved: bool
    always: bool = False
    """Approve this tool for the rest of the session without asking again."""
    message: str | None = None
    """Shown to the model when the call is denied."""


@dataclass
class QuestionAsked:
    """The model asks the user multiple-choice questions (`AskUser`); answer with an `AskUserResponse`."""

    request: AskUserRequest

    @property
    def id(self) -> str:
        return self.request.id


@dataclass
class RequestResolved:
    """A request was answered (or abandoned, with `answer=None`); frontends close its dialog."""

    id: str
    answer: Any


type Request = ApprovalRequested | QuestionAsked

__all__ = [
    "Approval",
    "ApprovalRequested",
    "AskUserAnswer",
    "AskUserRequest",
    "AskUserResponse",
    "Notice",
    "QuestionAsked",
    "Request",
    "RequestResolved",
    "TurnCancelled",
    "TurnFailed",
    "TurnFinished",
    "TurnStarted",
]
