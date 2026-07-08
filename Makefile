# seer's XDG locations (mirrors src/seer/paths.py).
CONFIG_DIR := $(or $(XDG_CONFIG_HOME),$(HOME)/.config)/seer
DATA_DIR := $(or $(XDG_DATA_HOME),$(HOME)/.local/share)/seer
CONFIG ?= $(CONFIG_DIR)/config.yaml
# Database(s) the db helper targets operate on. Defaults to every synced
# cluster database; narrow to one cluster with e.g.:
#   make criticals DBS=$(DATA_DIR)/prod-us-east-1.db
DBS ?= $(wildcard $(DATA_DIR)/*.db)

.PHONY: run sync install fmt lint check clean help criticals severities explained unexplained clear-explanations

run:
	uv run seer

sync:
	uv run seer-sync --config $(CONFIG)

install:
	uv tool install --force .

fmt:
	uvx ruff format .

lint:
	uvx ruff check .

check:
	uvx ruff format --check .
	uvx ruff check .

clean:
	rm -f $(DATA_DIR)/*.db

help:
	@echo "run                 start the TUI"
	@echo "sync                pull PrometheusRules into per-cluster .db files (CONFIG=$(CONFIG))"
	@echo "install             install the seer/seer-sync commands via 'uv tool install'"
	@echo "fmt                 format with ruff"
	@echo "lint                lint with ruff"
	@echo "check               fmt --check + lint (what CI should run)"
	@echo "clean               delete all synced .db files in $(DATA_DIR)"
	@echo "criticals           list all critical alerts               [DBS=...]"
	@echo "severities          alert counts per severity              [DBS=...]"
	@echo "explained           list alerts that have an AI explanation [DBS=...]"
	@echo "unexplained         list alerts without an AI explanation  [DBS=...]"
	@echo "clear-explanations  remove all saved AI explanations       [DBS=...]"

# --- Database helpers (need sqlite3 on PATH; operate on $(DBS)) ---

criticals:
	@[ -n "$(DBS)" ] || { echo "No .db files found -- run 'make sync' first."; exit 1; }
	@for db in $(DBS); do \
		echo "== $$db"; \
		sqlite3 -header -column "$$db" \
			"SELECT name, namespace, group_name, for_duration \
			 FROM alert_rules WHERE severity = 'critical' \
			 ORDER BY namespace, name;"; \
	done

severities:
	@[ -n "$(DBS)" ] || { echo "No .db files found -- run 'make sync' first."; exit 1; }
	@for db in $(DBS); do \
		echo "== $$db"; \
		sqlite3 -header -column "$$db" \
			"SELECT severity, COUNT(*) AS count FROM alert_rules \
			 GROUP BY severity ORDER BY count DESC;"; \
	done

explained:
	@[ -n "$(DBS)" ] || { echo "No .db files found -- run 'make sync' first."; exit 1; }
	@for db in $(DBS); do \
		echo "== $$db"; \
		sqlite3 -header -column "$$db" \
			"SELECT name, namespace, \
			        substr(replace(explanation, char(10), ' '), 1, 60) AS explanation_preview \
			 FROM alert_rules WHERE explanation IS NOT NULL \
			 ORDER BY namespace, name;"; \
	done

unexplained:
	@[ -n "$(DBS)" ] || { echo "No .db files found -- run 'make sync' first."; exit 1; }
	@for db in $(DBS); do \
		echo "== $$db"; \
		sqlite3 -header -column "$$db" \
			"SELECT name, namespace, severity FROM alert_rules \
			 WHERE explanation IS NULL ORDER BY namespace, name;"; \
	done

clear-explanations:
	@[ -n "$(DBS)" ] || { echo "No .db files found -- run 'make sync' first."; exit 1; }
	@for db in $(DBS); do \
		count=$$(sqlite3 "$$db" \
			"UPDATE alert_rules SET explanation = NULL WHERE explanation IS NOT NULL; \
			 SELECT changes();"); \
		echo "$$db: cleared $$count explanation(s)"; \
	done
