"""``load`` end to end on a fabricated download cache: the offline path the real study
takes, from cached EDGAR, Yahoo, Fama-French and dictionary responses to events."""

from __future__ import annotations

import gzip
import io
import json
import zipfile
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from alphadots_research.earnings.http import PoliteClient
from alphadots_research.earnings.sources import (
    FF5_DAILY,
    LM_DICTIONARY,
    MOM_DAILY,
    SEC_DATA,
    SEC_WWW,
    yahoo_url,
)
from alphadots_research.earnings.study import load

PRICED, UNPRICED, BROKEN = 1001, 1002, 1003
WORDS = " ".join(["revenue"] * 120)


def business_days(start: date, end: date) -> list[date]:
    days, day = [], start
    while day <= end:
        if day.weekday() < 5:
            days.append(day)
        day += timedelta(days=1)
    return days


DAYS = business_days(date(2014, 1, 1), date(2016, 12, 30))


def french_zip(header: str, width: int) -> bytes:
    lines = ["Fabricated for a test", "", header]
    lines += [f"{d:%Y%m%d}," + ",".join(["0.01"] * width) for d in DAYS]
    lines += ["", "Copyright"]
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("daily.csv", "\n".join(lines))
    return buffer.getvalue()


def frame(rows: list[tuple[int, str, float]]) -> bytes:
    data = [
        {"accn": "x", "cik": c, "entityName": n, "loc": "US", "end": "2014-06-30", "val": v}
        for c, n, v in rows
    ]
    return json.dumps({"data": data}).encode()


RELEASES = [
    # accession, accepted (UTC), items, form, tone words
    ("0001-15-000001", datetime(2015, 4, 21, 20, 30, tzinfo=UTC), "2.02,9.01", "8-K", "strong"),
    ("0001-15-000002", datetime(2015, 7, 21, 11, 0, tzinfo=UTC), "2.02", "8-K", "strong"),
    ("0001-15-000003", datetime(2015, 7, 23, 11, 0, tzinfo=UTC), "2.02", "8-K", "strong"),
    ("0001-15-000004", datetime(2015, 10, 20, 20, 30, tzinfo=UTC), "2.02", "8-K", "loss"),
    ("0001-15-000005", datetime(2015, 10, 21, 20, 30, tzinfo=UTC), "2.02", "8-K/A", "loss"),
    ("0001-16-000006", datetime(2016, 1, 26, 21, 30, tzinfo=UTC), "5.02", "8-K", "loss"),
]


def submissions(cik: int) -> bytes:
    columns = {
        "accessionNumber": [r[0] for r in RELEASES],
        "filingDate": [r[1].astimezone(UTC).date().isoformat() for r in RELEASES],
        "acceptanceDateTime": [r[1].isoformat().replace("+00:00", ".000Z") for r in RELEASES],
        "form": [r[3] for r in RELEASES],
        "items": [r[2] for r in RELEASES],
    }
    return json.dumps({"cik": cik, "filings": {"recent": columns, "files": []}}).encode()


def index_page(cik: int, accession: str) -> bytes:
    href = f"/Archives/edgar/data/{cik}/{accession.replace('-', '')}/ex991.htm"
    return (
        f'<table><tr><td>1</td><td>PRESS RELEASE</td><td><a href="{href}">ex991.htm</a></td>'
        f"<td>EX-99.1</td><td>1</td></tr></table>"
    ).encode()


def chart() -> bytes:
    stamps = [int(datetime.combine(d, datetime.min.time(), UTC).timestamp()) + 52200 for d in DAYS]
    closes = [100.0 + i * 0.1 for i in range(len(DAYS))]
    payload = {
        "chart": {
            "error": None,
            "result": [
                {
                    "timestamp": stamps,
                    "indicators": {
                        "quote": [{"close": closes, "volume": [1.0] * len(DAYS)}],
                        "adjclose": [{"adjclose": closes}],
                    },
                }
            ],
        }
    }
    return json.dumps(payload).encode()


@pytest.fixture
def cache(tmp_path: Path) -> Path:
    client = PoliteClient(tmp_path, "test", {}, offline=True)
    responses: dict[str, bytes] = {
        FF5_DAILY: french_zip(",Mkt-RF,SMB,HML,RMW,CMA,RF", 6),
        MOM_DAILY: french_zip(",Mom", 1),
        LM_DICTIONARY: b"Word,Negative,Positive\nSTRONG,0,2009\nLOSS,2009,0\n",
        f"{SEC_WWW}/files/company_tickers.json": json.dumps(
            {"0": {"cik_str": PRICED, "ticker": "AAA", "title": "Priced Co"}}
        ).encode(),
        yahoo_url("AAA"): chart(),
    }
    for period in ("CY2014Q1I", "CY2014Q2I", "CY2014Q3I", "CY2014Q4I"):
        floats = [
            (PRICED, "Priced Co", 5e9),
            (UNPRICED, "Gone Co", 4e9),
            (BROKEN, "Units Co", 9e14),
        ]
        assets = [(PRICED, "Priced Co", 1e9), (UNPRICED, "Gone Co", 1e9), (BROKEN, "Units Co", 1e9)]
        responses[f"{SEC_DATA}/api/xbrl/frames/dei/EntityPublicFloat/USD/{period}.json"] = frame(
            floats
        )
        responses[f"{SEC_DATA}/api/xbrl/frames/us-gaap/Assets/USD/{period}.json"] = frame(assets)
    for cik in (PRICED, UNPRICED):
        responses[f"{SEC_DATA}/submissions/CIK{cik:010d}.json"] = submissions(cik)
        for accession, _, _, _, word in RELEASES:
            folder = f"{SEC_WWW}/Archives/edgar/data/{cik}/{accession.replace('-', '')}"
            responses[f"{folder}/{accession}-index.htm"] = index_page(cik, accession)
            responses[f"{folder}/ex991.htm"] = (
                f"<html><body><p>{word} {WORDS}</p></body></html>".encode()
            )
    for url, body in responses.items():
        path = client.path_for(url)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(gzip.compress(body))
    return tmp_path


def test_load_builds_timed_scored_events_from_the_cache(cache: Path, tmp_path: Path) -> None:
    sec = PoliteClient(cache, "", {}, offline=True)
    web = PoliteClient(cache, "", {}, offline=True)
    data = load(sec, web, tmp_path / "derived", workers=2)

    assert [r.reason for r in data.rejected] == ["float above USD 1 trillion"]
    assert data.unpriced == ["Gone Co"]
    counts = {row["step"]: row["count"] for row in data.funnel}
    assert counts["Largest US filers by 2014 public float"] == 2
    assert counts["With Yahoo price history"] == 1
    # Per firm: 4 earnings 8-Ks (the 8-K/A and the Item 5.02 filing are not), one of them
    # two days after another, so 3 unique events.
    assert counts["Earnings 8-Ks (Item 2.02), 2015-2025"] == 8
    assert counts["After removing repeat releases"] == 6

    priced = sorted((e for e in data.events if e.firm == 0), key=lambda e: e.accepted_at)
    days = data.calendar.days
    assert [days[e.t0] for e in priced] == [
        date(2015, 4, 22),
        date(2015, 7, 21),
        date(2015, 10, 21),
    ]
    assert [days[e.naive] for e in priced] == [
        date(2015, 4, 21),
        date(2015, 7, 21),
        date(2015, 10, 20),
    ]
    assert [e.session for e in priced] == ["after close", "pre-market", "after close"]
    assert priced[2].lm_surprise == pytest.approx(-1.0 - 1.0)
    assert all(e.firm == -1 for e in data.events if e.cik == UNPRICED)

    # Text features are cached by accession number, so a second load parses nothing.
    assert (tmp_path / "derived" / "tone-v1.json").exists()
    again = load(sec, web, tmp_path / "derived", workers=1)
    assert len(again.events) == len(data.events)
