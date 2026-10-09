"""Chat log entries."""

import json
from typing import Any

from pydantic_ai.messages import RetryPromptPart, ToolCallPart, ToolReturnPart
from rich.markup import escape
from rich.text import Text
from textual.containers import Vertical
from textual.widget import Widget
from textual.widgets import Collapsible, Markdown, Static

from tether_plugin_tui import kitty


class UserMessage(Static):
    def __init__(self, text: str) -> None:
        super().__init__(escape(text), classes="user", markup=True)


class AssistantMessage(Markdown):
    """Streams markdown as the model writes it."""

    def __init__(self) -> None:
        super().__init__(classes="assistant")
        self._stream = Markdown.get_stream(self)

    async def write(self, text: str) -> None:
        if text:
            await self._stream.write(text)

    async def finish(self) -> None:
        await self._stream.stop()


class Thinking(Collapsible):
    def __init__(self) -> None:
        self._body = Static("", classes="thinking-body", markup=False)
        self._text = ""
        super().__init__(self._body, title="thinking", collapsed=True, classes="thinking")

    def write(self, text: str) -> None:
        self._text += text
        self._body.update(self._text)


class ToolCall(Collapsible):
    """A tool call: a one-line title with the arguments, and the result when it arrives."""

    def __init__(self, part: ToolCallPart) -> None:
        self.part = part
        args = _args(part)
        self._args = Static(_pretty(args), classes="tool-args", markup=False)
        self._result = Static("…", classes="tool-result", markup=False)
        super().__init__(
            Vertical(self._args, self._result),
            title=f"◌ {part.tool_name}  {_summary(args)}",
            collapsed=True,
            classes="tool running",
        )

    def finish(self, part: ToolReturnPart | RetryPromptPart) -> None:
        if isinstance(part, ToolReturnPart):
            outcome = part.outcome
            content = part.model_response_str()
        else:
            outcome = "failed"
            content = part.model_response()
        mark = {"success": "✓", "denied": "⊘", "failed": "✗", "interrupted": "⊘"}.get(outcome, "·")
        self.title = f"{mark} {self.part.tool_name}  {_summary(_args(self.part))}"
        self.remove_class("running")
        self.add_class(outcome)
        self._result.update(_clip(content, 4000))


class ImageView(Widget):
    """An image over the kitty graphics protocol, or a one-line stand-in where it is not available."""

    DEFAULT_CSS = """
    ImageView {
        width: auto;
        height: auto;
        margin: 0 0 0 2;
    }
    """

    def __init__(self, data: bytes, *, enabled: bool, caption: str = "", max_rows: int = 20) -> None:
        super().__init__()
        self.data = data
        self.enabled = enabled
        self.caption = caption
        self.max_rows = max_rows
        self.image: kitty.KittyImage | None = None
        self.png = kitty.to_png(data)
        self.pixels = kitty.png_size(self.png) if self.png else None

    def on_mount(self) -> None:
        if self.enabled and self.png and self.pixels:
            max_cols = max(1, min(100, self.app.size.width - 6))
            cols, rows = kitty.fit(
                *self.pixels, max_cols=max_cols, max_rows=self.max_rows, cell=kitty.cell_size()
            )
            self.image = kitty.KittyImage(self.png, cols, rows)
            _write_terminal(self, self.image.transmit())

    def on_unmount(self) -> None:
        if self.image is not None:
            _write_terminal(self, self.image.delete())

    def render(self) -> Text:
        if self.image is not None:
            return Text("\n").join(self.image.lines())
        size = f"{self.pixels[0]}×{self.pixels[1]}" if self.pixels else f"{len(self.data)} bytes"
        why = "not a PNG" if self.png is None else "no kitty graphics in this terminal"
        name = f"{self.caption} " if self.caption else ""
        return Text(f"[image {name}{size}: {why}]", style="dim")


def _write_terminal(widget: Widget, data: str) -> None:
    """Send escape sequences straight to the terminal, in order with Textual's own output."""
    driver = getattr(widget.app, "_driver", None)  # Textual has no public API for raw output
    if driver is not None:
        driver.write(data)
        driver.flush()


class NoticeMessage(Static):
    def __init__(self, text: str, *, kind: str = "notice") -> None:
        super().__init__(text, classes=kind, markup=False)


def _args(part: ToolCallPart) -> dict[str, Any]:
    try:
        return part.args_as_dict()
    except Exception:
        return {"args": part.args}


def _summary(args: dict[str, Any], limit: int = 90) -> str:
    """`ls -la` for a single string argument, compact JSON otherwise."""
    if isinstance(questions := args.get("questions"), list):  # AskUser
        text = " · ".join(str(q.get("question", "")) for q in questions if isinstance(q, dict))
    elif len(args) == 1 and isinstance(value := next(iter(args.values())), str):
        text = value
    else:
        text = json.dumps(args, ensure_ascii=False)
    text = " ".join(text.split())
    return escape(_clip(text, limit))


def _pretty(args: dict[str, Any]) -> str:
    return json.dumps(args, ensure_ascii=False, indent=2)


def _clip(text: str, limit: int) -> str:
    return text if len(text) <= limit else text[: limit - 1] + "…"
