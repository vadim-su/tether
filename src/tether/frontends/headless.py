"""Plain stdout frontend: model text to stdout, tool activity and notices to stderr."""

from __future__ import annotations

import sys
from typing import Any, TextIO

from pydantic_ai import FunctionToolCallEvent, FunctionToolResultEvent, PartDeltaEvent, PartStartEvent
from pydantic_ai.messages import TextPart, TextPartDelta

from tether.host.session import Notice, Session, TurnFailed, TurnFinished


class Headless:
    def __init__(self, out: TextIO | None = None, err: TextIO | None = None) -> None:
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self._mid_line = False

    def attach(self, session: Session) -> None:
        session.bus.subscribe(self.on_event)

    def on_event(self, event: Any) -> None:
        match event:
            case PartStartEvent(part=TextPart(content=text)):
                self._text(text)
            case PartDeltaEvent(delta=TextPartDelta(content_delta=text)):
                self._text(text)
            case FunctionToolCallEvent(part=part):
                self._line(f"→ {part.tool_name}({part.args_as_json_str()})", self.err)
            case FunctionToolResultEvent(part=part):
                self._line(f"← {part.tool_name}: {_preview(part.content)}", self.err)
            case Notice(text=text):
                self._line(text, self.err)
            case TurnFailed(error=error):
                self._line(f"error: {error}", self.err)
            case TurnFinished():
                self._end_line()

    def _text(self, text: str) -> None:
        if text:
            self.out.write(text)
            self.out.flush()
            self._mid_line = not text.endswith("\n")

    def _line(self, text: str, stream: TextIO) -> None:
        self._end_line()
        print(text, file=stream, flush=True)

    def _end_line(self) -> None:
        if self._mid_line:
            self.out.write("\n")
            self.out.flush()
            self._mid_line = False


def _preview(content: Any, limit: int = 200) -> str:
    text = str(content).replace("\n", " ")
    return text if len(text) <= limit else text[: limit - 1] + "…"
