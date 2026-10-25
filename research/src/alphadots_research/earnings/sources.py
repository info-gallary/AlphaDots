"""Source adapters: SEC EDGAR, Yahoo Finance daily bars, Kenneth French factors, and the
Loughran-McDonald dictionary.

Each source has a pure ``parse_*`` function (bytes in, typed values out, tested on
fixtures) and a thin fetch wrapper that goes through :class:`PoliteClient`.
"""

from __future__ import annotations

import csv
import html
import io
import json
import re
import zipfile
from dataclasses import dataclass
from datetime import UTC, date, datetime
from html.parser import HTMLParser
from zoneinfo import ZoneInfo

from alphadots_research.earnings.http import PoliteClient

NEW_YORK = ZoneInfo("America/New_York")
SEC_DATA = "https://data.sec.gov"
SEC_WWW = "https://www.sec.gov"
YAHOO = "https://query1.finance.yahoo.com"
FRENCH = "https://mba.tuck.dartmouth.edu/pages/faculty/ken.french/ftp"
FF5_DAILY = f"{FRENCH}/F-F_Research_Data_5_Factors_2x3_daily_CSV.zip"
MOM_DAILY = f"{FRENCH}/F-F_Momentum_Factor_daily_CSV.zip"
LM_DICTIONARY = "https://drive.google.com/uc?export=download&id=1iq2RUf8qGFEAk1g8wQntP3habOnR3fXF"
"""Loughran-McDonald Master Dictionary 1993-2025 (CSV), as linked from sraf.nd.edu."""

PRICE_START = datetime(2014, 1, 1, tzinfo=UTC)
PRICE_END = datetime(2026, 9, 1, tzinfo=UTC)
"""Fixed so the request URL, and therefore the cache key, never changes between runs."""

# ---------------------------------------------------------------------------- EDGAR


@dataclass(frozen=True, slots=True)
class Filing:
    cik: int
    accession: str
    form: str
    filing_date: date
    accepted_at: datetime
    """EDGAR acceptance time, UTC (verified: Apple's 16:30 New York releases show 20:30Z
    in summer and 21:30Z in winter)."""
    items: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class FloatFact:
    cik: int
    name: str
    end: date
    value: float


def parse_submissions(payload: bytes, cik: int) -> tuple[list[Filing], list[dict[str, str]]]:
    """Filings from a submissions document, plus the list of older pages to fetch.

    The main document keeps columns under ``filings.recent``; older pages hold the same
    columns at the top level.
    """
    data = json.loads(payload)
    columns = data["filings"]["recent"] if "filings" in data else data
    files = data["filings"].get("files", []) if "filings" in data else []
    filings = []
    for i, accession in enumerate(columns["accessionNumber"]):
        accepted = columns["acceptanceDateTime"][i]
        if not accepted:
            continue
        filings.append(
            Filing(
                cik=cik,
                accession=accession,
                form=columns["form"][i],
                filing_date=date.fromisoformat(columns["filingDate"][i]),
                accepted_at=datetime.fromisoformat(accepted).astimezone(UTC),
                items=tuple(item for item in columns["items"][i].split(",") if item),
            )
        )
    return filings, files


def submissions(client: PoliteClient, cik: int, since: date) -> list[Filing]:
    """Every filing for ``cik``, fetching older pages only if they reach back past ``since``."""
    body = client.get(f"{SEC_DATA}/submissions/CIK{cik:010d}.json")
    if body is None:
        return []
    filings, files = parse_submissions(body, cik)
    for page in files:
        if date.fromisoformat(page["filingTo"]) < since:
            continue
        older = client.get(f"{SEC_DATA}/submissions/{page['name']}")
        if older is not None:
            filings.extend(parse_submissions(older, cik)[0])
    return filings


def parse_frames(payload: bytes) -> list[FloatFact]:
    data = json.loads(payload)
    return [
        FloatFact(
            cik=int(row["cik"]),
            name=row["entityName"],
            end=date.fromisoformat(row["end"]),
            value=float(row["val"]),
        )
        for row in data["data"]
    ]


def frames(client: PoliteClient, taxonomy: str, concept: str, period: str) -> list[FloatFact]:
    body = client.get(f"{SEC_DATA}/api/xbrl/frames/{taxonomy}/{concept}/USD/{period}.json")
    return [] if body is None else parse_frames(body)


def parse_company_tickers(payload: bytes) -> dict[int, str]:
    """CIK to its first-listed current ticker."""
    tickers: dict[int, str] = {}
    for row in json.loads(payload).values():
        tickers.setdefault(int(row["cik_str"]), str(row["ticker"]))
    return tickers


def company_tickers(client: PoliteClient) -> dict[int, str]:
    body = client.get(f"{SEC_WWW}/files/company_tickers.json")
    return {} if body is None else parse_company_tickers(body)


def index_url(filing: Filing) -> str:
    folder = filing.accession.replace("-", "")
    return f"{SEC_WWW}/Archives/edgar/data/{filing.cik}/{folder}/{filing.accession}-index.htm"


_ROW = re.compile(r"<tr[^>]*>(.*?)</tr>", re.S | re.I)
_CELL = re.compile(r"<td[^>]*>(.*?)</td>", re.S | re.I)
_HREF = re.compile(r'href="([^"]+)"', re.I)
_TAG = re.compile(r"<[^>]+>")
_TEXT_DOCUMENT = re.compile(r"\.(htm|html|txt)$", re.I)


def parse_press_release_href(index_html: str) -> str | None:
    """Link to the first ``EX-99*`` exhibit (HTML or text) in a filing index page."""
    for row in _ROW.findall(index_html):
        cells = [html.unescape(_TAG.sub("", cell)).strip() for cell in _CELL.findall(row)]
        hrefs = _HREF.findall(row)
        if len(cells) < 4 or not hrefs:
            continue
        doc_type = cells[3].upper()
        href = hrefs[0].removeprefix("/ix?doc=")
        if doc_type.startswith("EX-99") and _TEXT_DOCUMENT.search(href):
            return f"{SEC_WWW}{href}" if href.startswith("/") else href
    return None


class _NarrativeText(HTMLParser):
    """Visible text outside tables: the release's words, not its financial statements."""

    SKIP = frozenset({"table", "script", "style", "head", "title"})
    BLOCK = frozenset({"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6"})

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip_depth = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in self.SKIP:
            self._skip_depth += 1
        elif tag in self.BLOCK:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in self.SKIP and self._skip_depth:
            self._skip_depth -= 1

    def handle_data(self, data: str) -> None:
        if not self._skip_depth:
            self.parts.append(data)


def narrative_text(document: bytes) -> str:
    """Plain text of an exhibit, with tables removed from HTML documents."""
    raw = document.decode("utf-8", errors="replace")
    if "<html" not in raw[:2000].lower() and "<body" not in raw[:5000].lower():
        return _TAG.sub(" ", raw)
    parser = _NarrativeText()
    parser.feed(raw)
    parser.close()
    return "".join(parser.parts)


# ---------------------------------------------------------------------------- prices


@dataclass(frozen=True, slots=True)
class Bars:
    ticker: str
    dates: tuple[date, ...]
    """New York trading dates."""
    adjclose: tuple[float | None, ...]
    close: tuple[float | None, ...]
    volume: tuple[float | None, ...]


def yahoo_url(ticker: str) -> str:
    start, end = int(PRICE_START.timestamp()), int(PRICE_END.timestamp())
    return (
        f"{YAHOO}/v8/finance/chart/{ticker}?period1={start}&period2={end}"
        "&interval=1d&events=div%2Csplit&includeAdjustedClose=true"
    )


def parse_chart(payload: bytes, ticker: str) -> Bars | None:
    chart = json.loads(payload)["chart"]
    if chart.get("error") or not chart.get("result"):
        return None
    result = chart["result"][0]
    stamps = result.get("timestamp") or []
    if not stamps:
        return None
    quote = result["indicators"]["quote"][0]
    adjclose = result["indicators"].get("adjclose", [{}])[0].get("adjclose") or quote["close"]
    dates = tuple(datetime.fromtimestamp(ts, UTC).astimezone(NEW_YORK).date() for ts in stamps)
    return Bars(
        ticker=ticker,
        dates=dates,
        adjclose=tuple(adjclose),
        close=tuple(quote["close"]),
        volume=tuple(None if v is None else float(v) for v in quote["volume"]),
    )


def daily_bars(client: PoliteClient, ticker: str) -> Bars | None:
    body = client.get(yahoo_url(ticker))
    return None if body is None else parse_chart(body, ticker)


# ---------------------------------------------------------------------------- factors


@dataclass(frozen=True, slots=True)
class FactorTable:
    names: tuple[str, ...]
    rows: dict[date, tuple[float, ...]]
    """Daily returns as fractions (the source publishes percentages)."""


def parse_french_daily(zipped: bytes) -> FactorTable:
    with zipfile.ZipFile(io.BytesIO(zipped)) as archive:
        text = archive.read(archive.namelist()[0]).decode("latin-1")
    names: tuple[str, ...] = ()
    rows: dict[date, tuple[float, ...]] = {}
    for line in text.splitlines():
        cells = [cell.strip() for cell in line.split(",")]
        if not names:
            if len(cells) > 1 and cells[0] == "" and cells[1]:
                names = tuple(cells[1:])
            continue
        if len(cells) != len(names) + 1 or not cells[0].isdigit():
            if rows:
                break
            continue
        day = date(int(cells[0][:4]), int(cells[0][4:6]), int(cells[0][6:]))
        rows[day] = tuple(float(cell) / 100.0 for cell in cells[1:])
    return FactorTable(names=names, rows=rows)


def french_daily(client: PoliteClient, url: str) -> FactorTable:
    body = client.get(url)
    if body is None:
        raise LookupError(url)
    return parse_french_daily(body)


# ---------------------------------------------------------------------------- dictionary


@dataclass(frozen=True, slots=True)
class Dictionary:
    positive: frozenset[str]
    negative: frozenset[str]


def parse_lm_dictionary(payload: bytes) -> Dictionary:
    """Words whose Positive/Negative column holds the year added (removed words are < 0)."""
    reader = csv.DictReader(io.StringIO(payload.decode("utf-8-sig")))
    positive, negative = set(), set()
    for row in reader:
        word = row["Word"].strip().upper()
        if float(row["Positive"]) > 0:
            positive.add(word)
        if float(row["Negative"]) > 0:
            negative.add(word)
    return Dictionary(positive=frozenset(positive), negative=frozenset(negative))


def lm_dictionary(client: PoliteClient) -> Dictionary:
    body = client.get(LM_DICTIONARY)
    if body is None:
        raise LookupError(LM_DICTIONARY)
    return parse_lm_dictionary(body)
