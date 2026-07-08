"""
Pull PrometheusRule custom resources from one or more Kubernetes clusters and
mirror them into local SQLite databases -- one file per cluster, named
`<cluster-name>.db`, written to ~/.local/share/seer (the XDG data dir).
seer picks up every *.db file there automatically.

Usage:
    seer-sync                       # uses ~/.config/seer/config.yaml
    seer-sync --config clusters.yaml
    seer-sync --cluster prod=~/.kube/prod-config --cluster staging=~/.kube/staging-config
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from kubernetes import client
from kubernetes import config as kube_config

from seer.config import ClusterConfig, load_clusters
from seer.models import AlertRule, SQLiteAlertStore
from seer.paths import CONFIG_FILE, DATA_DIR

PROMETHEUS_RULE_GROUP = "monitoring.coreos.com"
PROMETHEUS_RULE_VERSION = "v1"
PROMETHEUS_RULE_PLURAL = "prometheusrules"

# Page size for the Kubernetes LIST call. Without a limit, the apiserver tries
# to return every PrometheusRule object in one response, which can blow past
# its default response-size ceiling on clusters with lots of (or large)
# PrometheusRule objects. Paging with the `continue` token keeps each response
# small regardless of cluster size.
LIST_PAGE_SIZE = 100


def db_path_for(cluster: ClusterConfig) -> Path:
    return Path(cluster.db_file) if cluster.db_file else DATA_DIR / f"{cluster.name}.db"


def list_all_prometheus_rules(api: client.CustomObjectsApi) -> list[dict]:
    """Fetch every PrometheusRule object cluster-wide, paging via the continue token."""
    items: list[dict] = []
    continue_token: str | None = None

    while True:
        kwargs = {"limit": LIST_PAGE_SIZE}
        if continue_token:
            kwargs["_continue"] = continue_token

        response = api.list_cluster_custom_object(
            group=PROMETHEUS_RULE_GROUP,
            version=PROMETHEUS_RULE_VERSION,
            plural=PROMETHEUS_RULE_PLURAL,
            **kwargs,
        )
        items.extend(response.get("items", []))

        continue_token = response.get("metadata", {}).get("continue")
        if not continue_token:
            break

    return items


def fetch_alert_rules(cluster: ClusterConfig) -> list[AlertRule]:
    """Load the cluster's kubeconfig and flatten every PrometheusRule CR into AlertRules."""
    api_client = kube_config.new_client_from_config(config_file=cluster.kubeconfig_path)
    api = client.CustomObjectsApi(api_client)

    rules: list[AlertRule] = []
    for item in list_all_prometheus_rules(api):
        namespace = item.get("metadata", {}).get("namespace", "")
        source = item.get("metadata", {}).get("name", "")
        for group in item.get("spec", {}).get("groups", []):
            group_name = group.get("name", "")
            for rule in group.get("rules", []):
                # Recording rules use "record" instead of "alert" -- only alerting
                # rules belong in the browser.
                if "alert" not in rule:
                    continue
                labels = rule.get("labels", {})
                rules.append(
                    AlertRule(
                        name=rule["alert"],
                        cluster=cluster.name,
                        namespace=namespace,
                        group=group_name,
                        severity=labels.get("severity", "info"),
                        expr=rule.get("expr", ""),
                        for_duration=rule.get("for", ""),
                        source=source,
                        labels=labels,
                        annotations=rule.get("annotations", {}),
                    )
                )
    return rules


def sync_cluster(cluster: ClusterConfig) -> int:
    if not cluster.kubeconfig_path:
        raise ValueError(f"Cluster '{cluster.name}' has no kubeconfig_path, cannot sync")
    rules = fetch_alert_rules(cluster)
    db_path = db_path_for(cluster)
    db_path.parent.mkdir(parents=True, exist_ok=True)
    SQLiteAlertStore(db_path).replace_all(rules)
    return len(rules)


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Sync PrometheusRule alerts into per-cluster SQLite databases."
    )
    parser.add_argument("--config", help="Path to a YAML config file (see clusters.example.yaml)")
    parser.add_argument(
        "--cluster",
        action="append",
        default=[],
        metavar="NAME=KUBECONFIG_PATH",
        help="Cluster name and kubeconfig path, e.g. --cluster prod=~/.kube/prod-config. May be repeated.",
    )
    return parser.parse_args()


def resolve_clusters(args: argparse.Namespace) -> list[ClusterConfig]:
    clusters: list[ClusterConfig] = []
    if args.config:
        clusters.extend(load_clusters(args.config))
    elif not args.cluster and CONFIG_FILE.exists():
        clusters.extend(load_clusters(CONFIG_FILE))
    for entry in args.cluster:
        if "=" not in entry:
            print(f"Invalid --cluster value {entry!r}, expected NAME=KUBECONFIG_PATH", file=sys.stderr)
            sys.exit(1)
        name, _, kubeconfig_path = entry.partition("=")
        clusters.append(ClusterConfig(name=name, kubeconfig_path=str(Path(kubeconfig_path).expanduser())))
    return clusters


def main() -> None:
    args = parse_args()
    clusters = resolve_clusters(args)
    if not clusters:
        print(
            f"No clusters given. Use --config/--cluster, or create {CONFIG_FILE}.",
            file=sys.stderr,
        )
        sys.exit(1)

    for cluster in clusters:
        count = sync_cluster(cluster)
        print(f"[{cluster.name}] wrote {count} alert rules to {db_path_for(cluster)}")


if __name__ == "__main__":
    main()
