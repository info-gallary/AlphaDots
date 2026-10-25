# AlphaDots task entry points.
#
# Every target delegates to scripts/tasks.py, which is where the logic lives, so the same
# tasks run on Windows without GNU make:  uv run python -m scripts.tasks <target>
#
# Specification targets not implemented yet (they fail with the milestone that owns them):
#   benchmark (M6).

TASK := uv run python -m scripts.tasks
SPEED ?= 0
ARGS ?=

.DEFAULT_GOAL := help
.PHONY: help setup up seed replay smoke agents agent-smoke ui obs demo down dev dev-agent web web-build \
        test test-integration lint security fixtures research earnings-data earnings-study capture snapshot \
        schemas benchmark k8s-up k8s-smoke k8s-down

help:              ## List tasks
	$(TASK)

setup:             ## Verify toolchain and install dependencies
	$(TASK) setup

up:                ## Start Redpanda, PostgreSQL and the services; waits until healthy
	$(TASK) up

seed:              ## Load the curated seed fixture (idempotent)
	$(TASK) seed

replay:            ## Stream the bulk fixture; pace with SPEED=20000
	$(TASK) replay -- --speed $(SPEED)

smoke:             ## Verify ingest -> score -> signal -> API
	$(TASK) smoke

agents:            ## Start the research agent (Groq if ALPHADOTS_GROQ_API_KEY is set, else mock)
	$(TASK) agents

agent-smoke:       ## Verify research jobs, citations, abstention and monitoring
	$(TASK) agent-smoke

ui:                ## Start the dashboard container on :13000
	$(TASK) ui

obs:               ## Start Prometheus + Grafana on :13001
	$(TASK) obs

demo:              ## Start everything and generate traffic so every dashboard has data
	$(TASK) demo

down:              ## Stop the platform (ARGS=--volumes also deletes data)
	$(TASK) down -- $(ARGS)

dev:               ## Whole pipeline in one process, no Docker
	$(TASK) dev -- $(ARGS)

dev-agent:         ## Research agent against `make dev` (no Docker)
	$(TASK) dev-agent -- $(ARGS)

web:               ## Dashboard dev server on :3000
	$(TASK) web

web-build:         ## Production build of the dashboard, as Vercel runs it
	$(TASK) web-build

test:              ## Fast unit, contract, leakage and e2e tests
	$(TASK) test -- $(ARGS)

test-integration:  ## Tests against throwaway Redpanda and PostgreSQL
	$(TASK) test-integration -- $(ARGS)

lint:              ## Format check, lint, types, schema and fixture drift
	$(TASK) lint

security:          ## Dependency audit and secret scan
	$(TASK) security

fixtures:          ## Regenerate replay fixture and manifest
	$(TASK) fixtures

research:          ## Re-run the simulation study (Backtest page data and docs/research report)
	$(TASK) research

earnings-data:     ## Download SEC filings, prices and factors for the earnings study (cached)
	$(TASK) earnings-data

earnings-study:    ## Run the pre-registered earnings-tone study offline (Earnings page + report)
	$(TASK) earnings-study

capture:           ## Refresh data/real (SEC 8-K/6-K filings) for the dashboard and make demo
	$(TASK) capture

snapshot:          ## Re-export the dashboard snapshot and parity goldens
	$(TASK) snapshot

schemas:           ## Refresh contract schema snapshots
	$(TASK) schemas

k8s-up:            ## Build images, create/reuse a local k3d cluster, helm upgrade --install
	$(TASK) k8s-up

k8s-smoke:         ## Unsuspend the ingestion Job and verify ingest -> score -> signal -> API
	$(TASK) k8s-smoke

k8s-down:          ## helm uninstall and delete the k3d cluster
	$(TASK) k8s-down

benchmark:
	$(TASK) $@
