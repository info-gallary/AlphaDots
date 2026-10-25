"""The real dataset: how headlines are pulled out of filings, and that what is committed
under ``data/real`` is intact and well-formed."""

from __future__ import annotations

import json
from datetime import UTC, date, datetime

from alphadots_common.reference import InstrumentMaster
from alphadots_ingestion.adapters import FixtureNewsItem
from alphadots_research.earnings.sources import Filing
from scripts.capture_edgar import (
    NEWS_FILE,
    OUT,
    display_name,
    excerpt,
    item_section,
    items_headline,
    title_of,
)
from scripts.generate_fixtures import normalized_sha256


def test_press_release_title_skips_labels_datelines_and_contacts() -> None:
    text = "\n".join(
        [
            "EX-99.1",
            "Exhibit 99.1",
            "FOR IMMEDIATE RELEASE",
            "October 30, 2026",
            "Contacts:",
            "Apple reports fourth quarter results",
            "CUPERTINO, CALIFORNIA - Apple today announced financial results.",
        ]
    )
    assert title_of(text) == "Apple reports fourth quarter results"


def test_exchange_letter_subject_wins_and_wrapped_subjects_are_joined() -> None:
    text = (
        "National Stock Exchange of India Ltd.\nDear Sir,\nSub: Superannuation of\nMr. A. Kumar\n"
    )
    assert title_of(text) == "Superannuation of Mr. A. Kumar"
    # "Sub-groups" is not a subject line, so nothing is cut off the front of it.
    assert title_of("Sub-groups of the G-20 met.\n") == "Sub-groups of the G-20 met."


APOS = "\N{RIGHT SINGLE QUOTATION MARK}"
OPEN_QUOTE, CLOSE_QUOTE = "\N{LEFT DOUBLE QUOTATION MARK}", "\N{RIGHT DOUBLE QUOTATION MARK}"


def test_title_quoted_in_a_covering_letter() -> None:
    title = f"Dr. Reddy{APOS}s receives approval for a new oncology drug"
    text = (
        "Dear Sir/Madam,\n"
        f"Please find enclosed a press release titled {OPEN_QUOTE}{title}{CLOSE_QUOTE} "
        "for your records.\n"
    )
    assert title_of(text) == title


def test_cover_pages_addresses_and_names_are_not_titles() -> None:
    text = "\n".join(
        [
            "SECURITIES AND EXCHANGE COMMISSION",
            "(Address of principal executive office)",
            "2200 Mission College Blvd.",
            "Bandra-Kurla Complex",
            "Hyderabad, Telangana 500 034, India",
            f"Dr. Reddy{APOS}s Laboratories Limited",
            "Unaudited Financial Results for the quarter and half-year ended",
            "September 30, 2026",
        ]
    )
    assert (
        title_of(text)
        == "Unaudited Financial Results for the quarter and half-year ended September 30, 2026"
    )


def test_split_names_rejoin_and_capitals_keep_acronyms() -> None:
    assert (
        title_of("Caterpillar\nInc. Increases Dividend\n") == "Caterpillar Inc. Increases Dividend"
    )
    assert title_of("IBM RELEASES THIRD-QUARTER RESULTS\n") == "IBM Releases Third-quarter Results"
    assert (
        title_of("PepsiCo Reports First-Quarter 2026 Results1\n")
        == "PepsiCo Reports First-Quarter 2026 Results"
    )


def test_excerpt_starts_after_the_title_and_ends_on_a_sentence() -> None:
    text = "Big News Headline Here\nFirst sentence is here. Second one runs on and on."
    assert excerpt(text, after="Big News Headline Here", limit=30) == "First sentence is here."


def test_item_section_drops_the_cover_page_and_stops_at_the_next_item() -> None:
    text = (
        "FORM 8-K CURRENT REPORT ... Item 5.02 Departure of Directors. On May 1 the board "
        "appointed Jane Doe. Item 9.01 Financial Statements and Exhibits."
    )
    assert item_section(text).startswith("Departure of Directors. On May 1 the board appointed")
    assert "9.01" not in item_section(text)


def test_items_headline_describes_what_the_8k_reports() -> None:
    when = datetime(2026, 5, 1, 20, 0, tzinfo=UTC)
    eight_k = Filing(1, "a", "8-K", date(2026, 5, 1), when, ("5.02", "9.01"))
    assert items_headline("Apple", eight_k) == "Apple: Director or officer departure or appointment"
    six_k = Filing(1, "b", "6-K", date(2026, 5, 1), when, ())
    assert items_headline("Infosys", six_k) == "Infosys: report to the SEC (6-K)"


def test_committed_real_dataset_is_pinned_and_well_formed() -> None:
    manifest = json.loads((OUT / "MANIFEST.json").read_text(encoding="utf-8"))
    for name, entry in manifest["files"].items():
        assert normalized_sha256(OUT / name) == entry["sha256"], f"{name} changed; run make capture"

    master = InstrumentMaster.load(OUT / "instruments.json")
    window_end = date.fromisoformat(manifest["window"][1])
    symbols = {item.symbol_on(window_end) for item in master.all()}
    lines = [
        FixtureNewsItem.model_validate_json(line)
        for line in (OUT / NEWS_FILE).read_text(encoding="utf-8").splitlines()
        if line.startswith("{")
    ]
    assert len(lines) > 400
    assert [item.first_seen_at for item in lines] == sorted(item.first_seen_at for item in lines)
    for item in lines:
        assert item.source == "sec_edgar"
        assert item.source_url.startswith("https://www.sec.gov/Archives/edgar/data/")
        assert item.first_seen_at == item.published_at  # backfill: seen when EDGAR accepted it
        assert set(item.provider_symbols) <= symbols
    assert {item.provider_symbols[0] for item in lines} == symbols  # every issuer has news


def test_display_names_are_readable() -> None:
    assert display_name("BANK OF AMERICA CORP /DE/") == "Bank of America Corp"
    assert display_name("COSTCO WHOLESALE CORP /NEW") == "Costco Wholesale Corp"
    assert display_name("JPMORGAN CHASE & CO") == "JPMorgan Chase & Co"
    assert display_name("HDFC BANK LTD") == "HDFC Bank Ltd"
    assert display_name("AMAZON COM INC") == "Amazon.com Inc"
    assert display_name("Apple Inc.") == "Apple Inc."
