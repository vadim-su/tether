"""The Textual app: a chat log, a prompt, a status line and dialogs for requests."""

from pathlib import Path
from typing import Any

from pydantic_ai import (
    AgentRunResultEvent,
    FunctionToolCallEvent,
    FunctionToolResultEvent,
    PartDeltaEvent,
    PartEndEvent,
    PartStartEvent,
)
from pydantic_ai.messages import BinaryContent, TextPart, TextPartDelta, ThinkingPart, ThinkingPartDelta
from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.suggester import SuggestFromList
from textual.widgets import Footer, Input, Static

from tether.api.events import (
    ApprovalRequested,
    Notice,
    QuestionAsked,
    RequestResolved,
    TurnCancelled,
    TurnFailed,
    TurnFinished,
    TurnStarted,
)
from tether.host.session import Session, TurnError
from tether_plugin_tui import kitty
from tether_plugin_tui.dialogs import ApprovalDialog, QuestionDialog
from tether_plugin_tui.kitty import ImageMode
from tether_plugin_tui.widgets import (
    AssistantMessage,
    ImageView,
    NoticeMessage,
    Thinking,
    ToolCall,
    UserMessage,
)

SPINNER = "⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏"
LOCAL_COMMANDS = {
    "/exit": "quit",
    "/quit": "quit",
    "/clear": "clear the screen (history is kept)",
    "/image": "show an image file: /image path.png",
}


class TetherApp(App[None]):
    CSS_PATH = "tether.tcss"
    TITLE = "tether"
    BINDINGS = [
        Binding("escape", "cancel_turn", "cancel turn", show=True),
        Binding("ctrl+l", "clear", "clear"),
        Binding("ctrl+d", "quit", "quit", show=False),
    ]

    def __init__(self, session: Session, *, theme: str | None = None, images: ImageMode = "auto") -> None:
        super().__init__()
        self.session = session
        self.images = kitty.supported(images)
        if theme:
            self.theme = theme
        self._unsubscribe: Any = None
        self._text: AssistantMessage | None = None
        self._thinking: Thinking | None = None
        self._tools: dict[str, ToolCall] = {}
        self._dialogs: dict[str, Screen[Any]] = {}
        self._spin = 0
        self._model = "?"

    def compose(self) -> ComposeResult:
        with VerticalScroll(id="log"):
            yield Static(self._banner(), id="banner")
        yield Static("", id="status")
        commands = sorted({*self.session.commands, *LOCAL_COMMANDS})
        yield Input(placeholder="Message, or /help", id="prompt", suggester=SuggestFromList(commands))
        yield Footer()

    def on_mount(self) -> None:
        self._unsubscribe = self.session.bus.subscribe(self.on_session_event)
        self.query_one("#log", VerticalScroll).anchor()
        try:
            model = self.session.agent.model
            self._model = getattr(model, "model_name", None) or str(model)
        except Exception as e:  # e.g. a missing API key: say so now, not on the first turn
            self.query_one("#log").mount(NoticeMessage(f"error: {e}", kind="error"))
        self.query_one("#banner", Static).update(self._banner())
        self.set_interval(0.1, self._tick)
        self._update_status()
        self.query_one("#prompt", Input).focus()

    def on_unmount(self) -> None:
        if self._unsubscribe is not None:
            self._unsubscribe()
        self.session.cancel()

    # Input.

    async def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id != "prompt":
            return
        text = event.value.strip()
        if not text:
            return
        if self.session.busy:
            self.notify("The agent is busy; press esc to cancel the turn", severity="warning")
            return
        event.input.value = ""
        if text in {"/exit", "/quit"}:
            self.exit()
            return
        if text == "/clear":
            self.action_clear()
            return
        if text.partition(" ")[0] == "/image":
            await self._show_image_file(text.partition(" ")[2].strip())
            return
        if not text.startswith("/") or text.partition(" ")[0] not in self.session.commands:
            await self._add(UserMessage(text))
        self.run_worker(self._handle(text), group="turn", exit_on_error=False)

    async def _handle(self, text: str) -> None:
        try:
            await self.session.handle(text)
        except TurnError:
            pass  # shown from TurnFailed
        except Exception as e:  # a command handler failed
            await self._add(NoticeMessage(f"error: {e}", kind="error"))

    async def _show_image_file(self, path: str) -> None:
        try:
            data = Path(path).expanduser().read_bytes()
        except OSError as e:
            await self._add(NoticeMessage(f"error: {e}", kind="error"))
            return
        await self._add(ImageView(data, enabled=self.images, caption=Path(path).name))

    def action_cancel_turn(self) -> None:
        if not self.session.cancel():
            self.notify("Nothing to cancel")

    def action_clear(self) -> None:
        log = self.query_one("#log", VerticalScroll)
        for child in list(log.children):
            if child.id != "banner":
                child.remove()
        self._tools.clear()

    # Session events.

    async def on_session_event(self, event: Any) -> None:
        match event:
            case TurnStarted():
                self._update_status()
            case PartStartEvent(part=TextPart(content=text)):
                await self._end_parts()
                self._text = AssistantMessage()
                await self._add(self._text)
                await self._text.write(text)
            case PartDeltaEvent(delta=TextPartDelta(content_delta=text)) if self._text is not None:
                await self._text.write(text)
            case PartStartEvent(part=ThinkingPart(content=text)):
                await self._end_parts()
                self._thinking = Thinking()
                await self._add(self._thinking)
                self._thinking.write(text)
            case PartDeltaEvent(delta=ThinkingPartDelta(content_delta=text)) if self._thinking is not None:
                self._thinking.write(text or "")
            case PartEndEvent():
                await self._end_parts()
            case FunctionToolCallEvent(part=part):
                await self._end_parts()
                widget = self._tools[part.tool_call_id] = ToolCall(part)
                await self._add(widget)
            case FunctionToolResultEvent(part=part, content=extra):
                if (widget := self._tools.pop(part.tool_call_id, None)) is not None:
                    widget.finish(part)
                for image in _images(part.content) + _images(extra):
                    await self._add(ImageView(image.data, enabled=self.images, caption=part.tool_name))
            case AgentRunResultEvent():
                self._update_status()
            case ApprovalRequested() | QuestionAsked():
                self._open_dialog(event)
            case RequestResolved(id=request_id):
                if (screen := self._dialogs.pop(request_id, None)) is not None and screen.is_active:
                    screen.dismiss(None)
            case Notice(text=text):
                await self._add(NoticeMessage(text))
            case TurnFailed(error=error):
                await self._end_parts()
                await self._add(NoticeMessage(f"error: {error}", kind="error"))
                self._turn_over()
            case TurnCancelled():
                await self._end_parts()
                await self._add(NoticeMessage("cancelled", kind="warning"))
                self._turn_over()
            case TurnFinished():
                await self._end_parts()
                self._turn_over()

    def _open_dialog(self, event: ApprovalRequested | QuestionAsked) -> None:
        screen: Screen[Any] = (
            ApprovalDialog(event) if isinstance(event, ApprovalRequested) else QuestionDialog(event)
        )
        self._dialogs[event.id] = screen

        def answered(answer: Any) -> None:
            self._dialogs.pop(event.id, None)
            if answer is not None:
                self.session.answer(event.id, answer)

        self.push_screen(screen, answered)

    async def _end_parts(self) -> None:
        if self._text is not None:
            await self._text.finish()
            self._text = None
        self._thinking = None

    def _turn_over(self) -> None:
        for widget in self._tools.values():  # calls that never got a result
            widget.title = widget.title.replace("◌", "⊘", 1)
            widget.remove_class("running")
        self._tools.clear()
        self._update_status()

    async def _add(self, widget: Any) -> None:
        await self.query_one("#log", VerticalScroll).mount(widget)

    # Status line.

    def _tick(self) -> None:
        if self.session.busy:
            self._spin = (self._spin + 1) % len(SPINNER)
            self._update_status()

    def _update_status(self) -> None:
        usage = self.session.usage
        state = f"{SPINNER[self._spin]} working · esc to cancel" if self.session.busy else "ready"
        tokens = f"{_k(usage.input_tokens)} in · {_k(usage.output_tokens)} out"
        self.query_one("#status", Static).update(f"{state}   [dim]{self._model} · {tokens}[/dim]")

    def _banner(self) -> str:
        return f"[b]tether[/b] [dim]· {self._model} · /help for commands · esc cancels · ctrl+q quits[/dim]"


def _images(content: Any) -> list[BinaryContent]:
    """Images in a tool result: a `BinaryContent`, or one inside a list."""
    items = content if isinstance(content, list | tuple) else [content]
    return [item for item in items if isinstance(item, BinaryContent) and item.is_image]


def _k(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)
