"""Modal dialogs for requests that wait for the user: tool approvals and AskUser questions."""

import json

from rich.markup import escape
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, RadioButton, RadioSet, SelectionList, Static

from tether.api.events import Approval, ApprovalRequested, AskUserAnswer, AskUserResponse, QuestionAsked


class ApprovalDialog(ModalScreen[Approval | None]):
    BINDINGS = [
        Binding("y", "answer('yes')", "allow"),
        Binding("a", "answer('always')", "always"),
        Binding("n,escape", "answer('no')", "deny"),
    ]

    def __init__(self, request: ApprovalRequested) -> None:
        super().__init__(classes="dialog-screen")
        self.request = request

    def compose(self) -> ComposeResult:
        request = self.request
        with Vertical(classes="dialog approval"):
            yield Label(f"Allow [b]{escape(request.tool_name)}[/b]?", classes="dialog-title")
            with VerticalScroll(classes="dialog-body"):
                yield Static(_show_args(request.args), classes="approval-args", markup=False)
            with Horizontal(classes="dialog-buttons"):
                yield Button("Allow  y", id="yes", variant="success")
                yield Button(f"Always allow {request.tool_name}  a", id="always", variant="primary")
                yield Button("Deny  n", id="no", variant="error")

    def on_mount(self) -> None:
        self.query_one("#yes", Button).focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.action_answer(event.button.id or "no")

    def action_answer(self, choice: str) -> None:
        if choice == "no":
            self.dismiss(Approval(False))
        else:
            self.dismiss(Approval(True, always=choice == "always"))


class QuestionDialog(ModalScreen[AskUserResponse | None]):
    BINDINGS = [
        Binding("ctrl+s", "submit", "submit"),
        Binding("escape", "decline", "decline"),
    ]

    def __init__(self, event: QuestionAsked) -> None:
        super().__init__(classes="dialog-screen")
        self.questions = event.request.questions

    def compose(self) -> ComposeResult:
        with Vertical(classes="dialog question"):
            yield Label("The agent asks", classes="dialog-title")
            with VerticalScroll(classes="dialog-body"):
                for i, q in enumerate(self.questions):
                    yield Label(f"[b]{escape(q.header)}[/b]  {escape(q.question)}", classes="question-text")
                    if q.multi_select:
                        yield SelectionList[str](
                            *((_option_text(o.label, o.description), o.label) for o in q.options),
                            id=f"q{i}",
                        )
                    else:
                        with RadioSet(id=f"q{i}"):
                            for o in q.options:
                                yield RadioButton(_option_text(o.label, o.description))
                    yield Input(
                        placeholder="or type your own answer", id=f"custom{i}", classes="custom-answer"
                    )
            with Horizontal(classes="dialog-buttons"):
                yield Button("Answer  ctrl+s", id="submit", variant="success")
                yield Button("Decline  esc", id="decline", variant="error")

    def on_mount(self) -> None:
        self.query_one("#q0").focus()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "submit":
            self.action_submit()
        else:
            self.action_decline()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        self.action_submit()

    def action_decline(self) -> None:
        self.dismiss(AskUserResponse(cancelled=True))

    def action_submit(self) -> None:
        answers = []
        for i, q in enumerate(self.questions):
            custom = self.query_one(f"#custom{i}", Input).value.strip()
            if custom:
                answers.append(AskUserAnswer(header=q.header, custom_answer=custom))
                continue
            widget = self.query_one(f"#q{i}")
            if isinstance(widget, SelectionList):
                selected = tuple(label for label in widget.selected)
            else:
                assert isinstance(widget, RadioSet)
                index = widget.pressed_index
                selected = (q.options[index].label,) if index >= 0 else ()
            if not selected:
                self.notify(f"Answer “{q.header}” or press esc to decline", severity="warning")
                return
            answers.append(AskUserAnswer(header=q.header, selected=selected))
        self.dismiss(AskUserResponse(answers=tuple(answers)))


def _option_text(label: str, description: str | None) -> str:
    text = f"[b]{escape(label)}[/b]"
    return f"{text}  [dim]{escape(description)}[/dim]" if description else text


def _show_args(args: dict) -> str:
    """A shell command as itself, anything else as indented JSON."""
    if isinstance(command := args.get("command"), str):
        rest = {k: v for k, v in args.items() if k != "command"}
        return command + (f"\n\n{json.dumps(rest, ensure_ascii=False)}" if rest else "")
    return json.dumps(args, ensure_ascii=False, indent=2)
