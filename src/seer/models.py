"""
Data layer for alert rules.

Two stores are available, both exposing the same `load_all()` interface:

- JSONAlertStore: reads the bundled alert_rules.json sample data.
- SQLiteAlertStore: reads (and writes) a per-cluster SQLite database, as
  produced by k8s_sync.py.
"""

from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass, field
from pathlib import Path

from seer.config import load_clusters

SAMPLE_DATA_FILE = Path(__file__).resolve().parent / "alert_rules.json"

SEVERITY_STYLE = {
    "critical": "bold #ff5f5f",
    "error": "#d75f00",
    "warning": "#d7af5f",
    "info": "#5f87d7",
}

SEVERITY_RANK = {
    "critical": 4,
    "error": 3,
    "warning": 2,
    "info": 1,
}


@dataclass
class AlertRule:
    name: str
    cluster: str
    namespace: str
    group: str
    severity: str
    expr: str
    for_duration: str
    # Name of the PrometheusRule object this rule came from. Prometheus allows
    # the same alert name to appear in a same-named group across *different*
    # PrometheusRule objects in one namespace (common when multiple teams or
    # Helm releases each ship their own copy of a standard rule bundle), so
    # this is needed to keep those rules distinct rather than colliding.
    source: str = ""
    labels: dict = field(default_factory=dict)
    annotations: dict = field(default_factory=dict)
    # On-demand, AI-generated (Gemini) plain-language explanation. None until
    # the user requests one from the describe view; persisted once generated.
    explanation: str | None = None

    @property
    def id(self) -> str:
        # Stand-in for a primary key once this is a real DB row.
        return f"{self.cluster}/{self.namespace}/{self.source}/{self.group}/{self.name}"

    @property
    def summary(self) -> str:
        return self.annotations.get("summary", "")

    @property
    def description(self) -> str:
        return self.annotations.get("description", "")

    @classmethod
    def from_dict(cls, d: dict) -> AlertRule:
        return cls(
            name=d["name"],
            cluster=d["cluster"],
            namespace=d.get("namespace", ""),
            group=d.get("group", ""),
            severity=d.get("severity", "info"),
            expr=d.get("expr", ""),
            for_duration=d.get("for", ""),
            source=d.get("source", ""),
            labels=d.get("labels", {}),
            annotations=d.get("annotations", {}),
        )


class JSONAlertStore:
    """Loads alert rules from a JSON file. Swap for SQLiteAlertStore later."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load_all(self) -> list[AlertRule]:
        with open(self.path, encoding="utf-8") as f:
            raw = json.load(f)
        return [AlertRule.from_dict(item) for item in raw]


ALERT_RULES_SCHEMA = """
CREATE TABLE IF NOT EXISTS alert_rules (
    name TEXT NOT NULL,
    cluster TEXT NOT NULL,
    namespace TEXT NOT NULL,
    group_name TEXT NOT NULL,
    severity TEXT NOT NULL,
    expr TEXT NOT NULL,
    for_duration TEXT,
    source TEXT NOT NULL DEFAULT '',
    labels_json TEXT,
    annotations_json TEXT,
    explanation TEXT,
    PRIMARY KEY (cluster, namespace, source, group_name, name)
)
"""


def _ensure_explanation_column(conn: sqlite3.Connection) -> None:
    """Add `explanation` to a table created before this column existed."""
    columns = {row[1] for row in conn.execute("PRAGMA table_info(alert_rules)")}
    if "explanation" not in columns:
        conn.execute("ALTER TABLE alert_rules ADD COLUMN explanation TEXT")


class SQLiteAlertStore:
    """Reads and writes alert rules in a per-cluster SQLite database."""

    def __init__(self, path: str | Path):
        self.path = Path(path)

    def load_all(self) -> list[AlertRule]:
        if not self.path.exists():
            return []
        conn = sqlite3.connect(self.path)
        conn.row_factory = sqlite3.Row
        try:
            rows = conn.execute("SELECT * FROM alert_rules").fetchall()
        except sqlite3.OperationalError:
            rows = []
        finally:
            conn.close()
        return [
            AlertRule(
                name=r["name"],
                cluster=r["cluster"],
                namespace=r["namespace"],
                group=r["group_name"],
                severity=r["severity"],
                expr=r["expr"],
                for_duration=r["for_duration"] or "",
                source=r["source"] or "",
                labels=json.loads(r["labels_json"] or "{}"),
                annotations=json.loads(r["annotations_json"] or "{}"),
                # sqlite3.Row iterates *values*, so `in r` would be wrong here.
                explanation=r["explanation"] if "explanation" in r.keys() else None,  # noqa: SIM118
            )
            for r in rows
        ]

    def replace_all(self, rules: list[AlertRule]) -> None:
        """Overwrite the table contents with `rules`. Used by k8s_sync.py.

        Carries forward any previously-generated `explanation` for rules whose
        primary key is unchanged, so a re-sync doesn't throw away explanations
        generated interactively in the app.
        """
        conn = sqlite3.connect(self.path)
        try:
            conn.execute(ALERT_RULES_SCHEMA)
            _ensure_explanation_column(conn)
            try:
                existing_explanations = dict(
                    conn.execute(
                        "SELECT cluster || '\x1f' || namespace || '\x1f' || source || '\x1f' "
                        "|| group_name || '\x1f' || name, explanation FROM alert_rules "
                        "WHERE explanation IS NOT NULL"
                    ).fetchall()
                )
            except sqlite3.OperationalError:
                existing_explanations = {}

            conn.execute("DELETE FROM alert_rules")
            conn.executemany(
                """
                INSERT OR REPLACE INTO alert_rules
                    (name, cluster, namespace, group_name, severity, expr,
                     for_duration, source, labels_json, annotations_json, explanation)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                [
                    (
                        r.name,
                        r.cluster,
                        r.namespace,
                        r.group,
                        r.severity,
                        r.expr,
                        r.for_duration,
                        r.source,
                        json.dumps(r.labels),
                        json.dumps(r.annotations),
                        existing_explanations.get(
                            f"{r.cluster}\x1f{r.namespace}\x1f{r.source}\x1f{r.group}\x1f{r.name}"
                        ),
                    )
                    for r in rules
                ],
            )
            conn.commit()
        finally:
            conn.close()

    def save_explanation(self, rule: AlertRule, explanation: str) -> None:
        """Persist a generated explanation for one rule, by its primary key."""
        conn = sqlite3.connect(self.path)
        try:
            _ensure_explanation_column(conn)
            conn.execute(
                "UPDATE alert_rules SET explanation = ? "
                "WHERE cluster = ? AND namespace = ? AND source = ? AND group_name = ? AND name = ?",
                (explanation, rule.cluster, rule.namespace, rule.source, rule.group, rule.name),
            )
            conn.commit()
        finally:
            conn.close()


def discover_clusters(config_path: str | None, db_dir: Path) -> dict[str, Path | None]:
    """Return {cluster_name: db_path}. db_path is None for the JSON sample fallback."""
    if config_path:
        clusters = load_clusters(config_path)
        return {
            cluster.name: Path(cluster.db_file) if cluster.db_file else db_dir / f"{cluster.name}.db"
            for cluster in clusters
        }

    db_files = sorted(db_dir.glob("*.db"))
    if db_files:
        return {db_file.stem: db_file for db_file in db_files}

    # Nothing synced yet -- group the bundled sample data by its own "cluster"
    # field so the picker still has something meaningful to show.
    sample_rules = JSONAlertStore(SAMPLE_DATA_FILE).load_all()
    return {name: None for name in sorted({r.cluster for r in sample_rules})}


def load_cluster_rules(cluster_name: str, db_path: Path | None) -> list[AlertRule]:
    if db_path is not None:
        return SQLiteAlertStore(db_path).load_all()
    return [r for r in JSONAlertStore(SAMPLE_DATA_FILE).load_all() if r.cluster == cluster_name]
