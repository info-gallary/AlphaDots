"""Rule-based issuer resolution against the effective-dated instrument master.

Evidence, strongest first:

1. A ticker the provider tagged, or one written in the text as ``(NWSC)`` or ``$NWSC``,
   that was listed on the publication date.
2. A company name or alias found in the text on word boundaries. The longest alias wins a
   span, so "Apex Robotics" is never also counted as a bare, ambiguous "Apex".

The confidence values below are hand-set priors that order the evidence. They are not
measured link accuracy; that needs a labelled sample and is part of the NLP evaluation.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import date, datetime

from alphadots_common.reference import InstrumentMaster
from alphadots_contracts import InstrumentRef, ResolutionFlag

CONFIDENCE_SYMBOL_AND_NAME = 0.98
CONFIDENCE_PROVIDER_SYMBOL = 0.90
CONFIDENCE_TEXT_SYMBOL = 0.85
CONFIDENCE_NAME_ONLY = 0.75
CONFIDENCE_AMBIGUOUS_TOTAL = 0.50
_CONFIDENT_LINK = CONFIDENCE_NAME_ONLY

_TEXT_SYMBOL_RE = re.compile(r"(?:\$([A-Z]{1,6})\b|\((?:[A-Z]+:\s?)?([A-Z]{1,6})\))")


@dataclass(frozen=True, slots=True)
class Resolution:
    instruments: tuple[InstrumentRef, ...]
    flags: tuple[ResolutionFlag, ...]


@dataclass(slots=True)
class _Evidence:
    """Instrument IDs supported by each kind of evidence found for one event."""

    provider_symbols: set[str] = field(default_factory=set)
    text_symbols: set[str] = field(default_factory=set)
    unique_names: set[str] = field(default_factory=set)
    ambiguous_names: list[set[str]] = field(default_factory=list)
    unknown_provider_symbol: bool = False

    @property
    def symbols(self) -> set[str]:
        return self.provider_symbols | self.text_symbols


class EntityResolver:
    def __init__(self, master: InstrumentMaster) -> None:
        self._master = master
        alias_owners: dict[str, set[str]] = {}
        for instrument in master.all():
            for alias in (instrument.name, *instrument.aliases):
                alias_owners.setdefault(alias.casefold(), set()).add(instrument.instrument_id)
        self._alias_owners = alias_owners
        # Longest alternatives first so the regex prefers "apex robotics" over "apex".
        ordered = sorted(alias_owners, key=len, reverse=True)
        self._alias_re = (
            re.compile(
                r"(?<!\w)(" + "|".join(re.escape(alias) for alias in ordered) + r")(?!\w)",
                re.IGNORECASE,
            )
            if ordered
            else None
        )

    def resolve(self, *, text: str, provider_symbols: Sequence[str], as_of: datetime) -> Resolution:
        on = as_of.date()
        evidence = self._gather(text, provider_symbols, on)
        confidence, ambiguous = self._score(evidence)

        refs = []
        for instrument_id, score in confidence.items():
            instrument = self._master.get(instrument_id)
            symbol = instrument.symbol_on(on) if instrument is not None else None
            if symbol is not None:  # otherwise not listed that day, so not tradable then
                refs.append(
                    InstrumentRef(symbol=symbol, instrument_id=instrument_id, confidence=score)
                )
        refs.sort(key=lambda ref: (-ref.confidence, ref.instrument_id))

        flags: set[ResolutionFlag] = set()
        if evidence.unknown_provider_symbol:
            flags.add(ResolutionFlag.PROVIDER_SYMBOL_UNKNOWN)
        if ambiguous:
            flags.add(ResolutionFlag.AMBIGUOUS_ISSUER)
        if not refs:
            flags.add(ResolutionFlag.UNRESOLVED)
        elif sum(1 for ref in refs if ref.confidence >= _CONFIDENT_LINK) > 1:
            flags.add(ResolutionFlag.MULTI_ISSUER)
        return Resolution(instruments=tuple(refs), flags=tuple(sorted(flags)))

    def _gather(self, text: str, provider_symbols: Sequence[str], on: date) -> _Evidence:
        evidence = _Evidence()
        for symbol in provider_symbols:
            instrument = self._master.by_symbol(symbol, on)
            if instrument is None:
                evidence.unknown_provider_symbol = True
            else:
                evidence.provider_symbols.add(instrument.instrument_id)

        for match in _TEXT_SYMBOL_RE.finditer(text):
            instrument = self._master.by_symbol(match.group(1) or match.group(2), on)
            if instrument is not None:
                evidence.text_symbols.add(instrument.instrument_id)

        if self._alias_re is not None:
            for match in self._alias_re.finditer(text):
                owners = self._alias_owners[match.group(1).casefold()]
                if len(owners) == 1:
                    evidence.unique_names.update(owners)
                else:
                    evidence.ambiguous_names.append(owners)
        return evidence

    @staticmethod
    def _score(evidence: _Evidence) -> tuple[dict[str, float], bool]:
        """Confidence per instrument, and whether an alias stayed ambiguous."""
        confidence: dict[str, float] = {}
        for instrument_id in evidence.symbols | evidence.unique_names:
            if instrument_id in evidence.symbols and instrument_id in evidence.unique_names:
                confidence[instrument_id] = CONFIDENCE_SYMBOL_AND_NAME
            elif instrument_id in evidence.provider_symbols:
                confidence[instrument_id] = CONFIDENCE_PROVIDER_SYMBOL
            elif instrument_id in evidence.text_symbols:
                confidence[instrument_id] = CONFIDENCE_TEXT_SYMBOL
            else:
                confidence[instrument_id] = CONFIDENCE_NAME_ONLY

        ambiguous = False
        for owners in evidence.ambiguous_names:
            corroborated = owners & confidence.keys()
            if corroborated:
                # Other evidence already picks a candidate; the bare alias adds a name match.
                for instrument_id in corroborated & evidence.symbols:
                    confidence[instrument_id] = CONFIDENCE_SYMBOL_AND_NAME
                continue
            ambiguous = True
            share = CONFIDENCE_AMBIGUOUS_TOTAL / len(owners)
            for instrument_id in owners:
                confidence[instrument_id] = max(confidence.get(instrument_id, 0.0), share)
        return confidence, ambiguous
