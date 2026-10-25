"""Run the pre-registered earnings-tone study on the downloaded data.

    uv run python -m scripts.fetch_earnings_data   # once: downloads into var/research
    uv run python -m scripts.run_earnings_study    # offline: results + report

Reads only the local cache (a missing file is an error, never a silent download), and
writes ``web/src/data/earnings-study.json`` (aggregates only, read by the dashboard) and
``docs/research/earnings-tone-study.md`` (generated from that JSON).
"""

from __future__ import annotations

import json
import platform
import subprocess
import sys
import time
from importlib.metadata import version
from pathlib import Path
from typing import Any

from alphadots_research.earnings.study import Plan, analyse, load
from scripts.fetch_earnings_data import DATA, ROOT, sec_client, web_client
from scripts.run_research import _clean, _num

RESULTS = ROOT / "web" / "src" / "data" / "earnings-study.json"
REPORT = ROOT / "docs" / "research" / "earnings-tone-study.md"
PREREGISTRATION = "docs/research/earnings-tone-preregistration.md"
SOURCES = [
    {
        "name": "SEC EDGAR",
        "use": "8-K filings, acceptance times, press releases, XBRL size",
        "terms": "US government data, fair-access policy",
    },
    {
        "name": "Yahoo Finance",
        "use": "daily adjusted closes",
        "terms": "not licensed for redistribution; kept locally, only aggregates published",
    },
    {
        "name": "Kenneth R. French data library",
        "use": "market, FF5 and momentum daily factors",
        "terms": "free for research, cited",
    },
    {
        "name": "Loughran-McDonald Master Dictionary 1993-2025",
        "use": "tone word lists",
        "terms": "free for academic research; downloaded at run time, not committed",
    },
]


def git_commit() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],  # noqa: S607 - developer tool on PATH
            cwd=ROOT,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        return "unknown"
    return out.stdout.strip()


def _bps(value: float | None) -> str:
    return "n/a" if value is None else _num(value * 10_000, 1)


def _fit_table(fits: dict[str, Any], key_label: str) -> str:
    rows = ["| Sample | Events | Coefficient | t-stat | R² |", "|---|---:|---:|---:|---:|"]
    for sample, fit in fits.items():
        if fit is None:
            rows.append(f"| {sample} | too few | | | |")
            continue
        key = fit["key"]
        rows.append(
            f"| {sample} | {fit['n']:,} | {_bps(key['coef'])} bps per s.d. of {key_label} "
            f"| {_num(key['t'], 2)} | {_num(fit['r2'], 3, signed=False)} |"
        )
    return "\n".join(rows)


def _portfolio_row(label: str, s: dict[str, Any]) -> str:
    return (
        f"| {label} | {s['start']} to {s['end']} | {_num(s['ann_return'], 1, pct=True)} "
        f"| {_num(s['sharpe'])} [{_num(s['sharpe_ci_low'])}, {_num(s['sharpe_ci_high'])}] "
        f"| {_num(s['alpha_ann'], 1, pct=True)} (t {_num(s['alpha_t'], 1)}) "
        f"| {_num(s['max_drawdown'], 1, pct=True)} | {_num(s['avg_longs'], 0, signed=False)} / "
        f"{_num(s['avg_shorts'], 0, signed=False)} |"
    )


def report(results: dict[str, Any]) -> str:
    plan, funnel, port = results["plan"], results["funnel"], results["portfolio"]
    h1, h2 = results["confirmatory"]["h1"], results["confirmatory"]["h2"]
    timing, agreement = results["timing"], results["tone_agreement"]
    sessions = timing["sessions"]
    total = sum(sessions.values()) or 1
    dev, hold = port["primary"]["development"], port["primary"]["holdout"]
    naive_dev = port["naive"]["development"]
    decision = results["decision"]
    groups = results["car_paths"]["groups"]
    prov = results["provenance"]

    funnel_rows = "\n".join(
        f"| {row['step']} | {row['count']:,} | {row['note']} |" for row in funnel
    )
    rejected = ", ".join(
        f"{r['name']} ({_num(r['float_bn'], 0, signed=False)} bn)" for r in results["rejected"][:6]
    )
    session_rows = "\n".join(
        f"| {name} | {count:,} | {_num(count / total, 1, signed=False, pct=True)} |"
        for name, count in sessions.items()
    )
    tercile_rows = "\n".join(
        f"| {g['label']} | {g['n']:,} | {_bps(g['reaction']['mean'])} "
        f"[{_bps(g['reaction']['low'])}, {_bps(g['reaction']['high'])}] | "
        f"{_bps(g['drift']['mean'])} [{_bps(g['drift']['low'])}, {_bps(g['drift']['high'])}] |"
        for g in groups
    )
    exploratory_rows = "\n".join(
        f"| {row['label']} | "
        + " | ".join(
            "too few"
            if row[s] is None
            else f"{_bps(row[s]['key']['coef'])} (t {_num(row[s]['key']['t'], 1)})"
            for s in ("development", "holdout")
        )
        + " |"
        for row in results["exploratory"]
    )
    variant_rows = "\n".join(
        f"| {v['signal'].replace('_', ' ')} | {v['hold']} | {_num(v['sharpe'])} | "
        f"{_num(v['ann_return'], 1, pct=True)} | {_num(v['turnover'], 2, signed=False)} |"
        for v in port["variants"]
    )
    cost_rows = ", ".join(f"{_num(c['sharpe'])} at {c['bps']:g} bps" for c in port["costs"])
    criteria_rows = "\n".join(
        f"| {c['label']} | {'yes' if c['passed'] else 'no'} |" for c in decision["criteria"]
    )
    h1d, h2d, h2h = h1["development"], h2["development"], h2["holdout"]
    early_share = _num(timing["naive_early"] / max(timing["events"], 1), 0, signed=False, pct=True)
    portfolio_header = (
        "| Portfolio | Period | Net return a year | Net Sharpe [95% CI] "
        "| 6-factor alpha a year | Max drawdown | Names long / short |"
    )
    trials = port["deflated"]["trials"]
    verdict = (
        "the tone signal is worth further work."
        if decision["worth_more_work"]
        else "the effect is not established in this sample."
    )

    learned = []
    if h1d is not None and h1d["key"]["t"] > 2:
        learned.append(
            f"**The words carry news.** A one-standard-deviation more positive tone surprise "
            f"comes with a {_bps(h1d['key']['coef'])} bps higher two-day abnormal return "
            f"(t = {_num(h1d['key']['t'], 1)}, development sample). Tone measured against the "
            f"firm's own past releases is informative about the announcement."
        )
    else:
        learned.append(
            "**The tone measure barely registers at the announcement** (t below 2), which "
            "limits what it can say about the drift that follows."
        )
    if h2d is not None and h2d["key"]["t"] > 2:
        learned.append(
            f"**Tone predicts the following month in development** "
            f"({_bps(h2d['key']['coef'])} bps per s.d., t = {_num(h2d['key']['t'], 1)}); "
            f"the holdout shows {_bps(h2h['key']['coef']) if h2h else 'n/a'} bps "
            f"(t = {_num(h2h['key']['t'], 1) if h2h else 'n/a'})."
        )
    else:
        learned.append(
            f"**No reliable drift.** After controlling for the announcement return and the "
            f"run-up, tone surprise predicts days +2 to +21 with a coefficient of "
            f"{_bps(h2d['key']['coef']) if h2d else 'n/a'} bps per s.d. "
            f"(t = {_num(h2d['key']['t'], 1) if h2d else 'n/a'}). For these large US firms "
            f"in 2016-2023 the market appears to finish pricing the words within two days."
        )
    learned.append(
        f"**Date-only timing manufactures returns.** {early_share} "
        f"of releases came after the close but carry that day's date. Trading them at that "
        f"date's close (before the news was public) gives a development Sharpe of "
        f"{_num(naive_dev['sharpe'])}, against {_num(dev['sharpe'])} for the honest timing."
    )
    learned.append(
        f"**The pipeline's headline lexicon is a weak instrument for long documents.** It "
        f"correlates {_num(agreement['pearson'], 2)} with Loughran-McDonald tone and finds no "
        f"tone words at all in {agreement['lexicon_silent']:,} of {agreement['n']:,} releases."
    )

    return f"""# Earnings press-release tone and post-announcement returns

*Generated by `scripts/run_earnings_study.py` from `web/src/data/earnings-study.json`
(code at commit `{prov["commit"]}`, data downloaded {prov["downloaded_at"]}). The plan
was fixed before any return was computed: [pre-registration]({Path(PREREGISTRATION).name}).
Real data; research only, not investment advice. Before the full run, the code was
checked end to end on the first 25 firms to catch errors; nothing in the plan changed
after that check.*

## Question

When a large US company publishes its quarterly results, does the *tone* of the press
release, measured against the same company's earlier releases, predict how the stock
does over the following month, after the market's first reaction? If so, investors
under-react to the words; if not, the words are priced within a day or two.

## Data and sample

| Step | Count | Note |
|---|---:|---|
{funnel_rows}

The universe is chosen from 2014 data, so it does not favour today's survivors. Filer
XBRL contains unit errors that would otherwise top the size ranking, for example
{rejected}; {len(results["rejected"])} such filers were removed by the pre-registered checks.
{len(results["unpriced"])} selected firms have no price history today (acquired,
merged, delisted, or renamed under a new CIK), so their releases cannot be studied: price
survivorship re-enters here, and the results describe firms that survived to 2026.

When releases are published (New York time):

| Session | Releases | Share |
|---|---:|---:|
{session_rows}

## Method

Tone is the Loughran-McDonald net tone, (positive - negative) / (positive + negative), of
the release text with financial tables removed. **Tone surprise** is tone minus the mean
of the firm's previous four releases. Day 0 is the first close after EDGAR accepted the
filing. Abnormal return is the stock's return minus the CRSP value-weighted market
return. Regressions use two-way clustered standard errors (firm and day); tone surprise is
standardised within each year, so coefficients are per standard deviation. Development
sample: {plan["test_start"]} to 2023-12-31; holdout: {plan["holdout_start"]} to
{plan["end"]}, run once. Full detail: the pre-registration.

## Results

### H1: tone and the announcement return (days 0 to +1)

{_fit_table(h1, "tone surprise")}

### H2: tone and the drift (days +2 to +21), controlling for the reaction and run-up

{_fit_table(h2, "tone surprise")}

### Event-time abnormal returns by tone-surprise third (all tested events)

| Third | Events | Reaction, days 0 to +1 (bps) [95% CI] | Drift, days +2 to +21 (bps) [95% CI] |
|---|---:|---:|---:|
{tercile_rows}

### Trading test: long the most positive third, short the most negative

Enter at the close of day +1, hold {plan["hold"]} days, {plan["cost_bps"]:g} bps per dollar
traded.

{portfolio_header}
|---|---|---:|---:|---:|---:|---:|
{_portfolio_row("Honest timing, development", dev)}
{_portfolio_row("Honest timing, holdout", hold)}
{_portfolio_row("Date-only timing, development", naive_dev)}
{_portfolio_row("Date-only timing, holdout", port["naive"]["holdout"])}

Development Sharpe of the honest portfolio at different costs: {cost_rows}.

All {trials} trading variants tried (development, net of {plan["cost_bps"]:g} bps):

| Signal | Days held | Net Sharpe | Net return a year | Turnover a day |
|---|---:|---:|---:|---:|
{variant_rows}

Deflated Sharpe: by luck alone, the best of {port["deflated"]["trials"]} variants would show an
annualised Sharpe of {_num(port["deflated"]["benchmark_annualized"])}; the probability that
the primary variant's true Sharpe exceeds that is
{_num(port["deflated"]["probability"], 2, signed=False)}.

### Exploratory regressions (reported, never used to rescue the main test)

| Specification | Development | Holdout |
|---|---:|---:|
{exploratory_rows}

## Decision (pre-registered rule)

| Criterion | Met |
|---|---|
{criteria_rows}

**Verdict: {verdict}**

## What was learned

{chr(10).join(f"{i}. {text}" for i, text in enumerate(learned, start=1))}

## Limitations

- Price history only for firms listed today ({len(results["unpriced"])} of the 300 are
  missing), so the sample leans towards survivors.
- No analyst forecasts: the earnings surprise is controlled for only through the
  announcement return.
- One dictionary-based tone measure for the confirmatory test; dictionaries miss context
  that a transformer model might catch.
- Costs are a flat rate per dollar traded; no market impact or borrow costs.
- Daily closes only: the study cannot see intraday reactions.

## Reproduce

```bash
uv run python -m scripts.fetch_earnings_data   # needs ALPHADOTS_SEC_USER_AGENT in .env
uv run python -m scripts.run_earnings_study
```

Downloads are cached in `var/research` (never committed). The analysis code is tested on
synthetic panels with a planted effect and a null (`tests/research/earnings`).
"""


def main() -> int:
    started = time.monotonic()
    manifest_file = DATA / "download-manifest.json"
    if not manifest_file.exists():
        sys.exit("No downloaded data: run `uv run python -m scripts.fetch_earnings_data` first.")
    manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
    with sec_client(offline=True) as sec, web_client(offline=True) as web:
        data = load(sec, web, DATA / "derived", Plan())
    results = analyse(data, Plan())
    results["provenance"] = {
        "commit": git_commit(),
        "downloaded_at": manifest["completed_at"],
        "requests": manifest["requests"],
        "python": platform.python_version(),
        "numpy": version("numpy"),
        "runtime_seconds": round(time.monotonic() - started, 1),
        "preregistration": PREREGISTRATION,
        "sources": SOURCES,
    }
    results = _clean(results)
    RESULTS.write_text(
        json.dumps(results, indent=1, ensure_ascii=False) + "\n", encoding="utf-8", newline="\n"
    )
    REPORT.write_text(report(results), encoding="utf-8", newline="\n")
    print(f"wrote {RESULTS.relative_to(ROOT)} and {REPORT.relative_to(ROOT)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
