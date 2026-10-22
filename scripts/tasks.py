"""Project tasks, runnable on any OS.

    uv run python -m scripts.tasks            # list tasks
    uv run python -m scripts.tasks up
    uv run python -m scripts.tasks replay -- --speed 20000

The ``Makefile`` is a thin wrapper around this file, so ``make up`` and
``uv run python -m scripts.tasks up`` are the same thing. The logic lives here because
GNU make is not installed by default on Windows and tasks must work there too.

Targets named by the specification that do not exist yet are listed in ``NOT_YET`` with
the milestone that delivers them. They are omitted rather than stubbed.
"""

from __future__ import annotations

import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "-f", "infra/compose/docker-compose.yml"]
COMPOSE_IT = ["docker", "compose", "-f", "infra/compose/docker-compose.test.yml"]
# Both profiles: the job depends on core services, which must be in scope to resolve.
INGEST = [*COMPOSE, "--profile", "core", "--profile", "jobs", "run", "--rm", "ingestion", "replay"]
API_URL = "http://localhost:18000"
GITLEAKS_IMAGE = "zricethezav/gitleaks:v8.30.1"

NOT_YET = {
    "benchmark": "M6: controlled latency/throughput benchmark with recorded machine spec",
    "k8s-up": "M5: k3d cluster + Helm chart",
    "k8s-smoke": "M5",
    "k8s-down": "M5",
}


def run(*argv: str, env: dict[str, str] | None = None, cwd: Path = ROOT) -> None:
    """Run one command, echoing it first; exit with its status if it fails."""
    executable = shutil.which(argv[0])
    if executable is None:
        sys.exit(f"error: `{argv[0]}` is not installed or not on PATH")
    print(f"$ {' '.join(argv)}", flush=True)
    completed = subprocess.run(  # noqa: S603 - argv is built from constants in this file
        [executable, *argv[1:]], cwd=cwd, env={**os.environ, **(env or {})}, check=False
    )
    if completed.returncode != 0:
        sys.exit(completed.returncode)


def setup(_extra: list[str]) -> None:
    """Verify the toolchain and install Python and web dependencies."""
    for tool in ("uv", "docker", "node", "pnpm"):
        location = shutil.which(tool)
        print(f"{tool:<8}{location or 'MISSING'}")
        if location is None:
            sys.exit(f"error: `{tool}` is required")
    run("uv", "sync", "--locked")
    run("pnpm", "install", "--frozen-lockfile", cwd=ROOT / "web")


def up(_extra: list[str]) -> None:
    """Build and start the core platform; returns once every service is healthy."""
    run(*COMPOSE, "--profile", "core", "up", "--detach", "--build", "--wait")


def down(_extra: list[str]) -> None:
    """Stop the platform. Data volumes are kept; add `-- --volumes` to delete them."""
    run(*COMPOSE, "--profile", "core", "--profile", "jobs", "--profile", "ui", "down", *_extra)


def seed(_extra: list[str]) -> None:
    """Load the curated seed fixture (idempotent: safe to repeat)."""
    run(*INGEST, "--fixture", "news/seed.jsonl")


def replay(extra: list[str]) -> None:
    """Stream the bulk fixture. Pace it with `-- --speed 20000` for a live tape."""
    run(*INGEST, "--fixture", "news/replay.jsonl", *extra)


def smoke(extra: list[str]) -> None:
    """Verify ingest -> score -> signal -> API against the running platform."""
    seed([])
    run("uv", "run", "python", "-m", "scripts.smoke", "--base-url", API_URL, *extra)


def ui(_extra: list[str]) -> None:
    """Build and start the dashboard container on http://localhost:13000."""
    run(*COMPOSE, "--profile", "core", "--profile", "ui", "up", "--detach", "--build", "--wait")


def dev(extra: list[str]) -> None:
    """Run the whole pipeline in one process with no Docker (API on :18000)."""
    run("uv", "run", "python", "-m", "scripts.local_stack", "--serve", *extra)


def web(_extra: list[str]) -> None:
    """Run the dashboard dev server on http://localhost:3000."""
    run("pnpm", "dev", cwd=ROOT / "web")


def test(extra: list[str]) -> None:
    """Fast unit, contract, leakage, e2e and dashboard parity tests. No Docker needed."""
    run("uv", "run", "pytest", "-m", "not integration", *extra)
    run("pnpm", "test", cwd=ROOT / "web")


def test_integration(extra: list[str]) -> None:
    """Run integration tests against throwaway Redpanda and PostgreSQL containers."""
    run(*COMPOSE_IT, "up", "--detach", "--wait")
    try:
        run("uv", "run", "pytest", "-m", "integration", *extra)
    finally:
        # Always torn down, with its volumes, whether the tests passed or not.
        subprocess.run([*COMPOSE_IT, "down", "--volumes"], cwd=ROOT, check=False)  # noqa: S603


def lint(_extra: list[str]) -> None:
    """Formatting, lint, strict types, schema snapshots and fixture manifest."""
    run("uv", "run", "ruff", "format", "--check", ".")
    run("uv", "run", "ruff", "check", ".")
    run("uv", "run", "mypy")
    run("uv", "run", "python", "-m", "scripts.export_schemas", "--check")
    run("uv", "run", "python", "-m", "scripts.generate_fixtures", "--check")
    run("uv", "run", "python", "-m", "scripts.export_snapshot", "--check")
    run("pnpm", "lint", cwd=ROOT / "web")
    run("pnpm", "typecheck", cwd=ROOT / "web")


def security(_extra: list[str]) -> None:
    """Dependency audit and secret scan. Semgrep, Trivy, Checkov and SBOM arrive with M5."""
    run("uv", "run", "pip-audit", "--skip-editable")
    run("pnpm", "audit", "--prod", cwd=ROOT / "web")
    run(
        "docker", "run", "--rm", "--volume", f"{ROOT}:/repo:ro", GITLEAKS_IMAGE,
        "dir", "/repo", "--config", "/repo/.gitleaks.toml", "--redact", "--no-banner",
    )  # fmt: skip


def fixtures(_extra: list[str]) -> None:
    """Regenerate the bulk replay fixture and the fixture manifest."""
    run("uv", "run", "python", "-m", "scripts.generate_fixtures")


def snapshot(_extra: list[str]) -> None:
    """Re-export the dashboard's read-model snapshot and its parity goldens."""
    run("uv", "run", "python", "-m", "scripts.export_snapshot")


def web_build(_extra: list[str]) -> None:
    """Production build of the dashboard, exactly as Vercel runs it."""
    run("pnpm", "install", "--frozen-lockfile", cwd=ROOT / "web")
    run("pnpm", "build", cwd=ROOT / "web")


def schemas(_extra: list[str]) -> None:
    """Refresh contract schema snapshots (refuses breaking changes)."""
    run("uv", "run", "python", "-m", "scripts.export_schemas")


TASKS: dict[str, Callable[[list[str]], None]] = {
    "setup": setup,
    "up": up,
    "seed": seed,
    "replay": replay,
    "smoke": smoke,
    "ui": ui,
    "down": down,
    "dev": dev,
    "web": web,
    "test": test,
    "test-integration": test_integration,
    "lint": lint,
    "security": security,
    "fixtures": fixtures,
    "snapshot": snapshot,
    "web-build": web_build,
    "schemas": schemas,
}


def main() -> None:
    arguments = sys.argv[1:]
    if not arguments or arguments[0] in ("-h", "--help", "help"):
        print("Tasks:")
        for name, function in TASKS.items():
            print(f"  {name:<18}{(function.__doc__ or '').strip().splitlines()[0]}")
        print("\nNot implemented yet:")
        for name, reason in NOT_YET.items():
            print(f"  {name:<18}{reason}")
        return
    name, rest = arguments[0], arguments[1:]
    if name in NOT_YET:
        sys.exit(f"`{name}` is not implemented yet ({NOT_YET[name]})")
    if name not in TASKS:
        sys.exit(f"unknown task `{name}`; run with no arguments to list tasks")
    TASKS[name](rest[1:] if rest[:1] == ["--"] else rest)


if __name__ == "__main__":
    main()
