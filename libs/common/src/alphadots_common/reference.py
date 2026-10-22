"""Effective-dated instrument master.

A ticker is not an identity: it can change, and it can be reused by a different issuer.
Lookups therefore always take the date they are asked about, and events are linked to the
stable ``instrument_id``.

In M0 the master is an immutable, versioned fixture file that each service reads. It is
reference data distributed as a file, not a mutable table shared between services.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import date
from pathlib import Path
from typing import Any


@dataclass(frozen=True, slots=True)
class Listing:
    symbol: str
    valid_from: date
    valid_to: date | None  # inclusive; None means still listed

    def active_on(self, on: date) -> bool:
        return self.valid_from <= on and (self.valid_to is None or on <= self.valid_to)


@dataclass(frozen=True, slots=True)
class Instrument:
    instrument_id: str
    name: str
    sector: str
    aliases: tuple[str, ...]
    listings: tuple[Listing, ...]

    def symbol_on(self, on: date) -> str | None:
        return next((item.symbol for item in self.listings if item.active_on(on)), None)

    @property
    def all_symbols(self) -> tuple[str, ...]:
        return tuple(dict.fromkeys(item.symbol for item in self.listings))


class InstrumentMaster:
    def __init__(self, *, version: str, license_name: str, instruments: list[Instrument]) -> None:
        self.version = version
        self.license_name = license_name
        self._instruments = {item.instrument_id: item for item in instruments}
        if len(self._instruments) != len(instruments):
            raise ValueError("instrument master contains duplicate instrument_id values")
        self._check_no_overlapping_symbols()

    @classmethod
    def load(cls, path: Path) -> InstrumentMaster:
        document: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
        instruments = [
            Instrument(
                instrument_id=entry["instrument_id"],
                name=entry["name"],
                sector=entry["sector"],
                aliases=tuple(entry.get("aliases", ())),
                listings=tuple(
                    Listing(
                        symbol=listing["symbol"].upper(),
                        valid_from=date.fromisoformat(listing["valid_from"]),
                        valid_to=(
                            date.fromisoformat(listing["valid_to"])
                            if listing.get("valid_to")
                            else None
                        ),
                    )
                    for listing in entry["listings"]
                ),
            )
            for entry in document["instruments"]
        ]
        return cls(
            version=document["version"],
            license_name=document["license"],
            instruments=instruments,
        )

    def all(self) -> list[Instrument]:
        return sorted(self._instruments.values(), key=lambda item: item.instrument_id)

    def get(self, instrument_id: str) -> Instrument | None:
        return self._instruments.get(instrument_id)

    def by_symbol(self, symbol: str, on: date) -> Instrument | None:
        """Return the instrument that traded as ``symbol`` on ``on``, if any."""
        wanted = symbol.strip().upper()
        for instrument in self._instruments.values():
            if any(item.symbol == wanted and item.active_on(on) for item in instrument.listings):
                return instrument
        return None

    def ever_listed_as(self, symbol: str) -> list[Instrument]:
        """Every instrument that has used ``symbol`` at any time (old tickers included)."""
        wanted = symbol.strip().upper()
        return [item for item in self.all() if wanted in item.all_symbols]

    def search(self, query: str, on: date) -> list[Instrument]:
        """Case-insensitive substring search over names, aliases and symbols listed on ``on``."""
        needle = query.strip().casefold()
        if not needle:
            return [item for item in self.all() if item.symbol_on(on) is not None]
        matches = []
        for instrument in self.all():
            symbol = instrument.symbol_on(on)
            if symbol is None:
                continue
            haystack = (instrument.name, symbol, *instrument.aliases)
            if any(needle in text.casefold() for text in haystack):
                matches.append(instrument)
        return matches

    def _check_no_overlapping_symbols(self) -> None:
        claims: dict[str, list[tuple[str, Listing]]] = {}
        for instrument in self._instruments.values():
            for listing in instrument.listings:
                claims.setdefault(listing.symbol, []).append((instrument.instrument_id, listing))
        for symbol, holders in claims.items():
            for index, (first_id, first) in enumerate(holders):
                for second_id, second in holders[index + 1 :]:
                    first_end = first.valid_to or date.max
                    second_end = second.valid_to or date.max
                    if first.valid_from <= second_end and second.valid_from <= first_end:
                        raise ValueError(
                            f"symbol {symbol} is claimed by {first_id} and {second_id} "
                            "over overlapping dates"
                        )
