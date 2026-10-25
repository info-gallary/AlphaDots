"""Source parsers, on small hand-built payloads shaped like the real responses."""

from __future__ import annotations

import io
import json
import zipfile
from datetime import UTC, date, datetime

from alphadots_research.earnings.sources import (
    narrative_text,
    parse_chart,
    parse_company_tickers,
    parse_french_daily,
    parse_lm_dictionary,
    parse_press_release_href,
    parse_submissions,
)

COLUMNS = {
    "accessionNumber": ["0000320193-24-000120", "0000320193-24-000121", "0000320193-24-000122"],
    "filingDate": ["2024-10-31", "2024-11-01", "2024-11-02"],
    "acceptanceDateTime": ["2024-10-31T20:30:25.000Z", "", "2024-11-02T12:00:00.000Z"],
    "form": ["8-K", "4", "8-K/A"],
    "items": ["2.02,9.01", "", "2.02"],
}


def test_submissions_read_recent_columns_and_list_older_pages() -> None:
    payload = {
        "filings": {
            "recent": COLUMNS,
            "files": [{"name": "CIK0000320193-submissions-001.json", "filingTo": "2015-10-01"}],
        }
    }
    filings, files = parse_submissions(json.dumps(payload).encode(), 320193)
    assert [f.accession for f in filings] == ["0000320193-24-000120", "0000320193-24-000122"]
    first = filings[0]
    assert first.accepted_at == datetime(2024, 10, 31, 20, 30, 25, tzinfo=UTC)
    assert first.items == ("2.02", "9.01")
    assert first.filing_date == date(2024, 10, 31)
    assert files[0]["filingTo"] == "2015-10-01"


def test_older_submission_pages_hold_columns_at_the_top_level() -> None:
    filings, files = parse_submissions(json.dumps(COLUMNS).encode(), 320193)
    assert len(filings) == 2
    assert files == []


INDEX = """
<table class="tableFile">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>1</td><td>8-K</td><td><a href="/ix?doc=/Archives/edgar/data/1/2/main.htm">main.htm</a></td>
    <td>8-K</td><td>40452</td></tr>
<tr><td>2</td><td>PDF copy</td><td><a href="/Archives/edgar/data/1/2/ex991.pdf">ex991.pdf</a></td>
    <td>EX-99.1</td><td>1</td></tr>
<tr><td>3</td><td>PRESS RELEASE</td>
    <td><a href="/Archives/edgar/data/1/2/ex991.htm">ex991.htm</a></td>
    <td>EX-99.1</td><td>198391</td></tr>
</table>
"""


def test_press_release_is_the_first_html_or_text_ex99() -> None:
    assert (
        parse_press_release_href(INDEX) == "https://www.sec.gov/Archives/edgar/data/1/2/ex991.htm"
    )
    assert parse_press_release_href(INDEX.replace("EX-99.1", "EX-10.1")) is None


def test_narrative_text_drops_tables_scripts_and_styles() -> None:
    document = (
        b"<html><head><style>p{}</style></head><body><p>Revenue grew strongly.</p>"
        b"<table><tr><td>Net loss</td><td>(5)</td></tr></table><script>var x;</script>"
        b"<p>Outlook &amp; guidance raised.</p></body></html>"
    )
    text = narrative_text(document)
    assert "Revenue grew strongly." in text
    assert "Outlook & guidance raised." in text
    assert "Net loss" not in text
    assert "var x" not in text


def test_plain_text_exhibits_are_kept_whole() -> None:
    assert narrative_text(b"<DOCUMENT>\nRecord quarter.\n").strip().endswith("Record quarter.")


def test_chart_dates_are_new_york_trading_dates() -> None:
    # 2024-01-02 14:30 UTC is 09:30 in New York; 2024-07-01 13:30 UTC is 09:30 EDT.
    stamps = [
        int(datetime(2024, 1, 2, 14, 30, tzinfo=UTC).timestamp()),
        int(datetime(2024, 7, 1, 13, 30, tzinfo=UTC).timestamp()),
    ]
    payload = {
        "chart": {
            "result": [
                {
                    "timestamp": stamps,
                    "indicators": {
                        "quote": [{"close": [10.0, None], "volume": [100, None]}],
                        "adjclose": [{"adjclose": [9.5, None]}],
                    },
                }
            ],
            "error": None,
        }
    }
    bars = parse_chart(json.dumps(payload).encode(), "ABC")
    assert bars is not None
    assert bars.dates == (date(2024, 1, 2), date(2024, 7, 1))
    assert bars.adjclose == (9.5, None)


def test_chart_for_an_unknown_symbol_is_none() -> None:
    payload = {"chart": {"result": None, "error": {"code": "Not Found"}}}
    assert parse_chart(json.dumps(payload).encode(), "CELG") is None


def test_french_daily_file_parses_percentages_and_stops_at_the_footer() -> None:
    text = (
        "This file was created by using the 202608 CRSP database.\n\n"
        ",Mkt-RF,SMB,HML,RMW,CMA,RF\n"
        "20240102,   -0.67,    0.10,   -0.33,   -0.01,    0.16,    0.02\n"
        "20240103,    1.00,    0.00,    0.00,    0.00,    0.00,    0.02\n"
        "\nCopyright 2026 Eugene F. Fama and Kenneth R. French\n"
    )
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("F-F_daily.csv", text)
    table = parse_french_daily(buffer.getvalue())
    assert table.names == ("Mkt-RF", "SMB", "HML", "RMW", "CMA", "RF")
    assert table.rows[date(2024, 1, 2)][0] == -0.0067
    assert len(table.rows) == 2


def test_lm_dictionary_membership_is_a_positive_year() -> None:
    csv_text = "Word,Negative,Positive\nLOSS,2009,0\nGAIN,0,2009\nREMOVED,-2020,0\nTABLE,0,0\n"
    dictionary = parse_lm_dictionary(csv_text.encode())
    assert dictionary.negative == frozenset({"LOSS"})
    assert dictionary.positive == frozenset({"GAIN"})


def test_company_tickers_keep_the_first_listing() -> None:
    payload = {
        "0": {"cik_str": 1652044, "ticker": "GOOGL", "title": "Alphabet"},
        "1": {"cik_str": 1652044, "ticker": "GOOG", "title": "Alphabet"},
    }
    assert parse_company_tickers(json.dumps(payload).encode()) == {1652044: "GOOGL"}
