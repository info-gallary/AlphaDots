"""The study's claims, re-derived on a smaller market so they are not one lucky draw.

The committed results come from the default (larger) configuration; the last test checks
they are exactly what the code produces today.
"""

from __future__ import annotations

import json
from typing import Any

import pytest

from alphadots_research.market import MarketConfig
from alphadots_research.study import StudyConfig, run
from scripts.run_research import REPORT, RESULTS, _clean, _close, report

SMALL = StudyConfig(
    market=MarketConfig(n_assets=50, n_days=600, seed=11),
    first_train_days=200,
    test_days=60,
    holdout_days=100,
)


@pytest.fixture(scope="module")
def small() -> dict[str, Any]:
    return run(SMALL)


def _oos(results: dict[str, Any], world: str, strategy: str) -> dict[str, float]:
    oos: dict[str, float] = results["worlds"][world]["strategies"][strategy]["oos"]
    return oos


def test_a_planted_effect_is_found_out_of_sample(small: dict[str, Any]) -> None:
    assert _oos(small, "planted", "sentiment")["ic_tstat"] > 2.5


def test_no_effect_is_found_where_none_was_planted(small: dict[str, Any]) -> None:
    assert abs(_oos(small, "null", "sentiment")["ic_tstat"]) < 2.0
    assert abs(_oos(small, "null", "shuffled")["ic_tstat"]) < 2.0


def test_a_look_ahead_bug_manufactures_a_strategy_from_nothing(small: dict[str, Any]) -> None:
    honest = _oos(small, "null", "sentiment")
    leaky = _oos(small, "null", "lookahead")
    assert leaky["sharpe"] > honest["sharpe"] + 2.0
    assert leaky["ic_tstat"] > 4.0


def test_costs_only_ever_lower_the_sharpe(small: dict[str, Any]) -> None:
    curve = [row["sharpe"] for row in small["worlds"]["planted"]["cost_sensitivity"]]
    assert curve == sorted(curve, reverse=True)


def test_tau_is_chosen_from_the_grid_on_training_data_only(small: dict[str, Any]) -> None:
    for fold in small["worlds"]["planted"]["folds"]:
        assert fold["tau_days"] in SMALL.tau_grid_days
        assert fold["train_end"] < fold["test_start"]


def test_committed_results_and_report_are_what_the_code_produces() -> None:
    committed = json.loads(RESULTS.read_text(encoding="utf-8"))
    assert not _close(_clean(run()), committed), "run `make research`"
    assert REPORT.read_text(encoding="utf-8") == report(committed), "run `make research`"
