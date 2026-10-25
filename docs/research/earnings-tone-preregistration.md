# Pre-registration: earnings press-release tone and post-announcement returns

Written and committed **before any stock return was computed** for this study. The
analysis code implements exactly this plan. Anything not specified here, or changed
after results were seen, is reported as exploratory, with the reason. The git history
of this file is the record.

## Question

Do the words in a company's earnings press release carry information the market has not
finished pricing? Concretely: does the tone of the release, relative to the same
company's previous releases, predict the abnormal return over the following month?

Known literature to compare against: Tetlock, Saar-Tsechansky and Macskassy (2008),
Loughran and McDonald (2011), Jegadeesh and Wu (2013), and the post-earnings
announcement drift literature from Bernard and Thomas (1989). Recent large-cap evidence
suggests such effects have weakened. A null result is a valid outcome of this study.

## Data

| Input | Source | Terms |
|---|---|---|
| Filings, acceptance timestamps, 8-K items | SEC EDGAR (submissions API, filing indexes) | US government data; fair-access policy (declared User-Agent, at most 8 requests per second) |
| Universe size measure | SEC XBRL frames, `dei:EntityPublicFloat` and `us-gaap:Assets` | as above |
| Daily prices, split and dividend adjusted | Yahoo Finance chart endpoint | Not licensed for redistribution. Chosen by the project owner; kept on the local machine only; only aggregate statistics are published |
| Market and factor returns | Kenneth French data library (FF5 daily, momentum daily) | Free for research, cited |
| Tone dictionary | Loughran-McDonald Master Dictionary 1993-2025 | Free for academic research; downloaded at run time, never committed |

## Universe (fixed with information from 2014, so no survivorship bias in selection)

1. Public float from the XBRL frames `CY2014Q1I` to `CY2014Q4I`; for each CIK the value
   with the latest period end.
2. Data-error filter: drop a CIK if its float exceeds USD 1 trillion, if total assets
   (same frames, latest end) are missing, or if float exceeds 30 times total assets.
   These are unit errors in filer-submitted XBRL (for example a mid-cap reporting a
   float of USD 239 billion). Every dropped CIK is listed in the output.
3. Keep the **300 largest** by float.
4. Price mapping: the CIK's current ticker in SEC `company_tickers.json` (first listed).
   Firms with no current ticker, or no Yahoo history, are excluded and **counted**: this
   is where survivorship bias re-enters (acquired and delisted firms), and the report
   states its size.

## Events

- Original 8-K filings (not 8-K/A) listing **Item 2.02** (Results of Operations), accepted
  2015-01-01 to 2025-12-31. 2015 events only provide tone history; tested events are
  2016-01-01 to 2025-12-31.
- Text: the first exhibit whose type starts with `EX-99` in the filing index, HTML or
  text. Tables, scripts and styles removed; at least 100 words remain, otherwise the event
  is dropped (counted).
- Duplicate releases: a firm's event within 5 trading days after its previous event is
  dropped.
- **Timing.** EDGAR acceptance time is UTC; it is converted to New York time. Day 0 (`t0`)
  is the acceptance date if accepted before 16:00 New York time on a trading day,
  otherwise the next trading day. Trading days are the dates in the Fama-French daily
  file.

## Measures

- **Tone** = (P - N) / (P + N), with P and N the counts of Loughran-McDonald positive and
  negative words in the release text. A positive word with `no`, `not`, `none`,
  `neither`, `never` or `nobody` among the three preceding words is not counted. Events
  with P + N = 0 are dropped.
- **Tone surprise** = tone minus the mean tone of the same firm's previous (up to 4)
  events; requires at least 2 previous events. Uses only earlier releases, so it is known
  at `t0`.
- **Returns**: daily total returns from adjusted closes. Market return = (Mkt-RF + RF)
  from the Fama-French file. **Abnormal return** AR = stock return minus market return.
  CAR over a window is the sum of ARs; it requires at least 90% of the window's days to
  have a price, otherwise the event is missing for that window.
- Windows (trading days relative to `t0`): reaction `[0,+1]`, drift `[+2,+21]`, pre-event
  run-up `[-21,-2]`.

## Confirmatory tests

Development sample: events with `t0` from 2016-01-01 to 2023-12-31. Holdout: 2024-01-01 to
2025-12-31, evaluated once and reported whatever it shows.

Pooled OLS, standard errors clustered two ways (firm and `t0` date), dependent variables
winsorised at 1% and 99% over the sample, tone surprise z-scored within the calendar year
of `t0`:

- **H1, information content:** `CAR[0,+1] = a + b * ToneSurprise_z`. Expect `b > 0`.
- **H2, drift (the main hypothesis):** `CAR[+2,+21] = a + b * ToneSurprise_z + c * CAR[0,+1] + d * CAR[-21,-2]`.
  Expect `b > 0` if the market under-reacts to the words.

**Trading test (calendar-time portfolio).** An event goes long if its tone surprise is at
or above the 67th percentile of tone surprises of all events in the previous 252 trading
days (strictly before its `t0`), short if at or below the 33rd; at least 100 prior events
are required, otherwise it is not traded. Enter at the close of `t0+1`, exit at the close
of `t0+21`. Each day: equal-weighted mean return of open longs minus that of open shorts
(one dollar each side); no position on days when either side is empty. Costs: 10 basis
points per dollar traded, charged on daily changes in target weights. Reported: annualised
return and volatility, Sharpe with a stationary block-bootstrap 95% interval, alpha
against FF5 plus momentum with Newey-West (20 lags) t-statistic, maximum drawdown,
turnover.

**Look-ahead control (expected to look better, because it is wrong).** The same trading
rule using the filing *date* only: enter at the close of the filing date. For releases
accepted after the close, that position is opened before the news is public.

## Decision rule

The tone signal is **"worth further work"** only if all three hold: H2's `b` has t > 2 in
the development sample; `b` has the same sign in the holdout; the development-period net
Sharpe 95% interval excludes zero. Otherwise the report says the effect is not
established in this sample.

## Exploratory analyses (all reported; none can rescue the confirmatory result)

- Tone measures: level instead of surprise; the AlphaDots pipeline lexicon instead of
  Loughran-McDonald.
- Holding periods of 5 and 60 days; costs of 0, 5 and 20 bps.
- Abnormal returns from a Fama-French 5-factor plus momentum model estimated on
  `[-260,-21]` (at least 120 days).
- Unwinsorised regressions.
- A deflated Sharpe ratio across the 9 trading variants (3 tone measures x 3 holding
  periods).

## Known limitations, stated in advance

- Price data exist only for firms still listed today, so acquired and delisted firms
  drop out after selection; the report counts them.
- Reorganisations that change a company's CIK (for example Google to Alphabet in 2015)
  end that firm's event history; there is no successor mapping.
- No analyst forecasts, so the regression cannot control for the earnings surprise
  except through the announcement return.
- One dictionary-based tone measure; no transformer model in the confirmatory test.
