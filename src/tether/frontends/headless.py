"""Plain stdout frontend: model text to stdout, tool activity and notices to stderr.

Approvals and questions are asked on the terminal when stdin is interactive;
otherwise tool calls that need approval are denied and questions are declined.
"""

import asyncio
import json
import sys
from typing import Any, TextIO

from pydantic_ai import FunctionToolCallEvent, FunctionToolResultEvent, PartDeltaEvent, PartStartEvent
from pydantic_ai.messages import TextPart, TextPartDelta

from tether.api.events import (
    Approval,
    ApprovalRequested,
    AskUserAnswer,
    AskUserResponse,
    QuestionAsked,
    TurnCancelled,
)
from tether.host.session import Notice, Session, TurnError, TurnFailed, TurnFinished


class Headless:
    def __init__(
        self,
        out: TextIO | None = None,
        err: TextIO | None = None,
        *,
        interactive: bool | None = None,
    ) -> None:
        self.out = out or sys.stdout
        self.err = err or sys.stderr
        self.interactive = sys.stdin.isatty() if interactive is None else interactive
        self._mid_line = False
        self._session: Session | None = None
        self._tasks: set[asyncio.Task[Any]] = set()

    def attach(self, session: Session) -> None:
        self._session = session
        session.bus.subscribe(self.on_event)

    async def run(self, session: Session) -> None:
        """Interactive loop: read a line, run it, repeat until EOF or /exit."""
        self.attach(session)
        while True:
            try:
                text = await self._input("› ")
            except EOFError:
                return
            text = text.strip()
            if text in {"/exit", "/quit"}:
                return
            if not text:
                continue
            try:
                await session.handle(text)
            except TurnError:
                pass  # already shown
            except Exception as e:
                self._line(f"error: {e}", self.err)

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
            case TurnCancelled():
                self._line("cancelled", self.err)
            case TurnFinished():
                self._end_line()
            case ApprovalRequested() | QuestionAsked():
                self._spawn(self._answer(event))

    def _spawn(self, coro: Any) -> None:
        task = asyncio.get_running_loop().create_task(coro)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)

    async def _answer(self, request: ApprovalRequested | QuestionAsked) -> None:
        assert self._session is not None
        if isinstance(request, ApprovalRequested):
            answer: Any = await self._approve(request)
        else:
            answer = await self._ask(request)
        self._session.answer(request.id, answer)

    async def _approve(self, request: ApprovalRequested) -> Approval:
        if not self.interactive:
            return Approval(False, message="Denied: no one to approve it (non-interactive run).")
        self._line(f"? allow {request.tool_name}({json.dumps(request.args, ensure_ascii=False)})", self.err)
        reply = (await self._input("  [y]es / [a]lways / [N]o: ")).strip().lower()
        return Approval(reply in {"y", "yes", "a", "always"}, always=reply in {"a", "always"})

    async def _ask(self, event: QuestionAsked) -> AskUserResponse:
        if not self.interactive:
            return AskUserResponse(cancelled=True)
        answers = []
        for q in event.request.questions:
            self._line(f"? {q.question}", self.err)
            for i, option in enumerate(q.options, 1):
                extra = f" — {option.description}" if option.description else ""
                self._line(f"  {i}. {option.label}{extra}", self.err)
            hint = "numbers separated by spaces" if q.multi_select else "a number"
            reply = (await self._input(f"  {hint}, or your own answer (empty to skip): ")).strip()
            if not reply:
                return AskUserResponse(cancelled=True)
            picks = reply.replace(",", " ").split()
            if all(p.isdigit() and 1 <= int(p) <= len(q.options) for p in picks):
                labels = tuple(dict.fromkeys(q.options[int(p) - 1].label for p in picks))
                answers.append(
                    AskUserAnswer(header=q.header, selected=labels[: None if q.multi_select else 1])
                )
            else:
                answers.append(AskUserAnswer(header=q.header, custom_answer=reply))
        return AskUserResponse(answers=tuple(answers))

    async def _input(self, prompt: str) -> str:
        self._end_line()
        return await asyncio.get_running_loop().run_in_executor(None, input, prompt)

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
