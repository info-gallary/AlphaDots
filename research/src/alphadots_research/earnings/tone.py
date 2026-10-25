"""Tone of a press release: Loughran-McDonald word counts, and the pipeline's own lexicon
for comparison."""

from __future__ import annotations

import re
from dataclasses import dataclass

from alphadots_research.earnings.sources import Dictionary
from alphadots_sentiment.lexicon import score_text

NEGATORS = frozenset({"NO", "NOT", "NONE", "NEITHER", "NEVER", "NOBODY"})
NEGATION_WINDOW = 3
MIN_WORDS = 100
_WORD = re.compile(r"[A-Za-z]+")


@dataclass(frozen=True, slots=True)
class Tone:
    words: int
    positive: int
    negative: int

    @property
    def net(self) -> float | None:
        """(P - N) / (P + N), in [-1, 1]; None when the text has no tone words."""
        hits = self.positive + self.negative
        return (self.positive - self.negative) / hits if hits else None


def lm_tone(text: str, dictionary: Dictionary) -> Tone:
    """Count LM positive and negative words; a positive word with a negator among the
    three words before it is not counted (Loughran and McDonald, 2011)."""
    tokens = [token.upper() for token in _WORD.findall(text)]
    positive = negative = 0
    for index, token in enumerate(tokens):
        if token in dictionary.negative:
            negative += 1
        elif token in dictionary.positive:
            window = tokens[max(0, index - NEGATION_WINDOW) : index]
            if not any(word in NEGATORS for word in window):
                positive += 1
    return Tone(words=len(tokens), positive=positive, negative=negative)


def lexicon_tone(text: str) -> Tone:
    """The live pipeline's hand-written headline lexicon, applied to the release."""
    result = score_text(text)
    return Tone(
        words=len(_WORD.findall(text)),
        positive=result.positive_hits,
        negative=result.negative_hits,
    )
