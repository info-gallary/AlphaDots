"""Capture a real dataset from SEC EDGAR for the live pipeline and the dashboard.

    uv run python -m scripts.capture_edgar            # writes data/real/
    uv run python -m scripts.capture_edgar --check    # fail if data/real is stale

For each company on the watchlist, every 8-K (US filers) or 6-K (foreign private issuers,
here five Indian companies) accepted in the capture window becomes one news line: the
press release's own headline (or the 8-K items it reports), a short excerpt, the exact
EDGAR acceptance time, and a link to the filing on sec.gov. The output has the same shape
as the synthetic fixtures, so the unchanged pipeline replays it.

Requests go through the research package's polite, cached EDGAR client (declared
User-Agent from ``ALPHADOTS_SEC_USER_AGENT``, at most 8 a second); a re-run reads the
cache. The window is fixed, so the committed files only change when it is moved on.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from dataclasses import dataclass
from datetime import UTC, date, datetime
from pathlib import Path
from typing import Any

from alphadots_research.earnings.http import PoliteClient
from alphadots_research.earnings.sources import (
    SEC_DATA,
    Filing,
    index_url,
    narrative_text,
    parse_press_release_href,
    parse_submissions,
)
from scripts.fetch_earnings_data import ROOT, sec_client

OUT = ROOT / "data" / "real"
NEWS_FILE = Path("news/edgar.jsonl")
WINDOW = (date(2026, 1, 1), date(2026, 10, 23))
MAX_PER_ISSUER = 30
"""Most recent filings kept per company, so frequent filers do not crowd out the rest."""
EXCERPT_CHARS = 1200
SOURCE = "sec_edgar"
LICENSE = "sec_edgar_public_filing"


@dataclass(frozen=True, slots=True)
class Company:
    sec_ticker: str
    """Ticker in SEC's company_tickers.json (the US listing or ADR)."""
    aliases: tuple[str, ...]
    symbol: str = ""
    """Ticker shown in AlphaDots; defaults to ``sec_ticker``. NSE symbol for Indian firms."""
    instrument_id: str = ""
    """Defaults to ``SEC-<CIK>``; Indian firms keep their NSE- identifiers."""


US = [
    Company("AAPL", ("Apple",)),
    Company("MSFT", ("Microsoft",)),
    Company("NVDA", ("NVIDIA",)),
    Company("AMZN", ("Amazon.com", "Amazon")),
    Company("GOOGL", ("Alphabet",)),
    Company("META", ("Meta Platforms",)),
    Company("TSLA", ("Tesla",)),
    Company("AVGO", ("Broadcom",)),
    Company("JPM", ("JPMorgan Chase", "JPMorgan")),
    Company("V", ("Visa Inc",)),
    Company("MA", ("Mastercard",)),
    Company("UNH", ("UnitedHealth Group", "UnitedHealth")),
    Company("XOM", ("Exxon Mobil", "ExxonMobil")),
    Company("JNJ", ("Johnson & Johnson",)),
    Company("WMT", ("Walmart",)),
    Company("PG", ("Procter & Gamble",)),
    Company("HD", ("Home Depot",)),
    Company("COST", ("Costco",)),
    Company("BAC", ("Bank of America",)),
    Company("KO", ("Coca-Cola",)),
    Company("PEP", ("PepsiCo",)),
    Company("NFLX", ("Netflix",)),
    Company("ORCL", ("Oracle",)),
    Company("CRM", ("Salesforce",)),
    Company("AMD", ("Advanced Micro Devices",)),
    Company("INTC", ("Intel Corporation",)),
    Company("DIS", ("Walt Disney",)),
    Company("CSCO", ("Cisco",)),
    Company("MRK", ("Merck & Co",)),
    Company("PFE", ("Pfizer",)),
    Company("ABBV", ("AbbVie",)),
    Company("LLY", ("Eli Lilly",)),
    Company("CVX", ("Chevron",)),
    Company("NKE", ("Nike, Inc",)),
    Company("BA", ("Boeing",)),
    Company("CAT", ("Caterpillar",)),
    Company("GS", ("Goldman Sachs",)),
    Company("MS", ("Morgan Stanley",)),
    Company("IBM", ("International Business Machines", "IBM")),
    Company("QCOM", ("Qualcomm",)),
]

INDIA = [
    Company("INFY", ("Infosys",), "INFY", "NSE-0003"),
    Company("HDB", ("HDFC Bank",), "HDFCBANK", "NSE-0004"),
    Company("IBN", ("ICICI Bank",), "ICICIBANK", "NSE-0005"),
    Company("WIT", ("Wipro",), "WIPRO", "NSE-0007"),
    Company("RDY", ("Dr. Reddy's", "Dr Reddy's"), "DRREDDY", "NSE-0008"),
]

ITEMS = {
    "1.01": "Entry into a material definitive agreement",
    "1.02": "Termination of a material definitive agreement",
    "2.01": "Completion of an acquisition or disposition",
    "2.02": "Results of operations and financial condition",
    "2.03": "New direct financial obligation",
    "2.05": "Exit or disposal costs",
    "2.06": "Material impairment",
    "3.01": "Notice of delisting or failure to meet listing standards",
    "3.02": "Unregistered sale of equity securities",
    "3.03": "Material modification to shareholder rights",
    "4.01": "Change of certifying accountant",
    "5.01": "Change in control",
    "5.02": "Director or officer departure or appointment",
    "5.03": "Amendment to articles or bylaws",
    "5.07": "Shareholder vote results",
    "7.01": "Regulation FD disclosure",
    "8.01": "Other events",
}

_SKIP_LINE = re.compile(
    r"^(exhibit|ex-\d|press release|news release|for immediate release|media|investor|"
    r"contacts?\b|page \d|table of contents|forward[- ]looking|safe harbor|\(?in (millions|"
    r"thousands)|to,?$|dear (sir|madam))",
    re.IGNORECASE,
)
_BOILERPLATE = re.compile(
    r"securities and exchange commission|washington, d\.?c|form [68]-k|foreign private issuer|"
    r"pursuant to (rule|section)|commission file number|indicate by check mark|"
    r"name of registrant|securities exchange act|for the month of|current report|"
    r"stock exchange|scrip code|stock code|the manager|listing (department|centre|compliance)|"
    r"corporate relationship|translation of registrant|name into english|"
    r"for your (information|record)|take (the same|this|it) on record|thanking you|"
    r"yours (faithfully|sincerely|truly)|address of principal|act of 1934|"
    r"company secretary|compliance officer|regulation s-t|organized under|for the month|"
    r"indicate below|is marked|rule 12g3|"
    r"\b\d{3}\s?\d{3}\.?$|,\s*india\.?$|\.(htm|html|txt|pdf)$",
    re.IGNORECASE,
)
_ADDRESS = re.compile(
    r"^\d+[\w\s.,'-]*\b(blvd|boulevard|street|st|avenue|ave|road|rd|drive|dr|way|parkway|"
    r"plaza|floor|suite|lane)\b",
    re.IGNORECASE,
)
_SUBJECT = re.compile(r"^(?:sub(?:ject)?|re)\s*[:.]\s*(.{4,220})$", re.IGNORECASE)
DASHES = " -:\N{EN DASH}\N{EM DASH}"
_NAME_LINE = re.compile(
    r"^[\w.,'&\s" + chr(0x2019) + r"-]{0,80}\b"
    r"(limited|ltd\.?|inc\.?|incorporated|corporation|corp\.?|plc)$",
    re.IGNORECASE,
)
_PLACE_LINE = re.compile(
    r"^(\S+\s+){0,3}(complex|towers?|building|house|marg|nagar|road|centre|center|park)$",
    re.IGNORECASE,
)
_OPENER = re.compile(
    r"^(in continuation|pursuant to|please find|we (wish|would like) to|this is to|"
    r"further to|with reference to|in compliance with|as per|kindly|the board of directors|"
    r"a copy of|an audio recording|attached (herewith|is)|enclosed (herewith|is))",
    re.IGNORECASE,
)
_QUOTED = re.compile("[" + chr(0x201C) + '"](.{15,200}?)[' + chr(0x201D) + '"]')
_CONTINUES = frozenset(
    {"of", "and", "to", "for", "the", "in", "on", "with", "a", "an", "its", "&", "ended", "as"}
)
_LEADING_SUFFIX = re.compile(r"^(inc|corp|corporation|ltd|limited|co)\b\.?\s", re.IGNORECASE)
CASING = {
    "UNITEDHEALTH": "UnitedHealth",
    "REDDYS": "Reddy's",
    "DR": "Dr.",
    "COCA": "Coca",
    "JPMORGAN": "JPMorgan",
    "NVIDIA": "NVIDIA",
    "PEPSICO": "PepsiCo",
    "ABBVIE": "AbbVie",
}
QUOTES = "\"'" + chr(0x201C) + chr(0x2018)
_SMALL_WORDS = frozenset({"of", "and", "the", "for", "in", "on", "at", "to"})
_STATE_SUFFIX = re.compile(r"\s*/[A-Z]{2,3}/?$|\s*/NEW$", re.IGNORECASE)
_FOOTNOTE = re.compile(r"(?<=[a-z\)])\d{1,2}$")
ACRONYMS = frozenset(
    {
        "IBM",
        "FDA",
        "CCAR",
        "SEC",
        "INR",
        "USD",
        "IFRS",
        "AI",
        "CEO",
        "CFO",
        "COO",
        "NVIDIA",
        "AMD",
        "US",
        "USA",
        "U.S.",
        "HDFC",
        "ICICI",
        "NYSE",
        "NSE",
        "BSE",
        "ADR",
        "ADS",
        "FY",
        "Q1",
        "Q2",
        "Q3",
        "Q4",
        "AGM",
        "ESOP",
        "SEBI",
        "LLC",
        "LP",
        "UK",
        "EU",
        "IT",
        "GPU",
        "CPU",
        "R&D",
        "EPS",
        "GAAP",
    }
)
_DATE_LINE = re.compile(
    r"^(january|february|march|april|may|june|july|august|september|october|november|"
    r"december)\s+\d{1,2},\s+\d{4}$",
    re.IGNORECASE,
)
_ITEM_HEADING = re.compile(r"Item\s+(\d\.\d\d)", re.IGNORECASE)
_SENTENCE_END = re.compile(r"[.!?](?=\s|$)")


def clean(text: str) -> str:
    return " ".join(text.split())


def _tidy(line: str) -> str:
    line = _FOOTNOTE.sub("", line).strip(DASHES)
    if line.isupper():
        line = " ".join(
            CASING.get(word.strip(",.():;"), word)
            if word.strip(",.():;") in CASING
            else word
            if word.strip(",.():;") in ACRONYMS or any(char.isdigit() for char in word)
            else word.capitalize()
            for word in line.split()
        )
    return line


def _subject_title(lines: list[str]) -> str | None:
    """An exchange letter's subject line ("Sub: ..."), joined if it wraps."""
    for index, line in enumerate(lines):
        subject = _SUBJECT.match(line)
        if not subject:
            continue
        title = subject.group(1)
        for nxt in [n for n in lines[index + 1 : index + 4] if n][:2]:
            short = len(title.split()) < 5 and len(nxt) < 80 and not _OPENER.match(nxt)
            wrapped = title.split()[-1].lower() in _CONTINUES or nxt[0].islower() or short
            if not wrapped or len(title) + len(nxt) > 200:
                break
            title = f"{title} {nxt}"
        return title
    return None


def _quoted_title(lines: list[str]) -> str | None:
    """'Please find enclosed a press release titled "<headline>"'."""
    for line in lines:
        quoted = _QUOTED.search(line) if _OPENER.match(line) else None
        if quoted:
            return quoted.group(1)
    return None


def _could_be_title(line: str, before: str) -> bool:
    if not 20 <= len(line) <= 200 or line.endswith((":", ",")) or " " not in line:
        return False
    if line[0].islower() or not (line[0].isalnum() or line[0] in QUOTES) or _OPENER.match(line):
        return False  # lowercase start, "(", "*" footnotes, bullets, letter openers
    if before.endswith(",") or (
        len(before) > 80 and not before.endswith((".", ":", ";", "!", "?"))
    ):
        return False  # the middle of a sentence that started on the line before
    patterns = (_SKIP_LINE, _DATE_LINE, _ADDRESS, _NAME_LINE, _PLACE_LINE)
    if any(pattern.match(line) for pattern in patterns) or _BOILERPLATE.search(line):
        return False
    words = line.split()
    if sum(len(word) for word in words) / len(words) < 2.5:
        return False  # letter-spaced display text from slides: 'F S D ( S U P ...'
    return sum(char.isalpha() for char in line) >= 0.6 * len(line)


def _first_title_line(lines: list[str]) -> str | None:
    for index, line in enumerate(lines):
        before = next((prev for prev in reversed(lines[:index]) if prev), "")
        if not _could_be_title(line, before):
            continue
        title = line
        if _LEADING_SUFFIX.match(title) and before:
            title = f"{before} {title}"  # "Caterpillar" / "Inc. Increases Dividend"
        after = next((nxt for nxt in lines[index + 1 :] if nxt), "")
        if title.split()[-1].lower() in _CONTINUES and after and len(title) + len(after) < 240:
            title = f"{title} {after}"  # a headline wrapped over two lines
        return title
    return None


def title_of(text: str) -> str | None:
    """The document's own headline: an exchange letter's subject line, a title quoted in
    a covering letter, or else the first short, wordy line that is not boilerplate
    (exhibit labels, SEC cover pages, addressees, addresses, datelines, file names)."""
    lines = [clean(raw) for raw in text.splitlines()]
    title = _subject_title(lines) or _quoted_title(lines) or _first_title_line(lines)
    return None if title is None else _tidy(title)


def excerpt(text: str, after: str | None = None, limit: int = EXCERPT_CHARS) -> str:
    """Up to ``limit`` characters of running text, ending on a full sentence."""
    flat = clean(text)
    if after and after in flat:
        flat = flat[flat.index(after) + len(after) :].lstrip(DASHES)
    if len(flat) <= limit:
        return flat
    ends = [match.end() for match in _SENTENCE_END.finditer(flat, 0, limit)]
    return flat[: ends[-1]] if ends else flat[:limit].rsplit(" ", 1)[0] + " …"


def item_section(text: str) -> str:
    """Body of the first reported 8-K item other than 9.01 (exhibits), cover page dropped."""
    flat = clean(text)
    for match in _ITEM_HEADING.finditer(flat):
        if match.group(1) in ITEMS:
            body = flat[match.end() :]
            stop = re.search(r"Item\s+\d\.\d\d|SIGNATURES?\b", body)
            return (body[: stop.start()] if stop else body).lstrip(". ")
    return ""


def display_name(sec_name: str) -> str:
    """'BANK OF AMERICA CORP /DE/' -> 'Bank of America Corp'; mixed-case names unchanged."""
    name = _STATE_SUFFIX.sub("", sec_name).strip().replace("AMAZON COM", "Amazon.com")
    words = []
    for index, word in enumerate(name.split()):
        bare = word.strip(",.")
        if not bare.isupper() or len(bare) == 1:
            words.append(word)
        elif bare in CASING:
            words.append(word.replace(bare, CASING[bare]))
        elif bare in ACRONYMS:
            words.append(word)
        elif index and bare.lower() in _SMALL_WORDS:
            words.append(word.lower())
        else:
            words.append(word.capitalize())
    return " ".join(words)


def items_headline(name: str, filing: Filing) -> str:
    described = [ITEMS[item] for item in filing.items if item in ITEMS]
    if filing.form == "6-K" or not described:
        return f"{name}: report to the SEC ({filing.form})"
    return f"{name}: {'; '.join(described)}"


def short_name(company: Company, sec_name: str) -> str:
    return company.aliases[0] if company.aliases else sec_name


def news_line(client: PoliteClient, company: Company, name: str, filing: Filing) -> dict[str, Any]:
    """One fixture-shaped news item for a filing: its press release if it has one."""
    index = client.get(index_url(filing))
    href = None if index is None else parse_press_release_href(index.decode("utf-8", "replace"))
    headline = body = None
    if href:
        document = client.get(href)
        if document is not None:
            text = narrative_text(document)
            headline = title_of(text)
            body = excerpt(text, after=headline)
    if headline is None:
        primary = client.get(f"{filing.folder_url}/{filing.primary_document}")
        text = "" if primary is None else narrative_text(primary)
        # A 6-K's main document is usually the disclosure itself; an 8-K's is a cover page
        # plus item text, so its headline comes from the items it reports.
        own = title_of(text) if filing.form == "6-K" else None
        headline = own or items_headline(name, filing)
        body = excerpt(text, after=own) if own else excerpt(item_section(text) or text)
    names = (name, *company.aliases)
    if not any(alias.casefold() in headline.casefold() for alias in names):
        headline = f"{name}: {headline}"  # "Outcome of Board Meeting" does not say whose
    accepted = filing.accepted_at.astimezone(UTC).isoformat().replace("+00:00", "Z")
    return {
        "source": SOURCE,
        "source_event_id": filing.accession,
        "published_at": accepted,
        # Backfilled from the EDGAR archive: EDGAR disseminates a filing when it accepts
        # it, so the acceptance time is when it could first have been seen. A live poller
        # would record its own clock here instead.
        "first_seen_at": accepted,
        "headline": headline[:1000],
        "body": body or None,
        "language": "en",
        "provider_symbols": [company.symbol or company.sec_ticker],
        "source_url": index_url(filing),
        "source_license": LICENSE,
    }


def capture(client: PoliteClient) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    tickers_body = client.get("https://www.sec.gov/files/company_tickers.json")
    if tickers_body is None:
        raise LookupError("company_tickers.json")
    cik_by_ticker = {
        row["ticker"]: int(row["cik_str"]) for row in json.loads(tickers_body).values()
    }
    start, end = WINDOW
    instruments: list[dict[str, Any]] = []
    lines: list[dict[str, Any]] = []
    for company in [*US, *INDIA]:
        cik = cik_by_ticker[company.sec_ticker]
        body = client.get(f"{SEC_DATA}/submissions/CIK{cik:010d}.json")
        if body is None:
            raise LookupError(company.sec_ticker)
        profile = json.loads(body)
        filings, _ = parse_submissions(body, cik)
        chosen = [
            filing
            for filing in filings
            if filing.form in {"8-K", "6-K"}
            and start <= filing.accepted_at.astimezone(UTC).date() <= end
        ]
        chosen = sorted(chosen, key=lambda f: f.accepted_at)[-MAX_PER_ISSUER:]
        name = short_name(company, profile["name"])
        instruments.append(
            {
                "instrument_id": company.instrument_id or f"SEC-{cik:010d}",
                "name": display_name(profile["name"]),
                "sector": profile.get("sicDescription") or "Unclassified",
                "aliases": list(company.aliases),
                "listings": [
                    {
                        "symbol": company.symbol or company.sec_ticker,
                        "valid_from": start.isoformat(),
                        "valid_to": None,
                    }
                ],
                "cik": cik,
                "exchange": "NSE"
                if company.instrument_id.startswith("NSE-")
                else ",".join(profile.get("exchanges") or []),
            }
        )
        lines.extend(news_line(client, company, name, filing) for filing in chosen)
        print(f"{company.sec_ticker:6} {len(chosen):3} filings", flush=True)
    lines.sort(key=lambda line: (line["first_seen_at"], line["source_event_id"]))
    master = {
        "version": f"sec-edgar-{end.isoformat()}",
        "license": "Real issuers. Names, CIKs, SIC sectors and tickers from SEC EDGAR "
        "(public records); NSE symbols for the five Indian issuers that file with the SEC.",
        "note": "`valid_from` is the start of the capture window, not a listing date. "
        "Instrument IDs are SEC CIKs (SEC-) or this project's NSE- identifiers.",
        "instruments": instruments,
    }
    return master, lines


def manifest(files: dict[str, bytes]) -> dict[str, Any]:
    return {
        "generated_by": "scripts/capture_edgar.py",
        "window": [WINDOW[0].isoformat(), WINDOW[1].isoformat()],
        "files": {
            name: {"sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)}
            for name, data in sorted(files.items())
        },
    }


def render(master: dict[str, Any], lines: list[dict[str, Any]]) -> dict[str, bytes]:
    header = (
        "# AlphaDots real dataset: one line per SEC EDGAR 8-K/6-K filing, in arrival order.\n"
        f"# Window {WINDOW[0]} to {WINDOW[1]}. Headlines and excerpts are quoted from the "
        "filings; source_url links to each one on sec.gov.\n"
    )
    news = header + "".join(json.dumps(line, ensure_ascii=False) + "\n" for line in lines)
    files = {
        "instruments.json": (json.dumps(master, indent=1, ensure_ascii=False) + "\n").encode(),
        NEWS_FILE.as_posix(): news.encode(),
    }
    files["MANIFEST.json"] = (json.dumps(manifest(files), indent=1) + "\n").encode()
    return files


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify instead of writing")
    parser.add_argument("--offline", action="store_true", help="read only the download cache")
    args = parser.parse_args()
    with sec_client(offline=args.offline or args.check) as client:
        files = render(*capture(client))
    if args.check:
        stale = [
            name
            for name, data in files.items()
            if not (OUT / name).exists()
            or (OUT / name).read_bytes().replace(b"\r\n", b"\n") != data
        ]
        for name in stale:
            print(f"STALE data/real/{name}: run `make capture`", file=sys.stderr)
        return 1 if stale else 0
    for name, data in files.items():
        path = OUT / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
    count = files[NEWS_FILE.as_posix()].count(b"\n{")
    print(f"wrote data/real: {count} filings, {datetime.now(UTC):%Y-%m-%d %H:%M} UTC")
    return 0


if __name__ == "__main__":
    sys.exit(main())
