"use client";

import { Funnel, type FunnelStage } from "@/components/charts";
import {
  CarPathChart,
  ReactionDriftChart,
  SessionBars,
  TimingEquityChart,
  ToneIndexChart,
} from "@/components/earnings";
import { StatTile, StatusTag } from "@/components/primitives";
import {
  bps,
  EARNINGS_REPORT_URL,
  earnings,
  PREREGISTRATION_URL,
  SIGNAL_LABEL,
  type Fit,
  type PortfolioSummary,
} from "@/lib/earnings";
import { formatNumber, formatSigned } from "@/lib/format";

/** Funnel steps measured in firms; the rest count releases. */
const FIRM_STEPS = 3;
const FUNNEL_STEPS = 8;

function sharpeWithCi(summary: PortfolioSummary): string {
  return `${formatSigned(summary.sharpe)} [${formatSigned(summary.sharpe_ci_low)}, ${formatSigned(summary.sharpe_ci_high)}]`;
}

/** Criteria in pre-registered order: drift t-stat, holdout coefficient, Sharpe interval. */
function criterionValue(index: number, value: number | number[] | null): string {
  if (value === null) return "n/a";
  if (Array.isArray(value)) return `${formatSigned(value[0] ?? 0)} to ${formatSigned(value[1] ?? 0)}`;
  return index === 0 ? `t = ${formatSigned(value)}` : `holdout ${bps(value, 1)} bps`;
}

function coefficient(fit: Fit | null): string {
  return fit === null ? "too few" : `${bps(fit.key.coef, 1)} bps (t ${formatSigned(fit.key.t, 1)})`;
}

export default function EarningsPage() {
  const { confirmatory, portfolio, funnel, timing, decision, plan, provenance } = earnings;
  const h1 = confirmatory.h1.development;
  const h2 = confirmatory.h2.development;
  const h2Holdout = confirmatory.h2.holdout;
  const dev = portfolio.primary.development;
  const holdout = portfolio.primary.holdout;
  const naiveDev = portfolio.naive.development;
  const tested = funnel[FUNNEL_STEPS - 1]?.count ?? 0;
  const firms = funnel[FIRM_STEPS - 1]?.count ?? 0;
  const informative = h1 !== null && h1.key.t > 2;
  const drift = decision.criteria[0]?.passed ?? false;

  const stages: FunnelStage[] = funnel.slice(0, FUNNEL_STEPS).map((row, index) => {
    const previous = funnel[index - 1];
    const sameUnit = index !== 0 && index !== FIRM_STEPS;
    return {
      name: row.step,
      topic: row.note,
      value: row.count,
      drop: sameUnit && previous ? { label: "dropped", value: previous.count - row.count } : undefined,
    };
  });

  const verdicts = [
    {
      tone: informative ? ("good" as const) : ("warning" as const),
      title: informative ? "The words carry news" : "Tone barely registers",
      text: `A one-s.d. more positive tone surprise comes with ${h1 ? bps(h1.key.coef) : "n/a"} bps more abnormal return over days 0 to +1 (t = ${h1 ? formatSigned(h1.key.t, 1) : "n/a"}, ${formatNumber(h1?.n ?? 0)} releases).`,
    },
    {
      tone: drift ? ("good" as const) : ("neutral" as const),
      title: drift ? "Tone predicts the drift" : "No reliable drift",
      text: `After the reaction, tone surprise predicts days +2 to +21 by ${h2 ? bps(h2.key.coef, 1) : "n/a"} bps per s.d. (t = ${h2 ? formatSigned(h2.key.t, 1) : "n/a"}); holdout ${h2Holdout ? `${bps(h2Holdout.key.coef, 1)} bps` : "n/a"}. Net Sharpe of the strategy: ${sharpeWithCi(dev)}.`,
    },
    {
      tone: "critical" as const,
      title: "Date-only timing flatters the result",
      text: `${formatNumber(timing.naive_early)} of ${formatNumber(timing.events)} releases came out after the close but carry that day's date. Trading at that close, before the news was public, shows a net Sharpe of ${formatSigned(naiveDev.sharpe)}, against ${formatSigned(dev.sharpe)} with honest timing.`,
    },
  ];

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Earnings study</h1>
          <p className="page-sub">
            Does the tone of a company&rsquo;s quarterly results press release predict how its stock does
            over the next month? Real SEC filings from {plan.test_start.slice(0, 4)} to {plan.end.slice(0, 4)},
            tested exactly as pre-registered.
          </p>
        </div>
      </div>

      <p className="card-note backtest-scope">
        Real data: {formatNumber(tested)} earnings releases from {formatNumber(firms)} large US companies.
        The plan was committed before any return was computed. Research only, not investment advice.{" "}
        <a href={PREREGISTRATION_URL} target="_blank" rel="noreferrer">
          Pre-registration
        </a>{" "}
        ·{" "}
        <a href={EARNINGS_REPORT_URL} target="_blank" rel="noreferrer">
          Full report
        </a>
      </p>

      <div className="tiles">
        <StatTile
          label="Releases tested"
          value={formatNumber(tested)}
          sub={`${formatNumber(firms)} firms · ${plan.test_start.slice(0, 4)} to ${plan.end.slice(0, 4)}`}
        />
        <StatTile
          label="Tone → 2-day reaction"
          value={h1 ? `${bps(h1.key.coef)} bps` : "—"}
          sub={h1 ? `per s.d. · t = ${formatSigned(h1.key.t, 1)}` : "too few releases"}
        />
        <StatTile
          label="Tone → next-month drift"
          value={h2 ? `${bps(h2.key.coef, 1)} bps` : "—"}
          sub={h2 ? `per s.d. · t = ${formatSigned(h2.key.t, 1)}` : "too few releases"}
        />
        <StatTile
          label="Strategy net Sharpe"
          value={formatSigned(dev.sharpe)}
          sub={`95% CI ${formatSigned(dev.sharpe_ci_low)} to ${formatSigned(dev.sharpe_ci_high)}`}
        />
        <StatTile label="Holdout net Sharpe" value={formatSigned(holdout.sharpe)} sub="2024 to 2025, run once" />
        <StatTile label="Date-only timing" value={formatSigned(naiveDev.sharpe)} sub="the look-ahead version" />
      </div>

      <section className="card verdicts" aria-label="Findings">
        {verdicts.map((verdict) => (
          <div key={verdict.title} className="verdict">
            <StatusTag tone={verdict.tone} strong>
              {verdict.title}
            </StatusTag>
            <p>{verdict.text}</p>
          </div>
        ))}
      </section>

      <div style={{ marginBottom: 16 }}>
        <CarPathChart />
      </div>

      <div className="backtest-grid">
        <ReactionDriftChart />
        <section className="card">
          <div className="card-head">
            <h2 className="section-title">Pre-registered decision</h2>
            <StatusTag tone={decision.worth_more_work ? "good" : "neutral"} strong>
              {decision.worth_more_work ? "Worth further work" : "Not established"}
            </StatusTag>
          </div>
          <div className="card-body">
            <p className="card-note">
              The signal earns more work only if all three hold. The rule was written before the data was seen.
            </p>
            <ul className="criteria">
              {decision.criteria.map((criterion, index) => (
                <li key={criterion.label}>
                  <span>
                    {criterion.label}{" "}
                    <span className="num">({criterionValue(index, criterion.value)})</span>
                  </span>
                  <StatusTag tone={criterion.passed ? "good" : "critical"}>{criterion.passed ? "Met" : "Not met"}</StatusTag>
                </li>
              ))}
            </ul>
            {dev.sharpe_ci_high < 0 ? (
              <p className="card-note" style={{ marginTop: 10 }}>
                The Sharpe interval excludes zero on the negative side: net of costs the strategy reliably lost
                money. The rule is shown exactly as it was written.
              </p>
            ) : null}
            <ul className="method-list" style={{ marginTop: 16 }}>
              <li>
                <strong>Tone surprise.</strong> Loughran-McDonald net tone of the release text (tables removed),
                minus the firm&rsquo;s average over its previous four releases.
              </li>
              <li>
                <strong>Timing.</strong> Day 0 is the first close after EDGAR accepted the filing; positions open at
                the close of day +{plan.entry_offset} and are held {plan.hold} days.
              </li>
              <li>
                <strong>Inference.</strong> Standard errors clustered by firm and by day; Newey-West for portfolio
                alpha; block-bootstrap Sharpe intervals; deflated Sharpe over {portfolio.deflated.trials} variants
                (probability {portfolio.deflated.probability === null ? "n/a" : portfolio.deflated.probability.toFixed(2)}).
              </li>
            </ul>
          </div>
        </section>
      </div>

      <div style={{ marginBottom: 16 }}>
        <TimingEquityChart />
      </div>

      <div className="backtest-grid">
        <section className="card">
          <div className="card-head">
            <h2 className="section-title">From filings to a sample</h2>
          </div>
          <div className="card-body">
            <Funnel stages={stages} />
            <p className="card-note" style={{ marginTop: 12 }}>
              Chosen by 2014 public float, so the selection does not favour today&rsquo;s survivors; but{" "}
              {formatNumber(earnings.unpriced.length)} firms have no price history today (acquired or delisted),
              so the returns describe survivors.
              {earnings.rejected.length > 0
                ? ` ${formatNumber(earnings.rejected.length)} filers were removed for XBRL unit errors, such as ${earnings.rejected
                    .slice(0, 2)
                    .map((r) => `${r.name} (USD ${formatNumber(Math.round(r.float_bn))} bn float)`)
                    .join(" and ")}.`
                : null}
            </p>
          </div>
        </section>
        <div style={{ display: "grid", gap: 16 }}>
          <section className="card">
            <div className="card-head">
              <h2 className="section-title">When companies publish</h2>
              <span className="card-note">New York time</span>
            </div>
            <div className="card-body">
              <SessionBars />
              <p className="card-note" style={{ marginTop: 10 }}>
                {formatNumber(timing.naive_early)} after-close releases are filed under that day&rsquo;s date. A
                dataset without the time of day puts them one close too early.
              </p>
            </div>
          </section>
          <ToneIndexChart />
        </div>
      </div>

      <section className="card" style={{ marginBottom: 16 }}>
        <div className="card-head">
          <h2 className="section-title">Regressions</h2>
          <span className="card-note">bps of abnormal return per s.d. of tone; t-stats clustered by firm and day</span>
        </div>
        <div className="table-wrap" style={{ border: 0 }}>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Specification</th>
                <th scope="col" className="col-num">Development</th>
                <th scope="col" className="col-num">Holdout</th>
              </tr>
            </thead>
            <tbody>
              <tr>
                <td>
                  <strong>H1</strong> Reaction, days 0 to +1
                </td>
                <td className="col-num num">{coefficient(confirmatory.h1.development)}</td>
                <td className="col-num num">{coefficient(confirmatory.h1.holdout)}</td>
              </tr>
              <tr>
                <td>
                  <strong>H2</strong> Drift, days +2 to +21, given reaction and run-up
                </td>
                <td className="col-num num">{coefficient(confirmatory.h2.development)}</td>
                <td className="col-num num">{coefficient(confirmatory.h2.holdout)}</td>
              </tr>
              {earnings.exploratory.map((row) => (
                <tr key={row.label}>
                  <td className="muted">Exploratory: {row.label}</td>
                  <td className="col-num num">{coefficient(row.development)}</td>
                  <td className="col-num num">{coefficient(row.holdout)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      <div className="backtest-grid">
        <section className="card">
          <div className="card-head">
            <h2 className="section-title">Every trading variant tried</h2>
            <span className="card-note">development, net of {plan.cost_bps} bps</span>
          </div>
          <div className="table-wrap" style={{ border: 0 }}>
            <table className="table">
              <thead>
                <tr>
                  <th scope="col">Signal</th>
                  <th scope="col" className="col-num">Days held</th>
                  <th scope="col" className="col-num">Net Sharpe</th>
                  <th scope="col" className="col-num">Net return / yr</th>
                </tr>
              </thead>
              <tbody>
                {portfolio.variants.map((variant) => (
                  <tr key={`${variant.signal}-${variant.hold}`}>
                    <td>{SIGNAL_LABEL[variant.signal] ?? variant.signal}</td>
                    <td className="col-num num">{variant.hold}</td>
                    <td className="col-num num">{formatSigned(variant.sharpe)}</td>
                    <td className="col-num num">{formatSigned(variant.ann_return * 100, 1)}%</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          <p className="card-note" style={{ padding: "0 16px 14px" }}>
            Primary variant at other costs:{" "}
            {portfolio.costs.map((c) => `${formatSigned(c.sharpe)} at ${c.bps} bps`).join(", ")}. By luck, the best of{" "}
            {portfolio.deflated.trials} variants would show a Sharpe of{" "}
            {formatSigned(portfolio.deflated.benchmark_annualized)}.
          </p>
        </section>
        <section className="card">
          <div className="card-head">
            <h2 className="section-title">Data and provenance</h2>
          </div>
          <div className="card-body">
            <ul className="source-list">
              {provenance.sources.map((source) => (
                <li key={source.name}>
                  <strong>{source.name}</strong>: {source.use}. <span className="muted">{source.terms}.</span>
                </li>
              ))}
            </ul>
            <p className="card-note" style={{ marginTop: 12 }}>
              Code at commit <code className="kbd">{provenance.commit}</code>; data downloaded{" "}
              {provenance.downloaded_at.slice(0, 10)}, at most 8 SEC requests a second under a declared User-Agent.
              Only aggregates are published here; prices and text stay on the machine that ran the study.
            </p>
          </div>
        </section>
      </div>
    </>
  );
}
