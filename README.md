# seer

A k9s-style terminal UI for browsing Prometheus alerting rules from your
Kubernetes clusters. Rules are synced from PrometheusRule custom resources
into local SQLite databases, one per cluster, then browsed offline.

## Install

    uv tool install .

This installs two commands: seer (the TUI) and seer-sync (the sync job).

## Setup

Create ~/.config/seer/config.yaml listing your clusters:

    clusters:
      prod:
        kubeconfig_path: ~/.kube/prod.yaml
      staging:
        kubeconfig_path: ~/.kube/staging.yaml

Then pull the alert rules:

    seer-sync

Databases are written to ~/.local/share/seer, one file per cluster.
Re-run seer-sync whenever you want fresh data.

## Use

    seer

Pick a cluster, browse the alert table, press d or Enter on a rule for a
full describe view. Useful keys: / to search, s to toggle fuzzy matching,
g for the groups view, e for an AI explanation, y to copy the runbook URL,
q to go back. The footer shows the rest.

Without any synced databases, seer opens with bundled sample data so you
can try it before configuring anything.

AI explanations are optional and need GEMINI_API_KEY (or GOOGLE_API_KEY)
set in the environment. They are generated on demand, only after you
confirm, and are saved to the database so each one is generated once.

## Docker

The image works without uv or Python on the host:

    docker build -t seer .
    docker run -it --rm -e TERM=$TERM \
        -v seer-data:/data -v ~/.config/seer:/config/seer:ro seer

To sync, mount your kubeconfigs as well. Inside the container the config
is read from /config/seer/config.yaml and its kubeconfig_path entries must
use container paths, for example /kube/prod.yaml:

    docker run -it --rm -v seer-data:/data \
        -v ~/.config/seer:/config/seer:ro -v ~/.kube:/kube:ro seer seer-sync

Databases persist in the seer-data volume. For AI explanations add
-e GEMINI_API_KEY to the run command. The -it and TERM flags matter:
without them the TUI prints raw escape codes instead of rendering.

## Development

    make run      # run from source
    make check    # ruff format check and lint
    make help     # all targets, including sqlite report queries
