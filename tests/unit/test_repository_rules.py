"""Structural rules: service boundaries, fixture integrity and table ownership."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import pytest

from alphadots_api import tables as api_tables
from alphadots_common.reference import InstrumentMaster
from alphadots_preprocessor import dedup
from alphadots_research_agent import jobs as agent_jobs
from alphadots_signal_engine import store as signal_store
from scripts.generate_fixtures import MANIFEST_NAME, REPLAY_PATH, build_manifest, build_replay_text
from tests.conftest import FIXTURES_DIR, REPO_ROOT

SERVICE_PACKAGES = {
    path.name for path in (REPO_ROOT / "services").glob("*/src/alphadots_*") if path.is_dir()
}
LIB_PACKAGES = {
    path.name for path in (REPO_ROOT / "libs").glob("*/src/alphadots_*") if path.is_dir()
}


def _imports(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    found: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            found.update(alias.name.split(".")[0] for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
            found.add(node.module.split(".")[0])
    return found


def _package_files(root: str, package: str) -> list[Path]:
    return sorted((REPO_ROOT / root).glob(f"*/src/{package}/**/*.py"))


def test_packages_were_discovered() -> None:
    assert {
        "alphadots_api",
        "alphadots_ingestion",
        "alphadots_preprocessor",
        "alphadots_research_agent",
        "alphadots_sentiment",
        "alphadots_signal_engine",
    } == SERVICE_PACKAGES
    assert {
        "alphadots_common",
        "alphadots_contracts",
        "alphadots_llm",
        "alphadots_quant",
    } == LIB_PACKAGES


@pytest.mark.parametrize("package", sorted(SERVICE_PACKAGES))
def test_a_service_never_imports_another_service(package: str) -> None:
    for path in _package_files("services", package):
        foreign = _imports(path) & (SERVICE_PACKAGES - {package})
        assert not foreign, f"{path.relative_to(REPO_ROOT)} imports {sorted(foreign)}"


@pytest.mark.parametrize("package", sorted(LIB_PACKAGES))
def test_libraries_never_import_services(package: str) -> None:
    for path in _package_files("libs", package):
        foreign = _imports(path) & SERVICE_PACKAGES
        assert not foreign, f"{path.relative_to(REPO_ROOT)} imports {sorted(foreign)}"


def test_contracts_quant_and_llm_stay_free_of_runtime_plumbing() -> None:
    for package in ("alphadots_contracts", "alphadots_quant", "alphadots_llm"):
        for path in _package_files("libs", package):
            assert "alphadots_common" not in _imports(path), path


def test_each_service_owns_a_disjoint_prefixed_set_of_tables() -> None:
    owned = {
        "api_": set(api_tables.metadata.tables),
        "pre_": set(dedup.metadata.tables),
        "sig_": set(signal_store.metadata.tables),
        "agent_": set(agent_jobs.metadata.tables),
    }
    for prefix, names in owned.items():
        assert names, prefix
        assert all(name.startswith(prefix) for name in names), (prefix, names)
    all_names = [name for names in owned.values() for name in names]
    assert len(all_names) == len(set(all_names))


# ------------------------------------------------------------------ fixtures


def test_committed_replay_fixture_matches_its_generator() -> None:
    master = InstrumentMaster.load(FIXTURES_DIR / "instruments.json")
    committed = (FIXTURES_DIR / REPLAY_PATH).read_bytes().replace(b"\r\n", b"\n").decode("utf-8")
    assert committed == build_replay_text(master), "run `make fixtures` to regenerate"


def test_fixture_manifest_pins_every_file() -> None:
    committed = json.loads((FIXTURES_DIR / MANIFEST_NAME).read_text(encoding="utf-8"))
    assert committed == build_manifest(FIXTURES_DIR), "a fixture changed; run `make fixtures`"
    assert committed["synthetic"] is True
    assert set(committed["files"]) == {"instruments.json", "news/replay.jsonl", "news/seed.jsonl"}


def test_fixtures_are_unmistakably_synthetic() -> None:
    for path in (FIXTURES_DIR / "news").glob("*.jsonl"):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.startswith("{") or '"source_url"' not in line:
                continue
            assert ".example.invalid/" in line, "fixture URLs must be non-resolvable"
            assert "synthetic" in line.lower()
