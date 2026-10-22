"""IDs, time helpers, reference data, database helpers and logging redaction."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta, timezone

import pytest
from sqlalchemy import Column, MetaData, String, Table, select

from alphadots_common.db import UtcDateTime, make_engine, upsert
from alphadots_common.ids import derive_id, sha256_hex, trace_id_for
from alphadots_common.logging import _redact_sensitive
from alphadots_common.reference import Instrument, InstrumentMaster, Listing
from alphadots_common.timeutils import ensure_utc, parse_window

# ------------------------------------------------------------------------ ids


def test_ids_are_deterministic_and_namespaced() -> None:
    assert derive_id("raw", "wire", "1") == derive_id("raw", "wire", "1")
    assert derive_id("raw", "wire", "1") != derive_id("norm", "wire", "1")
    assert derive_id("raw", "wire", "1").startswith("raw_")
    assert len(trace_id_for("anything")) == 32


def test_hash_parts_are_length_prefixed() -> None:
    assert sha256_hex("ab", "c") != sha256_hex("a", "bc")
    assert sha256_hex("abc") != sha256_hex("ab", "c")


# ----------------------------------------------------------------------- time


@pytest.mark.parametrize(
    ("text", "expected"),
    [("30m", timedelta(minutes=30)), ("24h", timedelta(hours=24)), (" 7D ", timedelta(days=7))],
)
def test_parse_window(text: str, expected: timedelta) -> None:
    assert parse_window(text) == expected


@pytest.mark.parametrize("text", ["", "24", "h", "0h", "-1d", "1w", "1.5h", "24 h"])
def test_parse_window_rejects_malformed_input(text: str) -> None:
    with pytest.raises(ValueError, match="invalid window"):
        parse_window(text)


def test_ensure_utc_refuses_to_guess_a_timezone() -> None:
    with pytest.raises(ValueError, match="naive"):
        ensure_utc(datetime(2026, 3, 2, 14, 0))  # noqa: DTZ001 - the naive value is the point
    eastern = datetime(2026, 3, 2, 9, 0, tzinfo=timezone(timedelta(hours=-5)))
    assert ensure_utc(eastern) == datetime(2026, 3, 2, 14, 0, tzinfo=UTC)


# ------------------------------------------------------------------ reference


def test_symbol_lookup_is_effective_dated(master: InstrumentMaster) -> None:
    before, after = date(2026, 3, 8), date(2026, 3, 9)
    vireo = master.by_symbol("VIRE", before)
    assert vireo is not None
    assert vireo.instrument_id == "SYN-0008"
    assert master.by_symbol("VIRE", after) is None
    assert master.by_symbol("vrcs", after) is vireo
    assert master.by_symbol("VRCS", before) is None
    assert vireo.symbol_on(before) == "VIRE"
    assert vireo.symbol_on(after) == "VRCS"
    assert vireo.all_symbols == ("VIRE", "VRCS")


def test_instrument_is_not_listed_before_its_first_listing(master: InstrumentMaster) -> None:
    assert master.by_symbol("HLCB", date(2021, 6, 14)) is None
    assert master.by_symbol("HLCB", date(2021, 6, 15)) is not None


def test_old_tickers_remain_discoverable(master: InstrumentMaster) -> None:
    assert [item.instrument_id for item in master.ever_listed_as("VIRE")] == ["SYN-0008"]
    assert master.ever_listed_as("NOPE") == []


def test_search_matches_names_aliases_and_symbols(master: InstrumentMaster) -> None:
    on = date(2026, 3, 10)
    assert {item.instrument_id for item in master.search("apex", on)} == {"SYN-0004", "SYN-0005"}
    assert [item.instrument_id for item in master.search("VRCS", on)] == ["SYN-0008"]
    assert [item.instrument_id for item in master.search("grid", on)] == ["SYN-0007"]
    assert master.search("no such issuer", on) == []
    assert len(master.search("", on)) == len(master.all())
    assert master.search("", date(1990, 1, 1)) == [], (
        "only instruments listed on the date are returned"
    )


def _instrument(instrument_id: str, valid_from: date, valid_to: date | None) -> Instrument:
    return Instrument(
        instrument_id=instrument_id,
        name=instrument_id,
        sector="Test",
        aliases=(),
        listings=(Listing("DUPE", valid_from, valid_to),),
    )


def test_one_ticker_cannot_belong_to_two_issuers_at_once() -> None:
    first = _instrument("A", date(2020, 1, 1), date(2022, 12, 31))
    overlapping = _instrument("B", date(2022, 6, 1), None)
    with pytest.raises(ValueError, match="overlapping dates"):
        InstrumentMaster(version="t", license_name="t", instruments=[first, overlapping])

    reused_later = _instrument("B", date(2023, 1, 1), None)
    master = InstrumentMaster(version="t", license_name="t", instruments=[first, reused_later])
    assert master.by_symbol("DUPE", date(2021, 1, 1)) is first
    assert master.by_symbol("DUPE", date(2024, 1, 1)) is reused_later


# ------------------------------------------------------------------- database

_metadata = MetaData()
_things = Table(
    "things",
    _metadata,
    Column("id", String, primary_key=True),
    Column("value", String, nullable=False),
    Column("at", UtcDateTime(), nullable=False),
)


async def test_timestamps_round_trip_as_aware_utc_and_naive_is_refused() -> None:
    engine = make_engine("sqlite+aiosqlite://")
    eastern = datetime(2026, 3, 2, 9, 0, tzinfo=timezone(timedelta(hours=-5)))
    async with engine.begin() as conn:
        await conn.run_sync(_metadata.create_all)
        await conn.execute(_things.insert().values(id="a", value="x", at=eastern))
        stored = (await conn.execute(select(_things.c.at))).scalar_one()
        assert stored == datetime(2026, 3, 2, 14, 0, tzinfo=UTC)
        assert stored.utcoffset() == timedelta(0)
        with pytest.raises(Exception, match="naive datetime"):
            await conn.execute(
                _things.insert().values(id="b", value="x", at=datetime(2026, 3, 2))  # noqa: DTZ001
            )
    await engine.dispose()


async def test_upsert_overwrites_or_keeps_the_first_row() -> None:
    engine = make_engine("sqlite+aiosqlite://")
    at = datetime(2026, 3, 2, tzinfo=UTC)
    async with engine.begin() as conn:
        await conn.run_sync(_metadata.create_all)
        await upsert(conn, _things, [{"id": "a", "value": "one", "at": at}], key_columns=["id"])
        await upsert(conn, _things, [{"id": "a", "value": "two", "at": at}], key_columns=["id"])
        assert (await conn.execute(select(_things.c.value))).scalars().all() == ["two"]
        await upsert(
            conn,
            _things,
            [{"id": "a", "value": "three", "at": at}],
            key_columns=["id"],
            update=False,
        )
        assert (await conn.execute(select(_things.c.value))).scalars().all() == ["two"]
        await upsert(conn, _things, [], key_columns=["id"])
    await engine.dispose()


# -------------------------------------------------------------------- logging


def test_log_redaction_masks_credential_like_keys() -> None:
    event = _redact_sensitive(
        None, "info", {"event": "x", "api_token": "abc", "Authorization": "Bearer abc", "n": 1}
    )
    assert event == {"event": "x", "api_token": "[REDACTED]", "Authorization": "[REDACTED]", "n": 1}
