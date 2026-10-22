"""A tiny, hand-written finance polarity lexicon and scorer.

This is the *baseline* rung of the model ladder: transparent, deterministic, CPU-trivial,
and expected to be beaten. Its job is to give every later model something to prove lift
against, and to keep the pipeline runnable with no model download.

The word lists were written for this project and are deliberately small. The
Loughran-McDonald dictionary is the standard reference but is not bundled, because its
licence restricts redistribution and commercial use (see ``docs/data-licenses.md``).

Scoring: count positive and negative term hits, flipping polarity when a negator appears
within the three tokens before a term. The output distribution adds one neutral
pseudo-count. These are scores, not calibrated probabilities.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

from alphadots_contracts import PolarityScores

MODEL_ID = "lexicon-baseline"
MODEL_VERSION = "lex-0.1.0"
CALIBRATION_VERSION = "uncalibrated"

_NEGATION_WINDOW = 3
_MAX_PHRASE_TOKENS = 3
_NEUTRAL_PSEUDO_COUNT = 1.0
_TOKEN_RE = re.compile(r"[a-z]+(?:'[a-z]+)?")

NEGATORS = frozenset(
    {"not", "no", "never", "without", "fails", "failed", "fail", "unable", "denies", "denied"}
)

POSITIVE_TERMS = frozenset(
    {
        "beats", "beat", "tops", "topped", "exceeds", "exceeded", "surpasses", "outperforms",
        "raises", "raised", "lifts", "lifted", "boosts", "boosted", "upgrades", "upgraded",
        "upgrade", "growth", "grows", "grew", "surges", "surged", "jumps", "jumped", "soars",
        "soared", "rallies", "gains", "gained", "record", "strong", "stronger", "robust",
        "profitable", "profit", "wins", "won", "awarded", "approval", "approved", "approves",
        "expands", "expanded", "expansion", "accelerates", "accelerated", "improves", "improved",
        "improvement", "rebound", "rebounds", "recovery", "recovers", "breakthrough", "milestone",
        "buyback", "dividend increase", "raises guidance", "raises outlook", "beats estimates",
        "tops estimates", "record revenue", "ahead of schedule", "strategic partnership",
        "secures", "secured", "outperform", "resilient", "momentum",
    }
)  # fmt: skip

NEGATIVE_TERMS = frozenset(
    {
        "misses", "missed", "miss", "cuts", "cut", "lowers", "lowered", "slashes", "slashed",
        "downgrades", "downgraded", "downgrade", "falls", "fell", "drops", "dropped", "plunges",
        "plunged", "slumps", "slumped", "tumbles", "tumbled", "declines", "declined", "decline",
        "weak", "weaker", "weakness", "loss", "losses", "warns", "warned", "warning", "probe",
        "investigation", "lawsuit", "sued", "fine", "fined", "penalty", "recall", "recalls",
        "breach", "delays", "delayed", "delay", "halts", "halted", "suspends", "suspended",
        "layoffs", "resigns", "resigned", "default", "bankruptcy", "fraud", "shortfall",
        "impairment", "writedown", "restates", "restatement", "subpoena", "outage", "rejected",
        "rejects", "cuts guidance", "lowers guidance", "misses estimates", "data breach",
        "class action", "going concern", "profit warning", "behind schedule", "underperform",
        "headwinds", "slowdown",
    }
)  # fmt: skip


@dataclass(frozen=True, slots=True)
class LexiconResult:
    positive_hits: int
    negative_hits: int
    terms: tuple[str, ...]
    """Matched terms in reading order, prefixed with ``+`` or ``-`` after negation."""

    @property
    def total_hits(self) -> int:
        return self.positive_hits + self.negative_hits


def tokenize(text: str) -> list[str]:
    return _TOKEN_RE.findall(text.lower())


def score_text(text: str) -> LexiconResult:
    tokens = tokenize(text)
    positive = 0
    negative = 0
    terms: list[str] = []
    index = 0
    while index < len(tokens):
        matched = None
        # Prefer the longest phrase so "cuts guidance" is one negative hit, not two.
        for size in range(min(_MAX_PHRASE_TOKENS, len(tokens) - index), 0, -1):
            candidate = " ".join(tokens[index : index + size])
            if candidate in POSITIVE_TERMS or candidate in NEGATIVE_TERMS:
                matched = (candidate, size)
                break
        if matched is None:
            index += 1
            continue
        term, size = matched
        is_positive = term in POSITIVE_TERMS
        window = tokens[max(0, index - _NEGATION_WINDOW) : index]
        if any(token in NEGATORS for token in window):
            is_positive = not is_positive
        if is_positive:
            positive += 1
        else:
            negative += 1
        terms.append(f"{'+' if is_positive else '-'}{term}")
        index += size
    return LexiconResult(positive_hits=positive, negative_hits=negative, terms=tuple(terms))


def polarity_scores(result: LexiconResult) -> PolarityScores:
    total = result.positive_hits + result.negative_hits + _NEUTRAL_PSEUDO_COUNT
    return PolarityScores(
        negative=result.negative_hits / total,
        neutral=_NEUTRAL_PSEUDO_COUNT / total,
        positive=result.positive_hits / total,
    )


UNIFORM_SCORES = PolarityScores(negative=1 / 3, neutral=1 / 3, positive=1 / 3)


def normalized_entropy(scores: PolarityScores) -> float:
    """Shannon entropy of ``scores`` divided by ln 3, so 0 is certain and 1 is uniform."""
    entropy = -sum(p * math.log(p) for p in (scores.negative, scores.neutral, scores.positive) if p)
    return min(1.0, max(0.0, entropy / math.log(3)))
