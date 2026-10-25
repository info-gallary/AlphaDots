"""The study universe: the largest US filers by 2014 public float, with XBRL unit errors
removed. Chosen from 2014 data only, so selection does not favour today's survivors.
"""

from __future__ import annotations

from dataclasses import dataclass

from alphadots_research.earnings.sources import FloatFact

UNIVERSE_SIZE = 300
MAX_FLOAT_USD = 1e12
MAX_FLOAT_TO_ASSETS = 30.0
FLOAT_PERIODS = ("CY2014Q1I", "CY2014Q2I", "CY2014Q3I", "CY2014Q4I")


@dataclass(frozen=True, slots=True)
class Rejected:
    cik: int
    name: str
    float_usd: float
    assets_usd: float | None
    reason: str


def latest(facts: list[FloatFact]) -> dict[int, FloatFact]:
    """Each CIK's fact with the latest period end (ties: the larger value, deterministic)."""
    best: dict[int, FloatFact] = {}
    for fact in facts:
        held = best.get(fact.cik)
        if held is None or (fact.end, fact.value) > (held.end, held.value):
            best[fact.cik] = fact
    return best


def select(
    floats: list[FloatFact], assets: list[FloatFact], size: int = UNIVERSE_SIZE
) -> tuple[list[FloatFact], list[Rejected]]:
    """The ``size`` largest plausible floats, and every CIK rejected as a data error.

    Only CIKs that would otherwise have made the cut are reported as rejected, so the list
    stays readable.
    """
    float_by_cik, assets_by_cik = latest(floats), latest(assets)
    ranked = sorted(float_by_cik.values(), key=lambda fact: (-fact.value, fact.cik))
    chosen: list[FloatFact] = []
    rejected: list[Rejected] = []
    for fact in ranked:
        if len(chosen) == size:
            break
        held = assets_by_cik.get(fact.cik)
        total_assets = held.value if held else None
        reason = None
        if fact.value > MAX_FLOAT_USD:
            reason = "float above USD 1 trillion"
        elif total_assets is None or total_assets <= 0:
            reason = "no total assets reported"
        elif fact.value > MAX_FLOAT_TO_ASSETS * total_assets:
            reason = f"float above {MAX_FLOAT_TO_ASSETS:g}x total assets"
        if reason:
            rejected.append(Rejected(fact.cik, fact.name, fact.value, total_assets, reason))
        else:
            chosen.append(fact)
    return chosen, rejected
