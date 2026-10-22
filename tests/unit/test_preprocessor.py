"""Text cleanup, deduplication and entity resolution."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from alphadots_common.db import make_engine
from alphadots_common.reference import InstrumentMaster
from alphadots_common.worker import RejectMessage
from alphadots_contracts import ResolutionFlag
from alphadots_preprocessor.dedup import DedupStore, InMemoryDedupStore, SqlDedupStore
from alphadots_preprocessor.entities import EntityResolver, Resolution
from alphadots_preprocessor.processor import Preprocessor
from alphadots_preprocessor.text import clean_text, content_fingerprint
from tests.conftest import T0, make_raw

# ----------------------------------------------------------------------- text


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("  plain   text\n\twith  gaps ", "plain text with gaps"),
        ("<p>Revenue <b>grew</b></p>", "Revenue grew"),
        ("before<script>alert('x')</script>after", "before after"),
        ("a<STYLE type='x'>p{color:red}</STYLE>b", "a b"),
        ("&lt;script&gt;steal()&lt;/script&gt;safe", "safe"),
        ("&lt;img src=x onerror=alert(1)&gt;caption", "caption"),
        ("AT&amp;T style &quot;quotes&quot;", 'AT&T style "quotes"'),
        ("zero\u200bwidth\u200djoin", "zero width join"),
        ("bidi\u202eoverride", "bidi override"),
        (
            "\uff46\uff55\uff4c\uff4c\uff57\uff49\uff44\uff54\uff48 \uff21\uff22\uff23",
            "fullwidth ABC",
        ),
        ("bell\x07and\x00null", "bell and null"),
    ],
)
def test_clean_text(raw: str, expected: str) -> None:
    assert clean_text(raw) == expected


def test_cleaning_is_idempotent() -> None:
    once = clean_text("<p>Some &amp; <i>markup</i>\u200b here</p>")
    assert clean_text(once) == once


def test_fingerprint_ignores_case_punctuation_and_markup_noise() -> None:
    base = content_fingerprint("Northwind beats estimates", "Revenue grew 12%.")
    assert content_fingerprint("NORTHWIND  beats estimates!", "Revenue grew 12%") == base
    assert content_fingerprint("Northwind beats estimates", None) != base
    assert content_fingerprint("Northwind misses estimates", "Revenue grew 12%.") != base


# ---------------------------------------------------------------- resolution


@pytest.fixture
def resolver(master: InstrumentMaster) -> EntityResolver:
    return EntityResolver(master)


def _resolve(
    resolver: EntityResolver,
    text: str,
    symbols: tuple[str, ...] = (),
    as_of: datetime = T0,
) -> Resolution:
    return resolver.resolve(text=text, provider_symbols=symbols, as_of=as_of)


def _links(resolution: Resolution) -> dict[str, float]:
    return {ref.symbol: ref.confidence for ref in resolution.instruments}


def test_symbol_and_name_agreeing_is_the_strongest_link(resolver: EntityResolver) -> None:
    result = _resolve(resolver, "Northwind Semiconductor beats estimates", ("NWSC",))
    assert _links(result) == {"NWSC": 0.98}
    assert result.flags == ()


def test_evidence_strength_orders_confidence(resolver: EntityResolver) -> None:
    assert _links(_resolve(resolver, "Chip maker beats estimates", ("NWSC",))) == {"NWSC": 0.90}
    assert _links(_resolve(resolver, "Shares of (NWSC) rose")) == {"NWSC": 0.85}
    assert _links(_resolve(resolver, "Buy $HLCB now")) == {"HLCB": 0.85}
    assert _links(_resolve(resolver, "Listed as (NASDAQ: MRFS)")) == {"MRFS": 0.85}
    assert _links(_resolve(resolver, "northwind semiconductor reports")) == {"NWSC": 0.75}


def test_bare_shared_alias_is_ambiguous_and_low_confidence(resolver: EntityResolver) -> None:
    result = _resolve(resolver, "Apex wins multi-year automation contract")
    assert _links(result) == {"APXR": 0.25, "APXE": 0.25}
    assert result.flags == (ResolutionFlag.AMBIGUOUS_ISSUER,)


def test_full_name_or_symbol_disambiguates_a_shared_alias(resolver: EntityResolver) -> None:
    by_name = _resolve(resolver, "Apex Robotics said Apex will expand")
    assert _links(by_name) == {"APXR": 0.75}
    assert by_name.flags == ()

    by_symbol = _resolve(resolver, "Apex lowers guidance", ("APXE",))
    assert _links(by_symbol) == {"APXE": 0.98}
    assert by_symbol.flags == ()


def test_longest_alias_wins_so_a_prefix_is_not_double_counted(resolver: EntityResolver) -> None:
    result = _resolve(resolver, "Apex Energy Partners lowers guidance")
    assert _links(result) == {"APXE": 0.75}


def test_alias_must_match_on_word_boundaries(resolver: EntityResolver) -> None:
    assert _resolve(resolver, "Apexion and Northwinds are unrelated").instruments == ()


def test_unknown_issuer_is_flagged_unresolved(resolver: EntityResolver) -> None:
    result = _resolve(resolver, "Fictional Widgets Ltd unveils new product line")
    assert result.instruments == ()
    assert result.flags == (ResolutionFlag.UNRESOLVED,)


def test_unknown_provider_symbol_is_flagged_but_does_not_block(resolver: EntityResolver) -> None:
    result = _resolve(resolver, "Cobalt Harbor Bank raises dividend", ("CBHB", "ZZZZ"))
    assert _links(result) == {"CBHB": 0.98}
    assert result.flags == (ResolutionFlag.PROVIDER_SYMBOL_UNKNOWN,)


def test_two_confident_issuers_are_flagged_multi_issuer(resolver: EntityResolver) -> None:
    result = _resolve(
        resolver, "Northwind Semiconductor to acquire stake in Apex Robotics", ("NWSC", "APXR")
    )
    assert _links(result) == {"NWSC": 0.98, "APXR": 0.98}
    assert result.flags == (ResolutionFlag.MULTI_ISSUER,)


def test_ticker_is_interpreted_as_of_the_publication_date(resolver: EntityResolver) -> None:
    before = datetime(2026, 3, 6, 15, 0, tzinfo=UTC)
    after = datetime(2026, 3, 9, 15, 0, tzinfo=UTC)

    assert _links(_resolve(resolver, "Software firm rallies", ("VIRE",), before)) == {"VIRE": 0.90}
    assert _links(_resolve(resolver, "Software firm rallies", ("VRCS",), after)) == {"VRCS": 0.90}

    stale = _resolve(resolver, "Software firm rallies", ("VIRE",), after)
    assert stale.instruments == ()
    assert set(stale.flags) == {ResolutionFlag.PROVIDER_SYMBOL_UNKNOWN, ResolutionFlag.UNRESOLVED}

    early = _resolve(resolver, "Software firm rallies", ("VRCS",), before)
    assert early.instruments == (), "a ticker cannot be used before it existed"

    named = _resolve(resolver, "Vireo Cloud Software raises outlook", ("VIRE",), after)
    assert _links(named) == {"VRCS": 0.75}, "name evidence survives; the current ticker is shown"


# ---------------------------------------------------------------------- dedup


@pytest.fixture(params=["memory", "sql"])
async def dedup(request: pytest.FixtureRequest) -> DedupStore:
    if request.param == "memory":
        return InMemoryDedupStore()
    store = SqlDedupStore(make_engine("sqlite+aiosqlite://"))
    await store.create_schema()
    return store


async def test_first_claimant_is_canonical_and_claims_are_idempotent(dedup: DedupStore) -> None:
    assert await dedup.claim("f" * 64, "first", T0) == "first"
    assert await dedup.claim("f" * 64, "second", T0 + timedelta(minutes=1)) == "first"
    assert await dedup.claim("f" * 64, "first", T0) == "first", "redelivery stays canonical"
    assert await dedup.claim("0" * 64, "second", T0) == "second"


# ------------------------------------------------------------------ processor


@pytest.fixture
def preprocessor(resolver: EntityResolver) -> Preprocessor:
    return Preprocessor(
        resolver=resolver, dedup=InMemoryDedupStore(), stage_allowance=timedelta(seconds=1)
    )


async def test_normalize_carries_provenance_and_advances_availability(
    preprocessor: Preprocessor,
) -> None:
    raw = make_raw(headline="<b>Northwind Semiconductor</b> beats estimates", body="  ")
    event = await preprocessor.normalize(raw)

    assert event.headline == "Northwind Semiconductor beats estimates"
    assert event.body is None
    assert event.available_at == raw.available_at + timedelta(seconds=1)
    assert (event.published_at, event.first_seen_at, event.ingested_at) == (
        raw.published_at,
        raw.first_seen_at,
        raw.ingested_at,
    )
    assert (event.raw_event_id, event.trace_id) == (raw.event_id, raw.trace_id)
    assert (event.source_url, event.payload_ref, event.source_license) == (
        raw.source_url,
        raw.payload_ref,
        raw.source_license,
    )
    assert event.duplicate_of is None
    assert [ref.symbol for ref in event.instruments] == ["NWSC"]


async def test_same_content_from_another_source_is_a_duplicate(
    preprocessor: Preprocessor,
) -> None:
    original = await preprocessor.normalize(make_raw(source_event_id="a", source="wire_a"))
    copy = await preprocessor.normalize(
        make_raw(
            source_event_id="b",
            source="wire_b",
            headline="NORTHWIND SEMICONDUCTOR   beats estimates",
            first_seen_at=T0 + timedelta(minutes=2),
        )
    )
    assert copy.duplicate_of == original.event_id
    assert copy.cluster_id == original.cluster_id
    assert copy.event_id != original.event_id, "the copy is kept as its own provenance record"

    again = await preprocessor.normalize(make_raw(source_event_id="a", source="wire_a"))
    assert again == original, "reprocessing the original must not turn it into a duplicate"


async def test_headline_that_is_only_markup_is_rejected(preprocessor: Preprocessor) -> None:
    with pytest.raises(RejectMessage, match="empty after removing markup"):
        await preprocessor.normalize(make_raw(headline="<script>x()</script>\u200b"))


async def test_missing_language_is_marked_undetermined(preprocessor: Preprocessor) -> None:
    assert (await preprocessor.normalize(make_raw(language=None))).language == "und"
    assert (await preprocessor.normalize(make_raw(language=" EN "))).language == "en"
