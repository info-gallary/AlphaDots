"""Universe selection with data-error rejection, and tone scoring."""

from __future__ import annotations

from datetime import date

from alphadots_research.earnings.sources import Dictionary, FloatFact
from alphadots_research.earnings.tone import lexicon_tone, lm_tone
from alphadots_research.earnings.universe import latest, select


def fact(cik: int, value: float, end: date = date(2014, 6, 30)) -> FloatFact:
    return FloatFact(cik=cik, name=f"firm {cik}", end=end, value=value)


def test_latest_period_end_wins() -> None:
    facts = [fact(1, 5.0, date(2014, 3, 31)), fact(1, 7.0, date(2014, 12, 31))]
    assert latest(facts)[1].value == 7.0


def test_unit_errors_are_rejected_and_reported() -> None:
    floats = [
        fact(1, 907e12),  # above USD 1 trillion
        fact(2, 239e9),  # 400x its assets: a unit error
        fact(3, 200e9),  # no assets reported
        fact(4, 150e9),
        fact(5, 100e9),
        fact(6, 50e9),
    ]
    assets = [fact(2, 0.6e9), fact(4, 100e9), fact(5, 300e9), fact(6, 10e9)]
    chosen, rejected = select(floats, assets, size=2)
    assert [f.cik for f in chosen] == [4, 5]
    assert [r.cik for r in rejected] == [1, 2, 3]
    assert "1 trillion" in rejected[0].reason
    assert "30x" in rejected[1].reason


DICTIONARY = Dictionary(
    positive=frozenset({"STRONG", "GROWTH", "IMPROVED"}),
    negative=frozenset({"LOSS", "DECLINE"}),
)


def test_lm_tone_counts_words_and_skips_negated_positives() -> None:
    tone = lm_tone("Strong growth, no improved margins, and a small loss.", DICTIONARY)
    assert (tone.positive, tone.negative) == (2, 1)
    assert tone.words == 9
    assert tone.net == (2 - 1) / 3


def test_tone_without_tone_words_is_undefined() -> None:
    assert lm_tone("Results are attached.", DICTIONARY).net is None


def test_pipeline_lexicon_tone_uses_the_live_scorer() -> None:
    tone = lexicon_tone("Revenue surged to a record; margins improved despite a loss.")
    assert tone.positive >= 2
    assert tone.negative == 1
