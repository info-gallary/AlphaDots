# AlphaDots task entry points.
#
# Every target delegates to scripts/tasks.py, which is where the logic lives, so the same
# tasks run on Windows without GNU make:  uv run python -m scripts.tasks <target>
#
# Specification targets not implemented yet (they fail with the milestone that owns them):
#   benchmark (M6), k8s-up / k8s-smoke / k8s-down (M5).

TASK := uv run python -m scripts.tasks
SPEED ?= 0
ARGS ?=

.DEFAULT_GOAL := help
.PHONY: help setup up seed replay smoke ui down dev web web-build test test-integration lint \
        security fixtures snapshot schemas benchmark k8s-up k8s-smoke k8s-down

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

ui:                ## Start the dashboard container on :13000
	$(TASK) ui

down:              ## Stop the platform (ARGS=--volumes also deletes data)
	$(TASK) down -- $(ARGS)

dev:               ## Whole pipeline in one process, no Docker
	$(TASK) dev -- $(ARGS)

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

snapshot:          ## Re-export the dashboard snapshot and parity goldens
	$(TASK) snapshot

schemas:           ## Refresh contract schema snapshots
	$(TASK) schemas

benchmark k8s-up k8s-smoke k8s-down:
	$(TASK) $@
