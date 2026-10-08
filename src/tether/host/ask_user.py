"""`AskUser` from tether.yaml, with its answerer bound to the session's frontends."""

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic_ai.capabilities import AbstractCapability

if TYPE_CHECKING:
    from tether.host.session import Session


def ask_user_type(session: Session) -> type[AbstractCapability[Any]]:
    """A capability type named `AskUser` that builds harness `AskUser(answerer=session.ask_user)`."""

    @dataclass
    class SessionAskUser(AbstractCapability[Any]):
        @classmethod
        def get_serialization_name(cls) -> str | None:
            return "AskUser"

        @classmethod
        def from_spec(cls, *args: Any, **kwargs: Any) -> AbstractCapability[Any]:
            from pydantic_ai_harness import AskUser

            if args:
                raise ValueError("AskUser takes keyword config only, e.g. {timeout: 300}")
            return AskUser(answerer=session.ask_user, **kwargs)

    return SessionAskUser
