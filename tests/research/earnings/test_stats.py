"""Regression standard errors against cases worked by hand or by construction."""

from __future__ import annotations

import numpy as np
import pytest

from alphadots_research.earnings.stats import newey_west, ols, winsorize, zscore_by_group


def data(seed: int = 3, n: int = 400) -> tuple[np.ndarray, np.ndarray]:
    rng = np.random.default_rng(seed)
    x = rng.normal(size=n)
    y = 0.5 + 2.0 * x + rng.normal(size=n)
    return x, y


def test_coefficients_match_least_squares() -> None:
    x, y = data()
    fit = ols(y, {"x": x})
    expected, *_ = np.linalg.lstsq(np.column_stack([np.ones_like(x), x]), y, rcond=None)
    np.testing.assert_allclose(fit.coef, expected)
    assert fit.row("x")["coef"] == pytest.approx(2.0, abs=0.15)


def test_white_errors_match_the_sandwich_formula() -> None:
    x, y = data()
    fit = ols(y, {"x": x})
    design = np.column_stack([np.ones_like(x), x])
    residuals = y - design @ fit.coef
    bread = np.linalg.inv(design.T @ design)
    meat = (design * residuals[:, None] ** 2).T @ design
    n, k = design.shape
    factor = n / (n - 1) * (n - 1) / (n - k)
    np.testing.assert_allclose(fit.se, np.sqrt(np.diag(factor * bread @ meat @ bread)))


def test_two_way_with_a_trivial_second_dimension_equals_one_way() -> None:
    x, y = data()
    firms = np.repeat(np.arange(40), 10).astype(np.int64)
    one_way = ols(y, {"x": x}, [firms])
    two_way = ols(y, {"x": x}, [firms, np.arange(x.size, dtype=np.int64)])
    np.testing.assert_allclose(two_way.se, one_way.se, rtol=1e-10)


def test_clustering_widens_errors_when_shocks_are_shared() -> None:
    rng = np.random.default_rng(5)
    groups = np.repeat(np.arange(30), 20).astype(np.int64)
    x = rng.normal(size=30)[groups] + 0.1 * rng.normal(size=groups.size)
    y = x + rng.normal(size=30)[groups] + 0.1 * rng.normal(size=groups.size)
    assert ols(y, {"x": x}, [groups]).se[1] > 2 * ols(y, {"x": x}).se[1]


def test_newey_west_without_lags_is_the_uncorrected_sandwich() -> None:
    x, y = data()
    fit = newey_west(y, {"x": x}, lags=0)
    design = np.column_stack([np.ones_like(x), x])
    residuals = y - design @ fit.coef
    bread = np.linalg.inv(design.T @ design)
    meat = (design * residuals[:, None] ** 2).T @ design
    np.testing.assert_allclose(fit.se, np.sqrt(np.diag(bread @ meat @ bread)))


def test_newey_west_widens_errors_for_autocorrelated_series() -> None:
    rng = np.random.default_rng(9)
    shocks = rng.normal(size=2000)
    y = np.convolve(shocks, np.ones(20), mode="same") / 20
    x = rng.normal(size=y.size)
    assert newey_west(y, {"x": x}, lags=20).se[0] > 2 * newey_west(y, {"x": x}, lags=0).se[0]


def test_winsorize_and_group_zscores() -> None:
    values = np.arange(101, dtype=float)
    clipped = winsorize(values, 0.01, 0.99)
    assert clipped.min() == 1.0
    assert clipped.max() == 99.0
    groups = np.array([0, 0, 0, 1, 1, 1], dtype=np.int64)
    z = zscore_by_group(np.array([1.0, 2.0, 3.0, 10.0, 20.0, 30.0]), groups)
    np.testing.assert_allclose(z, [-1, 0, 1, -1, 0, 1])
