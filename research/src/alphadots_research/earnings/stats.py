"""Regression with honest standard errors: two-way clustered OLS for event panels and
Newey-West for daily return series. Plain numpy, tested against hand-worked cases."""

from __future__ import annotations

import math
from dataclasses import dataclass

import numpy as np

from alphadots_research.market import FloatArray

IntArray = np.ndarray[tuple[int], np.dtype[np.int64]]


@dataclass(frozen=True, slots=True)
class Fit:
    names: tuple[str, ...]
    coef: FloatArray
    se: FloatArray
    n: int
    r2: float

    @property
    def t(self) -> FloatArray:
        out: FloatArray = np.divide(
            self.coef, self.se, out=np.full_like(self.coef, math.nan), where=self.se > 0
        )
        return out

    def row(self, name: str) -> dict[str, float]:
        i = self.names.index(name)
        return {"coef": float(self.coef[i]), "se": float(self.se[i]), "t": float(self.t[i])}


def winsorize(values: FloatArray, low: float = 0.01, high: float = 0.99) -> FloatArray:
    lo, hi = np.nanquantile(values, [low, high])
    clipped: FloatArray = np.clip(values, lo, hi)
    return clipped


def zscore_by_group(values: FloatArray, groups: IntArray) -> FloatArray:
    out = np.full_like(values, math.nan)
    for group in np.unique(groups):
        members = groups == group
        chunk = values[members]
        deviation = chunk.std(ddof=1) if chunk.size > 1 else 0.0
        out[members] = (chunk - chunk.mean()) / deviation if deviation > 0 else 0.0
    return out


def _meat(design: FloatArray, residuals: FloatArray, clusters: IntArray) -> tuple[FloatArray, int]:
    scores = design * residuals[:, None]
    _, inverse = np.unique(clusters, return_inverse=True)
    groups = int(inverse.max()) + 1
    summed = np.zeros((groups, design.shape[1]))
    np.add.at(summed, inverse, scores)
    return summed.T @ summed, groups


def ols(
    y: FloatArray, columns: dict[str, FloatArray], clusters: list[IntArray] | None = None
) -> Fit:
    """OLS with an intercept. ``clusters``: none (White), one, or two dimensions (Cameron,
    Gelbach and Miller 2011: V = V_a + V_b - V_ab, with each term's small-sample factor
    G/(G-1) * (N-1)/(N-K), and negative eigenvalues clipped to zero)."""
    names = ("const", *columns)
    design = np.column_stack([np.ones_like(y), *columns.values()])
    n, k = design.shape
    bread = np.linalg.inv(design.T @ design)
    coef = bread @ design.T @ y
    residuals = y - design @ coef

    def covariance(ids: IntArray) -> FloatArray:
        meat, groups = _meat(design, residuals, ids)
        factor = groups / (groups - 1) * (n - 1) / (n - k) if groups > 1 else 1.0
        cov: FloatArray = factor * bread @ meat @ bread
        return cov

    if not clusters:
        cov = covariance(np.arange(n, dtype=np.int64))
    elif len(clusters) == 1:
        cov = covariance(clusters[0])
    else:
        a, b = clusters
        _, both = np.unique(np.column_stack([a, b]), axis=0, return_inverse=True)
        cov = covariance(a) + covariance(b) - covariance(both.astype(np.int64).ravel())
        values, vectors = np.linalg.eigh(cov)
        cov = vectors @ np.diag(np.clip(values, 0, None)) @ vectors.T
    total = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float((residuals**2).sum()) / total if total > 0 else math.nan
    return Fit(names=names, coef=coef, se=np.sqrt(np.diag(cov)), n=n, r2=r2)


def newey_west(y: FloatArray, columns: dict[str, FloatArray], lags: int) -> Fit:
    """OLS with Newey-West (Bartlett kernel) standard errors for serially correlated
    residuals, such as overlapping-holding-period portfolio returns."""
    names = ("const", *columns)
    design = np.column_stack([np.ones_like(y), *columns.values()])
    n, _ = design.shape
    bread = np.linalg.inv(design.T @ design)
    coef = bread @ design.T @ y
    residuals = y - design @ coef
    scores = design * residuals[:, None]
    meat = scores.T @ scores
    for lag in range(1, lags + 1):
        weight = 1.0 - lag / (lags + 1)
        gamma = scores[lag:].T @ scores[:-lag]
        meat += weight * (gamma + gamma.T)
    cov = bread @ meat @ bread
    total = float(((y - y.mean()) ** 2).sum())
    r2 = 1.0 - float((residuals**2).sum()) / total if total > 0 else math.nan
    return Fit(names=names, coef=coef, se=np.sqrt(np.diag(cov)), n=n, r2=r2)
