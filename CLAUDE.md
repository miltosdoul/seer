# seer — Prometheus Alert Rule browser (Textual TUI)

k9s-style TUI for browsing Prometheus alerting rules synced from Kubernetes.
uv project (hatchling, src layout), two console scripts: `seer` (TUI) and
`seer-sync` (sync job).

## Running & installing

```
uv run seer / uv run seer-sync
uv tool install --force .          # = make install
make run / sync / install / fmt / lint / check / clean / help
make criticals / severities / explained / unexplained / clear-explanations
```

`uv tool install` copies a built wheel into `~/.local/share/uv/tools/seer`;
nothing references the source tree afterwards.

## On-disk locations (XDG, `src/seer/paths.py`)

- Config: `~/.config/seer/config.yaml` (`XDG_CONFIG_HOME` respected);
  `--config` overrides. Schema: see `clusters.example.yaml`.
- Databases: `~/.local/share/seer/<cluster>.db` (`XDG_DATA_HOME`);
  `--db-dir` overrides.
- No config + no DBs -> bundled `alert_rules.json` sample data (grouped by
  its `cluster` field).
- AI explanations need `GEMINI_API_KEY`/`GOOGLE_API_KEY` (checked once at
  startup).

## Scope

- Read-mostly browser: clusters -> alert table -> detail ("describe") view.
- Source: `PrometheusRule` CRs (monitoring.coreos.com/v1), alerting rules
  only (recording rules skipped at sync).
- One SQLite file per cluster, written by `seer-sync`; the app only ever
  writes the AI explanation column.
- Gemini explanations: on-demand, confirm-gated, streamed, persisted.

## Layout (separation of concerns is deliberate — keep it)

| File | Responsibility |
|---|---|
| `src/seer/app.py` | `main()`, `SeerApp`, `ClusterListScreen`, `GroupListScreen`, `AlertTableScreen`, arg parsing. No search logic, no data-store code. |
| `src/seer/detail_screen.py` | `AlertDetailScreen` (describe) + `AlertSwitcherScreen` (quick switcher). |
| `src/seer/models.py` | `AlertRule`, `SEVERITY_STYLE`/`SEVERITY_RANK`, `SQLiteAlertStore`/`JSONAlertStore`, schema, `discover_clusters()`, `load_cluster_rules()`. |
| `src/seer/search.py` | All query matching: `filter_rules()` (table), `rank_rules_by_name()` (switcher). Wraps `textual.fuzzy.FuzzySearch`. |
| `src/seer/widgets.py` | Generic widgets: `ScrollableBody`, `SpinnerWidget` (rich Spinner), `ConfirmScreen`. |
| `src/seer/theme.py` | `K9S_THEME` + `KEY_STYLE`/`SUBKEY_STYLE`/`VALUE_STYLE`. |
| `src/seer/explain.py` | Gemini streaming, system prompt, API-key lookup. |
| `src/seer/config.py` | YAML cluster config loader. |
| `src/seer/paths.py` | XDG `CONFIG_FILE`/`DATA_DIR`. |
| `src/seer/k8s_sync.py` | `seer-sync` (paged LIST via continue token, `LIST_PAGE_SIZE=100`). |
| `pyproject.toml` | Metadata, exact-pinned deps, `[project.scripts]`, ruff config (line-length 110; E/F/W/I/B/UP/SIM). |
| `Makefile` | Helpers + sqlite3 report queries over `$(DBS)` in the XDG data dir. |

Import direction: `app.py` -> `detail_screen.py` -> (`search`, `models`,
`widgets`, `theme`, `explain`); `models` -> `config`. Absolute imports
(`from seer.x import ...`). Never import from `seer.app` elsewhere
(circular import; fixed once by extracting `search.py`/`widgets.py`).

## Dependencies

Exact pins (application). **textual pinned to 8.2.5** — gotchas below
verified against it; re-verify before bumping. `kubernetes==36.0.0` — 28.x
capped `urllib3<2`, pulling 10 known CVEs; `uv audit`/Trivy fail on that.

## CI (`.github/workflows/`)

- `ci.yml` — push to main, PRs, weekly cron. Jobs: lint (`uvx ruff format
  --check` + `check`), audit (`uv lock --check`, `uv audit`, `uv sync` with
  `UV_MALWARE_CHECK=1` — OSV MAL advisories abort the sync; both are uv
  preview features), smoke (`uv run seer --help` / `seer-sync --help`).
- `docker.yml` — push to main and `v*` tags: multi-arch (amd64+arm64)
  buildx push to `ghcr.io/<repo>` via `GITHUB_TOKEN` (tags: branch,
  semver + `latest`, `sha-<commit>`), then Trivy scan of the pushed digest,
  failing on fixable HIGH/CRITICAL. Post-push because multi-arch manifests
  can't be `load`ed. First GHCR push creates the package private.

## Docker

Multi-stage: `ghcr.io/astral-sh/uv:python3.14-bookworm-slim` builder
(`uv sync --frozen --no-editable`, deps layer cached separately) ->
`python:3.14-slim-bookworm` final with the venv copied; no uv shipped.
`XDG_CONFIG_HOME=/config`, `XDG_DATA_HOME=/data`; kubeconfig_path entries
in the mounted config must use container paths. Must run with `-it` and
`-e TERM=$TERM` or the TUI prints raw escape codes. README has commands.

## Data model

- Table `alert_rules`, PK `(cluster, namespace, source, group_name, name)`.
  `source` = PrometheusRule object name (same alert name can repeat in a
  same-named group across objects in one namespace).
- `explanation TEXT` added via `_ensure_explanation_column()` migration.
- `replace_all()` carries forward explanations for unchanged PKs
  (`\x1f`-joined keys) — a re-sync must never wipe explanations.
- `AlertRule.id` = PK joined with `/`; used as DataTable row keys.

## UI structure & key bindings

Screen stack: `ClusterListScreen` -> `AlertTableScreen` ->
`AlertDetailScreen`. `GroupListScreen`, `AlertSwitcherScreen`,
`ConfirmScreen` are pushed with callbacks, dismiss with typed result/None.

- **Alert table**: `/` live search (Enter refocuses table), `s` fuzzy/
  substring, `ctrl+s`/`ctrl+n` sort severity/namespace (repeat flips),
  `d`/Enter describe, `g` groups, `c` clusters, `j`/`k` cursor,
  `ctrl+f`/`ctrl+d`/`ctrl+b`/`ctrl+u` paging, `q` quit. Escape peels one
  layer: search text -> group filter -> refocus.
- **Group filter**: `GroupListScreen` dismisses with `(namespace, group)`,
  stored as `group_filter` — a *prefilter* in `visible_rules_for_state()`,
  so search, sorts, detail j/k rotation, and the switcher inherit scope.
- **Detail view**: `/` in-page search, `n`/`N` match cycling, `j`/`k`
  prev/next alert, `e` explain / `E` regenerate (confirm-gated), `ctrl+y`
  quick switcher (deliberate binding — do not change), `y` copies
  `runbook_url` via OSC 52, `q`/escape back.
- Quick switcher: top-5 fuzzy matches by name, down/up or ctrl+n/ctrl+p
  cycle, Enter jumps.

## Gotchas

- `textual.fuzzy.Matcher.highlight()` returns `textual.content.Content`
  (not rich `Text`) in 8.2.5; no `style` kwarg on its `.append()`. Hence
  `search.py` uses `FuzzySearch.match() -> (score, offsets)` (score > 0 =
  hit) and stylizes rich `Text` itself.
- Textual `Vertical`/`Horizontal` default to `1fr` height — modals need
  explicit `height: auto` or they stretch full page.
- Detail body is built exclusively with `Text.append()`, never markup, so
  PromQL/`{{ $labels.foo }}` can't be misparsed. Keep it that way.
- **Spinner contract**: spinner runs until the SQLite row is *persisted*,
  not just stream end. `_stream_explanation()` keeps state `"streaming"`
  through phases `"generating"` -> `"saving"`, writes via
  `asyncio.to_thread`, then flips `"done"`.
- Explanation worker: `run_worker(..., exclusive=True, group="explain")`;
  j/k/switcher navigation cancels via `_cancel_explain_worker()`.
- Gemini thinking tokens count against `max_output_tokens` — keep headroom
  beyond the ~120-word answer. Prompt is name + expression only.
- `sqlite3.Row` iterates *values*: membership needs `in r.keys()`
  (SIM118 noqa in `models.py`).
- Theme: aqua = interactive/accent, steel blue = chrome, orange = title
  only, warm off-white = values. No ad-hoc colors; use `theme.py`
  constants and `SEVERITY_STYLE`.
- Clipboard: mouse reporting suppresses native drag-select (bypass:
  Option/Fn/Shift-drag by terminal); OSC 52 needs the terminal's
  clipboard permission.

## Known issues / deferred

- Detail-view position indicator ignores the group filter (deferred).
- Groups view is a first cut; discussed refinements: search in the groups
  list, fuzzy group popup like the alert switcher.
