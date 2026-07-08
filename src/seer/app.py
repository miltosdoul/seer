"""
Prometheus Alert Rule browser -- Textual TUI.

Run with:
    seer
    seer --config path/to/clusters.yaml
    seer --db-dir /var/lib/seer

On startup, shows a list of clusters and lets you pick one to browse.
Selecting a cluster opens its alert table; press "c" from there to return to
the cluster list at any time.

Cluster discovery works one of two ways:

- A YAML config (--config, or ~/.config/seer/config.yaml when it exists; see
  clusters.example.yaml): each cluster's database is its `db_file` if set,
  otherwise `<db-dir>/<cluster-name>.db`.
- No config: glob `<db-dir>/*.db` (default ~/.local/share/seer), one cluster
  per file (name = stem). If no databases exist yet, falls back to grouping
  the bundled alert_rules.json sample data by its own "cluster" field, so
  the picker is usable out of the box.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from textual.app import App, ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import DataTable, Footer, Header, Input, OptionList
from textual.widgets.option_list import Option

from seer.detail_screen import AlertDetailScreen
from seer.explain import gemini_api_key_from_env
from seer.models import SEVERITY_RANK, SEVERITY_STYLE, AlertRule, discover_clusters, load_cluster_rules
from seer.paths import CONFIG_FILE, DATA_DIR
from seer.search import filter_rules
from seer.theme import K9S_THEME

COLUMNS = ("Name", "Namespace", "Severity", "Group")


class ClusterListScreen(Screen):
    """Entry point: pick which cluster's alert rules to browse."""

    BINDINGS = [
        Binding("q", "app.quit", "Quit"),
    ]

    def __init__(self, config_path: str | None, db_dir: Path) -> None:
        super().__init__()
        self.config_path = config_path
        self.db_dir = db_dir

    def compose(self) -> ComposeResult:
        yield Header()
        self.clusters = discover_clusters(self.config_path, self.db_dir)
        yield OptionList(*(Option(name, id=name) for name in self.clusters), id="cluster-list")
        yield Footer()

    def on_mount(self) -> None:
        self.title = "Prometheus Alert Rules"
        self.sub_title = "Select a cluster"
        self.query_one(OptionList).focus()

    def on_option_list_option_selected(self, event: OptionList.OptionSelected) -> None:
        cluster_name = event.option.id
        self.app.push_screen(AlertTableScreen(cluster_name, self.clusters[cluster_name]))


class GroupListScreen(Screen):
    """Browse the cluster's (namespace, group) pairs and pick one to pin as a filter.

    Dismisses with the picked (namespace, group) tuple, or None if cancelled.
    """

    CSS = """
    DataTable {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("escape", "cancel", "Back"),
        Binding("q", "cancel", "Back", show=False),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
    ]

    def __init__(self, rules: list[AlertRule]) -> None:
        super().__init__()
        self.rules = rules
        self.groups: list[tuple[str, str]] = []

    def compose(self) -> ComposeResult:
        yield Header()
        yield DataTable(id="groups", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        counts: dict[tuple[str, str], list[int]] = {}
        for rule in self.rules:
            entry = counts.setdefault((rule.namespace, rule.group), [0, 0])
            entry[0] += 1
            if rule.severity == "critical":
                entry[1] += 1
        self.groups = sorted(counts)

        table = self.query_one(DataTable)
        table.add_columns("Namespace", "Group", "Alerts", "Critical")
        for i, (namespace, group) in enumerate(self.groups):
            total, critical = counts[(namespace, group)]
            critical_cell = f"[{SEVERITY_STYLE['critical']}]{critical}[/]" if critical else "0"
            table.add_row(namespace, group, str(total), critical_cell, key=str(i))
        table.focus()
        self.title = "Alert Groups"
        self.sub_title = f"{len(self.groups)} groups | Enter filters the alert list"

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        self.dismiss(self.groups[int(event.row_key.value)])

    def action_cancel(self) -> None:
        self.dismiss(None)

    def action_cursor_down(self) -> None:
        self.query_one(DataTable).action_cursor_down()

    def action_cursor_up(self) -> None:
        self.query_one(DataTable).action_cursor_up()


class AlertTableScreen(Screen):
    """Table of alert rules for a single cluster, with live text search/filter."""

    CSS = """
    #search {
        dock: top;
        margin: 0 1;
    }
    DataTable {
        margin: 0 1;
    }
    """

    BINDINGS = [
        Binding("/", "focus_search", "Search"),
        Binding("escape", "clear_search", "Clear search"),
        Binding("s", "toggle_search_mode", "Fuzzy/Substr"),
        Binding("ctrl+s", "sort_by_severity", "Sort severity"),
        Binding("ctrl+n", "sort_by_namespace", "Sort namespace"),
        Binding("d", "show_details", "Describe"),
        Binding("g", "show_groups", "Groups"),
        Binding("c", "show_clusters", "Clusters"),
        Binding("q", "app.quit", "Quit"),
        Binding("j", "cursor_down", "Down", show=False),
        Binding("k", "cursor_up", "Up", show=False),
        Binding("ctrl+f", "page_move(1.0)", "Page down", show=False),
        Binding("ctrl+d", "page_move(0.5)", "Half page down", show=False),
        Binding("ctrl+b", "page_move(-1.0)", "Page up", show=False),
        Binding("ctrl+u", "page_move(-0.5)", "Half page up", show=False),
    ]

    def __init__(self, cluster_name: str, db_path: Path | None) -> None:
        super().__init__()
        self.cluster_name = cluster_name
        self.db_path = db_path
        self.all_rules: list[AlertRule] = []
        self.visible_rules: list[AlertRule] = []
        self.search_query: str = ""
        self.fuzzy_search: bool = False
        self.group_filter: tuple[str, str] | None = None  # pinned (namespace, group)
        self.sort_key: str | None = None  # None, "severity", or "namespace"
        self.severity_sort_desc: bool = True
        self.namespace_sort_asc: bool = True

    def compose(self) -> ComposeResult:
        yield Header()
        yield Input(id="search")
        yield DataTable(id="table", cursor_type="row")
        yield Footer()

    def on_mount(self) -> None:
        self.all_rules = load_cluster_rules(self.cluster_name, self.db_path)
        table = self.query_one(DataTable)
        table.add_columns(*COLUMNS)
        self.update_search_placeholder()
        self.refresh_table()
        table.focus()
        self.title = f"Prometheus Alert Rules -- {self.cluster_name}"

    def update_search_placeholder(self) -> None:
        mode = "fuzzy" if self.fuzzy_search else "substring"
        self.query_one("#search", Input).placeholder = (
            f"Search ({mode}) by name, namespace, severity, group... "
            "(/ focus, Esc clear, s toggle match mode)"
        )

    def visible_rules_for_state(self) -> list[AlertRule]:
        rules = self.all_rules
        if self.group_filter is not None:
            namespace, group = self.group_filter
            rules = [r for r in rules if r.namespace == namespace and r.group == group]
        rules = filter_rules(rules, self.search_query, self.fuzzy_search)
        if self.sort_key == "severity":
            rules.sort(
                key=lambda r: SEVERITY_RANK.get(r.severity.lower(), 0),
                reverse=self.severity_sort_desc,
            )
        elif self.sort_key == "namespace":
            rules.sort(key=lambda r: r.namespace.lower(), reverse=not self.namespace_sort_asc)
        return rules

    def refresh_table(self) -> None:
        rules = self.visible_rules_for_state()
        self.visible_rules = rules
        table = self.query_one(DataTable)
        table.clear()
        for rule in rules:
            style = SEVERITY_STYLE.get(rule.severity, "")
            severity_cell = f"[{style}]{rule.severity}[/{style}]" if style else rule.severity
            table.add_row(rule.name, rule.namespace, severity_cell, rule.group, key=rule.id)

        status = [f"{len(rules)} / {len(self.all_rules)} rules"]
        if self.group_filter is not None:
            namespace, group = self.group_filter
            status.insert(0, f"group: {namespace}/{group} (Esc clears)")
        if self.sort_key == "severity":
            status.append(
                f"sorted by severity ({'high-to-low' if self.severity_sort_desc else 'low-to-high'})"
            )
        elif self.sort_key == "namespace":
            status.append(f"sorted by namespace ({'a-z' if self.namespace_sort_asc else 'z-a'})")
        self.sub_title = " | ".join(status)

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id != "search":
            return
        self.search_query = event.value.strip().lower()
        self.refresh_table()

    def on_input_submitted(self, event: Input.Submitted) -> None:
        """Enter commits the search: move focus to the results."""
        if event.input.id != "search":
            return
        self.query_one(DataTable).focus()

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        rule_id = event.row_key.value
        index = next((i for i, r in enumerate(self.visible_rules) if r.id == rule_id), None)
        if index is not None:
            self.app.push_screen(AlertDetailScreen(self.visible_rules, index, self.db_path))

    def action_focus_search(self) -> None:
        self.query_one("#search", Input).focus()

    def action_clear_search(self) -> None:
        """Escape peels one layer at a time: search text, then the group filter."""
        search = self.query_one("#search", Input)
        if self.focused is search and search.value:
            search.value = ""
        elif self.focused is search:
            self.query_one(DataTable).focus()
        elif search.value:
            search.value = ""  # Input.Changed refreshes the table
        elif self.group_filter is not None:
            self.group_filter = None
            self.refresh_table()
        else:
            self.query_one(DataTable).focus()

    def action_toggle_search_mode(self) -> None:
        self.fuzzy_search = not self.fuzzy_search
        self.update_search_placeholder()
        self.refresh_table()

    def action_sort_by_severity(self) -> None:
        if self.sort_key == "severity":
            self.severity_sort_desc = not self.severity_sort_desc
        else:
            self.sort_key = "severity"
            self.severity_sort_desc = True
        self.refresh_table()

    def action_sort_by_namespace(self) -> None:
        if self.sort_key == "namespace":
            self.namespace_sort_asc = not self.namespace_sort_asc
        else:
            self.sort_key = "namespace"
            self.namespace_sort_asc = True
        self.refresh_table()

    def action_show_details(self) -> None:
        table = self.query_one(DataTable)
        if table.row_count == 0:
            return
        row_key, _ = table.coordinate_to_cell_key(table.cursor_coordinate)
        index = next((i for i, r in enumerate(self.visible_rules) if r.id == row_key.value), None)
        if index is None:
            return
        self.app.push_screen(AlertDetailScreen(self.visible_rules, index, self.db_path))

    def action_show_groups(self) -> None:
        def on_pick(picked: tuple[str, str] | None) -> None:
            if picked is not None:
                self.group_filter = picked
                self.refresh_table()

        self.app.push_screen(GroupListScreen(self.all_rules), on_pick)

    def action_show_clusters(self) -> None:
        self.app.pop_screen()

    def action_cursor_down(self) -> None:
        table = self.query_one(DataTable)
        if self.focused is table:
            table.action_cursor_down()

    def action_cursor_up(self) -> None:
        table = self.query_one(DataTable)
        if self.focused is table:
            table.action_cursor_up()

    def action_page_move(self, pages: float) -> None:
        """Move the cursor by a fraction of the visible page (vim-style paging)."""
        table = self.query_one(DataTable)
        if self.focused is not table or table.row_count == 0:
            return
        step = max(1, int(table.scrollable_content_region.height * abs(pages)))
        if pages < 0:
            step = -step
        table.move_cursor(row=max(0, min(table.cursor_row + step, table.row_count - 1)))


class SeerApp(App):
    """Prometheus Alert Rule browser."""

    # k9s-style chrome: aqua block cursor on the tables, steel-blue column
    # headers, orange reserved for the title bar.
    CSS = """
    Header {
        background: $background;
        color: $accent;
        text-style: bold;
    }
    DataTable > .datatable--header {
        background: $background;
        color: $secondary;
        text-style: bold;
    }
    DataTable > .datatable--cursor {
        background: $primary;
        color: $background;
        text-style: bold;
    }
    """

    def __init__(self, config_path: str | None = None, db_dir: Path = DATA_DIR) -> None:
        super().__init__()
        self.config_path = config_path
        self.db_dir = db_dir
        # Read once at startup so a missing key is known up front rather than
        # discovered deep in the describe view when "e" is first pressed.
        self.gemini_api_key = gemini_api_key_from_env()

    def on_mount(self) -> None:
        self.register_theme(K9S_THEME)
        self.theme = "k9s"
        self.push_screen(ClusterListScreen(self.config_path, self.db_dir))


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Prometheus Alert Rule browser.")
    parser.add_argument(
        "--config",
        help=f"Path to a YAML config file (default: {CONFIG_FILE} when it exists; see clusters.example.yaml)",
    )
    parser.add_argument(
        "--db-dir",
        default=str(DATA_DIR),
        help="Directory to scan for <cluster>.db files (used when no config is given, and as the "
        f"default location for clusters that don't set db_file). Defaults to {DATA_DIR}.",
    )
    return parser.parse_args()


def main() -> None:
    args = parse_args()
    config_path = args.config or (str(CONFIG_FILE) if CONFIG_FILE.exists() else None)
    SeerApp(config_path=config_path, db_dir=Path(args.db_dir)).run()


if __name__ == "__main__":
    main()
