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
import socket
import subprocess
import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
COMPOSE = ["docker", "compose", "-f", "infra/compose/docker-compose.yml"]
COMPOSE_IT = ["docker", "compose", "-f", "infra/compose/docker-compose.test.yml"]
# Both profiles: the job depends on core services, which must be in scope to resolve.
INGEST = [*COMPOSE, "--profile", "core", "--profile", "jobs", "run", "--rm", "ingestion", "replay"]
API_URL = "http://localhost:18000"
# Docker Hub, not ghcr.io: ghcr.io blob pulls are unreachable from this dev machine
# (see memory `dev-environment-quirks`). Versions pinned to what was actually pulled and
# run, not guessed.
GITLEAKS_IMAGE = "zricethezav/gitleaks:v8.30.1"
TRIVY_IMAGE = "aquasec/trivy:0.76.1"
SEMGREP_IMAGE = "semgrep/semgrep:1.181.1"
CHECKOV_IMAGE = "bridgecrew/checkov:3.3.30"
SECURITY_OUT = "var/security"

NOT_YET = {
    "benchmark": "M6: controlled latency/throughput benchmark with recorded machine spec",
}

K3D_CLUSTER = "alphadots-dev"
HELM_RELEASE = "alphadots"
HELM_CHART = "infra/helm/alphadots"
K3D_CONFIG = "infra/k3d/cluster-config.yaml"
# service -> (SERVICE build-arg, MODULE build-arg); matches infra/docker/service.Dockerfile
# and the image repo names infra/helm/alphadots/values.yaml and the `ecr` Terraform
# module both expect ("alphadots/<service>").
K8S_PYTHON_SERVICES = {
    "ingestion": "alphadots_ingestion",
    "preprocessor": "alphadots_preprocessor",
    "sentiment": "alphadots_sentiment",
    "signal-engine": "alphadots_signal_engine",
    "api": "alphadots_api",
    "research-agent": "alphadots_research_agent",
}
K8S_API_URL = "http://localhost:30080"


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
    run(
        *COMPOSE,
        "--profile",
        "core",
        "--profile",
        "jobs",
        "--profile",
        "agents",
        "--profile",
        "ui",
        "--profile",
        "obs",
        "down",
        *_extra,
    )


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


def agents(_extra: list[str]) -> None:
    """Build and start the research agent (Groq if ALPHADOTS_GROQ_API_KEY is set in .env,
    else the deterministic mock)."""
    run(*COMPOSE, "--profile", "core", "--profile", "agents", "up", "--detach", "--build", "--wait")


def agent_smoke(extra: list[str]) -> None:
    """Verify the research agent: job creation, idempotency, citations, abstention, monitoring."""
    run("uv", "run", "python", "-m", "scripts.agent_smoke", "--base-url", API_URL, *extra)


def ui(_extra: list[str]) -> None:
    """Build and start the dashboard container on http://localhost:13000."""
    run(*COMPOSE, "--profile", "core", "--profile", "ui", "up", "--detach", "--build", "--wait")


def obs(_extra: list[str]) -> None:
    """Build and start Prometheus + Grafana (http://localhost:13001, admin/admin by default)."""
    run(*COMPOSE, "--profile", "core", "--profile", "obs", "up", "--detach", "--build", "--wait")


def _k3d_cluster_exists() -> bool:
    executable = shutil.which("k3d")
    if executable is None:
        return False
    result = subprocess.run(  # noqa: S603 - fixed argv, resolved via shutil.which
        [executable, "cluster", "list", K3D_CLUSTER, "--no-headers"],
        cwd=ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    return result.returncode == 0 and K3D_CLUSTER in result.stdout


def _k3d_import_image(image: str) -> None:
    """Load `image` into the cluster's containerd directly, bypassing `k3d image import`.

    That command pulls `ghcr.io/k3d-io/k3d-tools` to do the loading, and ghcr.io blob
    pulls are unreachable from this dev machine (see memory `dev-environment-quirks`).
    `docker save` + `docker cp` + `ctr images import` reaches the same end state without
    needing that image at all.
    """
    node = f"k3d-{K3D_CLUSTER}-server-0"
    safe_name = image.replace("/", "_").replace(":", "_")
    remote_tar = f"/tmp/{safe_name}.tar"  # noqa: S108 - path inside the k3d node container, not this host
    with tempfile.TemporaryDirectory() as tmp:
        tar_path = Path(tmp) / f"{safe_name}.tar"
        run("docker", "save", image, "-o", str(tar_path))
        run("docker", "cp", str(tar_path), f"{node}:{remote_tar}")
    run("docker", "exec", node, "ctr", "-n", "k8s.io", "images", "import", remote_tar)
    run("docker", "exec", node, "rm", remote_tar)


def k8s_up(_extra: list[str]) -> None:
    """Build service images, create/reuse a local k3d cluster, and `helm upgrade --install`."""
    for service, module in K8S_PYTHON_SERVICES.items():
        run(
            "docker", "build", "-f", "infra/docker/service.Dockerfile",
            "--build-arg", f"SERVICE={service}", "--build-arg", f"MODULE={module}",
            "-t", f"alphadots/{service}:dev", ".",
        )  # fmt: skip
    run("docker", "build", "-t", "alphadots/web:dev", ".", cwd=ROOT / "web")
    if _k3d_cluster_exists():
        print(f"k3d cluster `{K3D_CLUSTER}` already exists; reusing it.")
    else:
        fixtures_path = str(ROOT / "data" / "fixtures").replace("\\", "/")
        run(
            "k3d", "cluster", "create", "--config", K3D_CONFIG,
            "--volume", f"{fixtures_path}:/fixtures@server:0",
            # No loadbalancer: it needs `ghcr.io/k3d-io/k3d-proxy`, unreachable from this
            # dev machine (see infra/k3d/cluster-config.yaml's header comment for how
            # that was confirmed). `k8s_smoke` below reaches the cluster via
            # `kubectl port-forward` instead of a published host port.
            "--no-lb",
            "--timeout", "90s",
        )  # fmt: skip
    images = [f"alphadots/{service}:dev" for service in K8S_PYTHON_SERVICES] + ["alphadots/web:dev"]
    for image in images:
        _k3d_import_image(image)
    run("helm", "upgrade", "--install", HELM_RELEASE, HELM_CHART, "--wait", "--timeout", "5m")


def _wait_for_port(port: int, *, deadline: float) -> None:
    end = time.monotonic() + deadline
    while time.monotonic() < end:
        try:
            with socket.create_connection(("localhost", port), timeout=1.0):
                return
        except OSError:
            time.sleep(0.5)
    sys.exit(f"error: nothing listening on localhost:{port} after {deadline}s")


def k8s_smoke(extra: list[str]) -> None:
    """Unsuspend the ingestion Job, wait for it, then verify ingest -> score -> signal -> API.

    Reached via `kubectl port-forward`, not a published host port: this cluster has no
    load balancer (see infra/k3d/cluster-config.yaml).
    """
    job = f"{HELM_RELEASE}-ingestion-replay"
    run("kubectl", "patch", "job", job, "--type", "merge", "-p", '{"spec":{"suspend":false}}')
    run("kubectl", "wait", f"job/{job}", "--for=condition=complete", "--timeout=180s")
    kubectl = shutil.which("kubectl")
    if kubectl is None:
        sys.exit("error: `kubectl` is not installed or not on PATH")
    port = K8S_API_URL.rsplit(":", 1)[-1]
    forward = subprocess.Popen(  # noqa: S603 - fixed argv, resolved via shutil.which
        [kubectl, "port-forward", f"svc/{HELM_RELEASE}-api", f"{port}:8000"], cwd=ROOT
    )
    try:
        _wait_for_port(int(port), deadline=30.0)
        run("uv", "run", "python", "-m", "scripts.smoke", "--base-url", K8S_API_URL, *extra)
    finally:
        forward.terminate()
        forward.wait(timeout=10)


def k8s_down(_extra: list[str]) -> None:
    """Uninstall the Helm release and delete the k3d cluster."""
    for tool, argv in (
        ("helm", ["uninstall", HELM_RELEASE]),
        ("k3d", ["cluster", "delete", K3D_CLUSTER]),
    ):
        executable = shutil.which(tool)
        if executable is not None:
            subprocess.run([executable, *argv], cwd=ROOT, check=False)  # noqa: S603 - resolved via shutil.which


def dev(extra: list[str]) -> None:
    """Run the whole pipeline in one process with no Docker (API on :18000)."""
    run("uv", "run", "python", "-m", "scripts.local_stack", "--serve", *extra)


def dev_agent(extra: list[str]) -> None:
    """Research agent against `make dev`: its own SQLite file, no Kafka, API on :18000.

    Job creation, polling, citations and monitoring all work this way. The one thing
    that does not: a completed report never reaches `GET /v1/research/{symbol}`, since
    that read model is filled by the Kafka round-trip `make dev` does not have — use
    `make up` + `make agents` for that.
    """
    env = {
        "ALPHADOTS_BUS_BACKEND": "memory",
        "ALPHADOTS_DATABASE_URL": "sqlite+aiosqlite:///var/research_agent_dev.sqlite3",
        "ALPHADOTS_API_URL": "http://localhost:18000",
    }
    run("uv", "run", "python", "-m", "alphadots_research_agent", *extra, env=env)


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
    """Dependency audit, secret scan, SAST, image/fs scan, IaC scan, and SBOM export.

    pip-audit, pnpm audit, Gitleaks, Semgrep and Trivy all gate: a real finding stops
    this task with a non-zero exit, the block CLAUDE.md S11 asks for. A deliberate,
    scoped exception belongs in `.trivyignore` (or a `.semgrepignore`), each entry with
    a comment saying why and until when - never a blanket suppression. Checkov runs and
    reports but does not gate yet; see the comment at its call site for why.
    """
    (ROOT / SECURITY_OUT).mkdir(parents=True, exist_ok=True)
    run("uv", "run", "pip-audit", "--skip-editable")
    run(
        "uv", "run", "pip-audit", "--skip-editable", "--format=cyclonedx-json",
        "--output", f"{SECURITY_OUT}/python-sbom.cdx.json",
    )  # fmt: skip
    run("pnpm", "audit", "--prod", cwd=ROOT / "web")
    run(
        "docker", "run", "--rm", "--volume", f"{ROOT}:/repo:ro", GITLEAKS_IMAGE,
        "dir", "/repo", "--config", "/repo/.gitleaks.toml", "--redact", "--no-banner",
    )  # fmt: skip
    run(
        "docker", "run", "--rm", "--volume", f"{ROOT}:/src:ro", SEMGREP_IMAGE,
        "semgrep", "scan", "--config=p/ci", "--error", "--metrics=off",
        "--exclude=.venv", "--exclude=node_modules", "--exclude=.next", "/src",
    )  # fmt: skip
    trivy_cache = ["--volume", "alphadots-trivy-cache:/root/.cache/trivy"]
    # No whole-repo `trivy fs --scanners vuln` here: dependency vulnerabilities are
    # already fully covered above by pip-audit (Python) and `pnpm audit` (JS), and a
    # measured attempt at a redundant trivy vuln pass over the whole repo reliably timed
    # out past 5 minutes regardless of --skip-dirs. Misconfig scanning (Dockerfile/
    # Terraform/Helm) is Trivy's actual unique contribution here, so that's all it runs.
    #
    # Scoped to `infra`/`web` directly, not `/repo`, and with bare (unprefixed)
    # `--skip-dirs` entries: a combined `fs --scanners vuln,misconfig` run over `/repo`
    # walked into `web/node_modules` for pnpm license detection and never returned
    # within a 10-minute budget, even with `web/node_modules` already in --skip-dirs -
    # that path-prefixed form does not reliably stop this scanner's directory walk in
    # this trivy version, but a bare `node_modules` does (confirmed: scoping the target
    # directory to `web` with `--skip-dirs node_modules,.next` completes in seconds).
    for subdir in ("infra", "web"):
        run(
            "docker", "run", "--rm", "--volume", f"{ROOT}:/repo:ro", *trivy_cache, TRIVY_IMAGE,
            "config", "--severity", "HIGH,CRITICAL", "--exit-code", "1",
            "--ignorefile", "/repo/.trivyignore", "--timeout", "3m",
            "--skip-dirs", "node_modules,.next",
            f"/repo/{subdir}",
        )  # fmt: skip
    # Informational, not gating: Checkov's default ruleset mostly flags production-
    # hardening gaps (RDS deletion protection, S3 replication/access logging, VPC flow
    # logs, Secrets Manager rotation) that are legitimate for a real deployment but not
    # exploitable vulnerabilities in a dev/reference stack that is never `apply`-ed
    # without review (see docs/cost-guardrails.md). Matches the CI workflow's own
    # `soft_fail: true` for the same reason - see docs/threat-model.md "Known gaps".
    checkov_argv = [
        "docker", "run", "--rm", "--volume", f"{ROOT}:/repo:ro", CHECKOV_IMAGE,
        "--directory", "/repo/infra/terraform", "--directory", "/repo/infra/helm",
        "--framework", "terraform", "--framework", "helm", "--framework", "dockerfile",
        "--compact", "--quiet",
    ]  # fmt: skip
    print(f"$ {' '.join(checkov_argv)}", flush=True)
    subprocess.run(checkov_argv, cwd=ROOT, check=False)  # noqa: S603 - argv built from constants above


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
    "agents": agents,
    "agent-smoke": agent_smoke,
    "ui": ui,
    "obs": obs,
    "down": down,
    "k8s-up": k8s_up,
    "k8s-smoke": k8s_smoke,
    "k8s-down": k8s_down,
    "dev": dev,
    "dev-agent": dev_agent,
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
