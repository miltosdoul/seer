"""Small reusable widgets with no alert-domain knowledge."""

from __future__ import annotations

from rich.spinner import Spinner
from textual.app import ComposeResult
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Button, Static


class ScrollableBody(VerticalScroll):
    """VerticalScroll that can take focus, so key bindings work inside it."""

    can_focus = True


class SpinnerWidget(Static):
    """Renders one of rich's named spinners (rich.spinner / cli-spinners).

    rich's Spinner picks its frame from the current time, so all this widget
    does is re-render on an interval; the timer only runs while visible.
    """

    def __init__(self, style: str, **kwargs) -> None:
        super().__init__("", **kwargs)
        self._spinner = Spinner(style)

    def on_mount(self) -> None:
        self.set_interval(1 / 12.5, self._tick)

    def _tick(self) -> None:
        if self.display:
            self.update(self._spinner)


class ConfirmScreen(ModalScreen[bool]):
    """Small modal asking yes/no before an action; dismiss(True/False)."""

    CSS = """
    ConfirmScreen {
        align: center middle;
    }
    #confirm-dialog {
        width: auto;
        height: auto;
        max-width: 70%;
        padding: 1 2;
        border: round $primary;
        background: $panel;
    }
    #confirm-question {
        width: auto;
        margin-bottom: 1;
    }
    #confirm-buttons {
        width: auto;
        height: auto;
        align-horizontal: right;
    }
    #confirm-buttons Button {
        margin-left: 1;
    }
    """

    def __init__(self, question: str) -> None:
        super().__init__()
        self.question = question

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-dialog"):
            yield Static(self.question, id="confirm-question")
            with Horizontal(id="confirm-buttons"):
                yield Button("Yes", id="yes", variant="primary")
                yield Button("No", id="no")

    def on_button_pressed(self, event: Button.Pressed) -> None:
        self.dismiss(event.button.id == "yes")

    def on_key(self, event) -> None:
        if event.key.lower() == "y":
            self.dismiss(True)
        elif event.key in ("n", "N", "escape"):
            self.dismiss(False)
