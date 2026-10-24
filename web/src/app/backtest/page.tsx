"use client";

import { useState } from "react";

import { CostChart, EquityChart, FoldChart, QuintileChart, SeriesSwatch } from "@/components/backtest";
import { StatTile, StatusTag } from "@/components/primitives";
import { formatNumber, formatSigned } from "@/lib/format";
import { REPORT_URL, STRATEGY_ORDER, study, type Summary, type WorldKey } from "@/lib/research";

const WORLDS: { value: WorldKey; label: string }[] = [
  { value: "planted", label: "Planted effect" },
  { value: "null", label: "No effect (null)" },
];

function sharpeWithCi(summary: Summary): string {
  return `${formatSigned(summary.sharpe)} [${formatSigned(summary.sharpe_ci_low)}, ${formatSigned(summary.sharpe_ci_high)}]`;
}

export default function BacktestPage() {
  const [worldKey, setWorldKey] = useState<WorldKey>("planted");
  const world = study.worlds[worldKey];
  const config = study.config;
  const news = world.strategies.sentiment;
  const lookahead = world.strategies.lookahead;
  const oos = news.oos;
  const tStat = oos.ic_tstat ?? 0;
  const dsr = world.deflated_sharpe.probability;

  const verdicts =
    worldKey === "planted"
      ? [
          {
            tone: tStat > 3 ? ("good" as const) : ("warning" as const),
            title: tStat > 3 ? "Effect found" : "Effect not clearly found",
            text: `Out-of-sample rank IC ${formatSigned(oos.mean_ic ?? 0, 3)} (t = ${formatSigned(tStat, 1)}); the untouched holdout agrees at ${formatSigned(news.holdout.mean_ic ?? 0, 3)}.`,
          },
          {
            tone: oos.sharpe_ci_low > 0 ? ("good" as const) : ("warning" as const),
            title: oos.sharpe_ci_low > 0 ? "Profitable after costs" : "Not yet profitable after costs",
            text: `Gross Sharpe ${formatSigned(oos.gross_sharpe)}, net ${sharpeWithCi(oos)} after ${config.cost_bps} bps per dollar traded. Deflated-Sharpe probability ${dsr === null ? "n/a" : dsr.toFixed(2)} over ${world.deflated_sharpe.trials} values of τ tried.`,
          },
          {
            tone: "critical" as const,
            title: "Look-ahead bug would mislead",
            text: `Computing the signal one close ahead shows a net Sharpe of ${formatSigned(lookahead.oos.sharpe)}, against ${formatSigned(oos.sharpe)} for the honest signal.`,
          },
        ]
      : [
          {
            tone: Math.abs(tStat) < 2 ? ("good" as const) : ("warning" as const),
            title: Math.abs(tStat) < 2 ? "No effect claimed" : "Spurious effect",
            text: `With nothing planted, the same procedure gives rank IC ${formatSigned(oos.mean_ic ?? 0, 3)} (t = ${formatSigned(tStat, 1)}). The shuffled control: t = ${formatSigned(world.strategies.shuffled.oos.ic_tstat ?? 0, 1)}.`,
          },
          {
            tone: "critical" as const,
            title: "A leak manufactures a strategy",
            text: `The look-ahead bug earns net Sharpe ${formatSigned(lookahead.oos.sharpe)} in a market with nothing to find. That gap is what the point-in-time guard is for.`,
          },
          {
            tone: "neutral" as const,
            title: "Costs alone",
            text: `The honest signal's ${formatSigned(oos.sharpe)} net Sharpe here is ${config.cost_bps} bps trading costs on a gross of ${formatSigned(oos.gross_sharpe)}.`,
          },
        ];

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Backtest lab</h1>
          <p className="page-sub">
            Does the news signal predict returns? Tested where the answer is known: a synthetic
            market with a planted effect, and the same market with none. A sound method finds the
            first and stays silent on the second.
          </p>
        </div>
        <div className="segmented" role="group" aria-label="Market">
          {WORLDS.map((option) => (
            <button
              key={option.value}
              type="button"
              aria-pressed={worldKey === option.value}
              onClick={() => setWorldKey(option.value)}
            >
              {option.label}
            </button>
          ))}
        </div>
      </div>

      <p className="card-note backtest-scope">
        Synthetic market of {config.market.n_assets} assets × {formatNumber(config.market.n_days)} trading days.
        These numbers grade the method; they are not a claim about real markets.{" "}
        <a href={REPORT_URL} target="_blank" rel="noreferrer">
          Full report
        </a>
      </p>

      <div className="tiles">
        <StatTile
          label="Rank IC (walk-forward)"
          value={formatSigned(oos.mean_ic ?? 0, 3)}
          sub={`t = ${formatSigned(tStat, 1)} · ${Math.round((oos.ic_hit_rate ?? 0) * 100)}% of days positive`}
        />
        <StatTile
          label="Net Sharpe"
          value={formatSigned(oos.sharpe)}
          sub={`95% CI ${formatSigned(oos.sharpe_ci_low)} to ${formatSigned(oos.sharpe_ci_high)}`}
        />
        <StatTile label="Gross Sharpe" value={formatSigned(oos.gross_sharpe)} sub="before trading costs" />
        <StatTile
          label="Holdout net Sharpe"
          value={formatSigned(news.holdout.sharpe)}
          sub={`${config.holdout_days} untouched days`}
        />
        <StatTile
          label="Deflated Sharpe prob."
          value={dsr === null ? "—" : dsr.toFixed(2)}
          sub={`after ${world.deflated_sharpe.trials} trials`}
        />
        <StatTile
          label="Max drawdown"
          value={`${formatSigned(oos.max_drawdown * 100, 1)}%`}
          sub={`${oos.turnover.toFixed(2)}× gross traded per day`}
        />
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
        <EquityChart world={world} />
      </div>

      <div className="backtest-grid">
        <FoldChart world={world} />
        <QuintileChart world={world} />
      </div>

      <div className="backtest-grid">
        <CostChart world={world} current={config.cost_bps} />
        <section className="card">
          <div className="card-head">
            <h2 className="section-title">How it was tested</h2>
          </div>
          <div className="card-body">
            <ul className="method-list">
              <li>
                <strong>Production signal code.</strong> Scores come from the same{" "}
                <code className="kbd">decayed_sentiment</code> the live pipeline runs. It was handed
                readings before they were available {formatNumber(study.pit_guard.offered_before_available)}{" "}
                times and refused every one.
              </li>
              <li>
                <strong>Walk-forward.</strong> {config.first_train_days}-day first training window, then{" "}
                {config.folds} test blocks of {config.test_days} days. A {config.purge_days}-day purge and{" "}
                {config.embargo_days}-day embargo separate training from testing; τ is chosen on training data only.
              </li>
              <li>
                <strong>Untouched holdout.</strong> The last {config.holdout_days} days, evaluated once.
              </li>
              <li>
                <strong>Portfolio.</strong> Long the top 30%, short the bottom 30% of names with recent
                news, held one day; no trade with fewer than {config.min_names} names.{" "}
                {config.cost_bps} bps on every dollar traded.
              </li>
              <li>
                <strong>Uncertainty.</strong> Block-bootstrap Sharpe intervals, and a deflated Sharpe
                ratio for the {world.deflated_sharpe.trials} values of τ tried.
              </li>
              <li>
                <strong>Data.</strong> {formatNumber(study.news.items)} synthetic news items; the sentiment
                model abstained on {formatNumber(study.news.abstained)}.
              </li>
            </ul>
          </div>
        </section>
      </div>

      <section className="card">
        <div className="card-head">
          <h2 className="section-title">All strategies</h2>
          <span className="card-note">walk-forward unless marked; net of costs</span>
        </div>
        <div className="table-wrap" style={{ border: 0 }}>
          <table className="table">
            <thead>
              <tr>
                <th scope="col">Strategy</th>
                <th scope="col" className="col-num">Rank IC</th>
                <th scope="col" className="col-num">IC t-stat</th>
                <th scope="col" className="col-num">Net Sharpe [95% CI]</th>
                <th scope="col" className="col-num">Gross Sharpe</th>
                <th scope="col" className="col-num">Max drawdown</th>
                <th scope="col" className="col-num">Traded / day</th>
                <th scope="col" className="col-num">Holdout Sharpe</th>
              </tr>
            </thead>
            <tbody>
              {STRATEGY_ORDER.map((key) => {
                const strategy = world.strategies[key];
                return (
                  <tr key={key}>
                    <td>
                      <span className="strategy-name">
                        <SeriesSwatch series={key} />
                        {strategy.label}
                      </span>
                    </td>
                    <td className="col-num num">{formatSigned(strategy.oos.mean_ic ?? 0, 3)}</td>
                    <td className="col-num num">{formatSigned(strategy.oos.ic_tstat ?? 0, 1)}</td>
                    <td className="col-num num">{sharpeWithCi(strategy.oos)}</td>
                    <td className="col-num num">{formatSigned(strategy.oos.gross_sharpe)}</td>
                    <td className="col-num num">{formatSigned(strategy.oos.max_drawdown * 100, 1)}%</td>
                    <td className="col-num num">{strategy.oos.turnover.toFixed(2)}</td>
                    <td className="col-num num">{formatSigned(strategy.holdout.sharpe)}</td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </>
  );
}
