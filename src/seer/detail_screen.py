"""
k9s-style "describe" view for a single alert rule, with incremental search,
next/prev navigation across the list it was opened from, and an on-demand AI
explanation (Gemini) of the alert.

The body is built with rich.text.Text.append(), never Text.from_markup() --
so dynamic content (PromQL expressions like `rate(x[5m])`, or annotation
templates like `{{ $labels.foo }}`) can never be misread as markup tags.
There's nothing to escape because nothing gets parsed.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

from rich.text import Text
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Header, Input, Markdown, OptionList, Static
from textual.widgets.option_list import Option
from textual.worker import Worker

from seer.explain import stream_explanation
from seer.models import SEVERITY_STYLE, AlertRule, SQLiteAlertStore
from seer.search import rank_rules_by_name
from seer.theme import KEY_STYLE, SUBKEY_STYLE, VALUE_STYLE
from seer.widgets import ConfirmScreen, ScrollableBody, SpinnerWidget

LABEL_WIDTH = 14
HIGHLIGHT_STYLE = "black on yellow"


class AlertSwitcherScreen(ModalScreen[int]):
    """Spotlight-style quick switcher: fuzzy-find an alert by name and jump to it.

    Dismisses with the picked alert's index into `rules`, or None if cancelled.
    """

    MAX_RESULTS = 5

    CSS = """
    AlertSwitcherScreen {
        align: center top;
    }
    #switcher {
        width: 60%;
        max-width: 90;
        height: auto;
        margin-top: 4;
        padding: 0 1;
        border: round $primary;
        background: $panel;
    }
    #switcher-input {
        border: none;
        height: 1;
        margin: 0;
    }
    #switcher-list {
        height: auto;
        max-height: 5;
        border: none;
        background: $panel;
    }
    """

    def __init__(self, rules: list[AlertRule]) -> None:
        super().__init__()
        self.rules = rules

    def compose(self) -> ComposeResult:
        with Vertical(id="switcher"):
            yield Input(placeholder="Jump to alert...", id="switcher-input")
            yield OptionList(id="switcher-list")

    def on_mount(self) -> None:
        self.query_one("#switcher-input", Input).focus()
        self.refresh_results("")

    def refresh_results(self, query: str) -> None:
        option_list = self.query_one("#switcher-list", OptionList)
        option_list.clear_options()
        for index, name in rank_rules_by_name(self.rules, query, self.MAX_RESULTS):
            rule = self.rules[index]
            prompt = name
            prompt.append(f"  {rule.namespace}", style="dim")
            prompt.append(f"  {rule.severity}", style=SEVERITY_STYLE.get(rule.severity, "dim"))
            option_list.add_option(Option(prompt, id=str(index)))
        if option_list.option_count:
            option_list.highlighted = 0

    def on_input_changed(self, event: Input.Changed) -> None:
        self.refresh_results(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        option_list = self.query_one("#switcher-list", OptionList)
        if option_list.highlighted is not None:
            option = option_list.get_option_at_index(option_list.highlighted)
            self.dismiss(int(option.id))

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        self.dismiss(int(event.option.id))

    def on_key(self, event) -> None:
        option_list = self.query_one("#switcher-list", OptionList)
        if event.key == "escape":
            event.stop()
            self.dismiss(None)
        elif event.key in ("down", "ctrl+n") and option_list.option_count:
            event.stop()
            option_list.highlighted = ((option_list.highlighted or 0) + 1) % option_list.option_count
        elif event.key in ("up", "ctrl+p") and option_list.option_count:
            event.stop()
            option_list.highlighted = ((option_list.highlighted or 0) - 1) % option_list.option_count


def build_detail_text(rule: AlertRule, query: str = "") -> tuple[Text, list[int]]:
    """Return (styled body, character offsets of each query match)."""
    text = Text()

    def kv(label: str, value: str, value_style: str | None = None) -> None:
        text.append(f"{label:<{LABEL_WIDTH}}", style=KEY_STYLE)
        text.append(f"{value}\n", style=value_style)

    kv("Name:", rule.name)
    kv("Cluster:", rule.cluster)
    kv("Namespace:", rule.namespace)
    kv("Source:", rule.source or "<none>")
    kv("Group:", rule.group)
    kv("Severity:", rule.severity, SEVERITY_STYLE.get(rule.severity))
    kv("For:", rule.for_duration or "<none>")
    text.append("\n")

    text.append("Expression:\n", style=KEY_STYLE)
    text.append(f"  {rule.expr}\n", style=VALUE_STYLE)
    text.append("\n")

    text.append("Labels:\n", style=KEY_STYLE)
    if rule.labels:
        width = max(len(k) for k in rule.labels) + 2
        for k, v in rule.labels.items():
            text.append(f"  {(k + ':'):<{width}}", style=SUBKEY_STYLE)
            text.append(f"{v}\n")
    else:
        text.append("  <none>\n")
    text.append("\n")

    text.append("Annotations:\n", style=KEY_STYLE)
    if rule.annotations:
        for k, v in rule.annotations.items():
            text.append(f"  {k}:\n", style=SUBKEY_STYLE)
            text.append(f"    {v}\n")
    else:
        text.append("  <none>\n")

    matches: list[int] = []
    if query:
        plain = text.plain.lower()
        needle = query.lower()
        pos = 0
        while True:
            idx = plain.find(needle, pos)
            if idx == -1:
                break
            text.stylize(HIGHLIGHT_STYLE, idx, idx + len(needle))
            matches.append(idx)
            pos = idx + len(needle)

    return text, matches


class AlertDetailScreen(Screen):
    """Full detail view for one alert, navigable across `rules`."""

    CSS = """
    #search {
        dock: top;
        margin: 0 1;
    }
    ScrollableBody {
        margin: 0 1;
        height: 1fr;
    }
    #position-indicator {
        height: 1;
        padding: 0 2;
        background: $panel;
        color: $text;
        text-style: bold;
        content-align: right middle;
    }
    #ai-explanation-spinner {
        height: 1;
        display: none;
    }
    """

    BINDINGS = [
        Binding("/", "focus_search", "Search"),
        Binding("escape", "clear_search", "Clear/Back"),
        Binding("q", "app.pop_screen", "Back"),
        Binding("n", "next_match", "Next match", show=False),
        Binding("N", "prev_match", "Prev match", show=False),
        Binding("j", "next_alert", "Next alert"),
        Binding("k", "prev_alert", "Prev alert"),
        Binding("e", "explain", "AI Explain"),
        Binding("E", "explain(True)", "Regenerate", show=False),
        Binding("ctrl+y", "quick_switch", "Jump to alert"),
        Binding("y", "copy_runbook", "Copy runbook"),
    ]

    def __init__(self, rules: list[AlertRule], index: int, db_path: Path | None = None) -> None:
        super().__init__()
        self.rules = rules
        self.index = index
        self.db_path = db_path
        self.search_query = ""
        self.matches: list[int] = []
        self.current_match = -1
        self._text = Text()
        self.explain_state = "idle"  # idle | unavailable | streaming | done | error
        self.explain_phase = ""  # while streaming: "generating" | "saving"
        self.explain_text = ""
        self.explain_error = ""
        self._explain_worker: Worker | None = None

    @property
    def rule(self) -> AlertRule:
        return self.rules[self.index]

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(
            placeholder="Search within this alert... (n/N next/prev match, j/k next/prev alert, e explain)",
            id="search",
        )
        yield Static(id="position-indicator")
        with ScrollableBody(id="detail-body"):
            yield Static(id="detail-content")
            yield Static(id="ai-explanation-status")
            yield SpinnerWidget("dots8Bit", id="ai-explanation-spinner")
            yield Markdown(id="ai-explanation-md")
        yield Footer()

    def on_mount(self) -> None:
        self.query_one(ScrollableBody).focus()
        self.refresh_body()

    def refresh_body(self) -> None:
        rule = self.rule
        self._text, self.matches = build_detail_text(rule, self.search_query)
        self.current_match = 0 if self.matches else -1
        self.query_one("#detail-content", Static).update(self._text)

        self.explain_state = "done" if rule.explanation else "idle"
        self.explain_text = rule.explanation or ""
        self.explain_error = ""
        self.render_explanation_section()

        self.query_one(ScrollableBody).scroll_home(animate=False)
        if self.current_match >= 0:
            self.scroll_to_match()
        self.update_status()

    def render_explanation_section(self) -> None:
        status = Text()
        status.append("\n")
        status.append("AI Explanation (Gemini):", style=KEY_STYLE)
        if self.explain_state == "idle":
            status.append("\n  Press 'e' to generate an explanation.", style="dim italic")
        elif self.explain_state == "unavailable":
            status.append(f"\n  {self.explain_error}", style="dim italic")
        elif self.explain_state == "error":
            status.append(f"\n  Error: {self.explain_error}", style="bold red")
        elif self.explain_state == "streaming":
            label = "Saving..." if self.explain_phase == "saving" else "Generating..."
            status.append(f"  {label}", style="dim italic")
        elif self.explain_state == "done":
            status.append("  (E regenerates)", style="dim")
        self.query_one("#ai-explanation-status", Static).update(status)

        # Spinner runs for the whole request: streaming *and* the DB write.
        spinner = self.query_one("#ai-explanation-spinner", SpinnerWidget)
        spinner.display = self.explain_state == "streaming"

        if self.explain_state == "streaming":
            markdown = self.explain_text + " ▌"  # "still typing" cursor
        elif self.explain_state == "done":
            markdown = self.explain_text
        else:
            markdown = ""
        self.query_one("#ai-explanation-md", Markdown).update(markdown)

    def update_status(self) -> None:
        rule = self.rule
        total_in_ns = sum(1 for r in self.rules if r.namespace == rule.namespace)
        idx_in_ns = sum(1 for r in self.rules[: self.index] if r.namespace == rule.namespace) + 1
        self.title = rule.name
        status = (
            f"{idx_in_ns}/{total_in_ns} alerts in {rule.namespace}"
            f"  |  {self.index + 1}/{len(self.rules)} total"
        )
        if self.matches:
            status += f"  |  match {self.current_match + 1}/{len(self.matches)}"
        self.query_one("#position-indicator", Static).update(status)

    def scroll_to_match(self) -> None:
        if not (0 <= self.current_match < len(self.matches)):
            return
        offset = self.matches[self.current_match]
        line_no = self._text.plain.count("\n", 0, offset)
        self.query_one(ScrollableBody).scroll_to(y=line_no, animate=True)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search":
            return
        self.search_query = event.value.strip()
        self.refresh_body()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter commits the search: move focus back to the body for n/N/j/k."""
        if event.input.id != "search":
            return
        self.query_one(ScrollableBody).focus()

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_clear_search(self) -> None:
        search = self.query_one("#search", Input)
        if self.focused is search and search.value:
            search.value = ""
        elif self.focused is search:
            self.query_one(ScrollableBody).focus()
        else:
            self.app.pop_screen()

    def action_next_match(self) -> None:
        if not self.matches:
            return
        self.current_match = (self.current_match + 1) % len(self.matches)
        self.scroll_to_match()
        self.update_status()

    def action_prev_match(self) -> None:
        if not self.matches:
            return
        self.current_match = (self.current_match - 1) % len(self.matches)
        self.scroll_to_match()
        self.update_status()

    def _cancel_explain_worker(self) -> None:
        if self._explain_worker is not None:
            self._explain_worker.cancel()
            self._explain_worker = None

    def action_next_alert(self) -> None:
        if self.index < len(self.rules) - 1:
            self._cancel_explain_worker()
            self.index += 1
            self.refresh_body()

    def action_prev_alert(self) -> None:
        if self.index > 0:
            self._cancel_explain_worker()
            self.index -= 1
            self.refresh_body()

    def action_copy_runbook(self) -> None:
        url = self.rule.annotations.get("runbook_url", "").strip()
        if not url:
            self.notify("This alert has no runbook_url annotation.", severity="warning")
            return
        self.app.copy_to_clipboard(url)
        self.notify(f"Copied: {url}")

    def action_quick_switch(self) -> None:
        def on_pick(index: int | None) -> None:
            if index is not None and index != self.index:
                self._cancel_explain_worker()
                self.index = index
                self.refresh_body()

        self.app.push_screen(AlertSwitcherScreen(self.rules), on_pick)

    def action_explain(self, force: bool = False) -> None:
        rule = self.rule
        if self.explain_state == "streaming":
            return  # already in progress

        if rule.explanation and not force:
            return  # already generated -- press E to regenerate

        api_key = self.app.gemini_api_key
        if not api_key:
            self.explain_state = "unavailable"
            self.explain_error = "Set GEMINI_API_KEY (or GOOGLE_API_KEY) to use AI explanations."
            self.render_explanation_section()
            return

        def on_confirm(confirmed: bool | None) -> None:
            if confirmed:
                self._explain_worker = self.run_worker(
                    self._stream_explanation(api_key), exclusive=True, group="explain"
                )

        question = (
            f"Regenerate the AI explanation for '{rule.name}'? The saved one will be overwritten."
            if rule.explanation
            else f"Generate an AI explanation for '{rule.name}' using Gemini?"
        )
        self.app.push_screen(ConfirmScreen(question), on_confirm)

    async def _stream_explanation(self, api_key: str) -> None:
        rule = self.rule
        self.explain_state = "streaming"
        self.explain_phase = "generating"
        self.explain_text = ""
        self.render_explanation_section()

        body = self.query_one(ScrollableBody)
        try:
            async for chunk in stream_explanation(rule, api_key):
                self.explain_text += chunk
                self.render_explanation_section()
                body.scroll_end(animate=False)
        except Exception as exc:  # noqa: BLE001 -- surface any provider/network error to the user
            self.explain_state = "error"
            self.explain_error = str(exc)
            self.render_explanation_section()
            return

        if not self.explain_text.strip():
            self.explain_state = "error"
            self.explain_error = "Model returned no text (response may have hit the token limit)."
            self.render_explanation_section()
            return

        # Persist before flipping to "done": the spinner stays up until the
        # row is actually written, not just until the stream ends. The write
        # runs on a thread so the indicator keeps animating meanwhile.
        self.explain_phase = "saving"
        self.render_explanation_section()
        rule.explanation = self.explain_text
        if self.db_path is not None:
            await asyncio.to_thread(SQLiteAlertStore(self.db_path).save_explanation, rule, self.explain_text)

        self.explain_state = "done"
        self.render_explanation_section()
        body.scroll_end(animate=False)
