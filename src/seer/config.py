"""
YAML config loader for cluster definitions.

Expected schema:

    clusters:
      prod-us-east-1:
        kubeconfig_path: ~/.kube/prod-us-east-1.yaml
      staging:
        kubeconfig_path: ~/.kube/staging.yaml
        db_file: /var/lib/seer/staging.db   # optional, overrides the default <name>.db path

The default config location is ~/.config/seer/config.yaml (XDG_CONFIG_HOME
respected); both `seer` and `seer-sync` read it automatically when it exists.

`db_file` is optional -- when omitted, seer-sync writes to (and seer reads
from) `<cluster-name>.db` in ~/.local/share/seer (or --db-dir). A cluster
only needs `kubeconfig_path` if you intend to sync it with seer-sync, and
only needs `db_file` if you intend to browse it with a non-default database
location.

See clusters.example.yaml for a full example.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path


@dataclass
class ClusterConfig:
    name: str
    kubeconfig_path: str | None = None
    db_file: str | None = None


def load_clusters(path: str | Path) -> list[ClusterConfig]:
    import yaml

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f) or {}

    clusters = raw.get("clusters") or {}
    if not clusters:
        raise ValueError(f"No clusters defined in {path}")

    result = []
    for name, settings in clusters.items():
        settings = settings or {}
        kubeconfig_path = settings.get("kubeconfig_path")
        db_file = settings.get("db_file")
        if not kubeconfig_path and not db_file:
            raise ValueError(f"Cluster '{name}' needs at least one of kubeconfig_path or db_file")
        result.append(
            ClusterConfig(
                name=name,
                kubeconfig_path=str(Path(kubeconfig_path).expanduser()) if kubeconfig_path else None,
                db_file=str(Path(db_file).expanduser()) if db_file else None,
            )
        )
    return result
