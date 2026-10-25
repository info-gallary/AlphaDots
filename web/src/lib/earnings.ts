/** The real-data earnings-tone study, produced by `scripts/run_earnings_study.py`. */
import data from "@/data/earnings-study.json";

export interface Coefficient {
  coef: number;
  se: number;
  t: number;
}

export interface Fit {
  n: number;
  r2: number;
  coefficients: (Coefficient & { name: string })[];
  key: Coefficient;
}

export interface Samples {
  development: Fit | null;
  holdout: Fit | null;
}

export interface Interval {
  mean: number;
  low: number;
  high: number;
  n: number;
}

export interface PathGroup {
  label: string;
  n: number;
  mean: number[];
  low: number[];
  high: number[];
  reaction: Interval;
  drift: Interval;
}

export interface PortfolioSummary {
  start: string;
  end: string;
  days: number;
  days_traded: number;
  ann_return: number;
  ann_vol: number;
  sharpe: number;
  sharpe_ci_low: number;
  sharpe_ci_high: number;
  max_drawdown: number;
  turnover: number;
  avg_longs: number;
  avg_shorts: number;
  alpha_ann: number;
  alpha_t: number | null;
}

export interface Periods {
  development: PortfolioSummary;
  holdout: PortfolioSummary;
}

export interface EarningsStudy {
  plan: {
    test_start: string;
    holdout_start: string;
    end: string;
    reaction: [number, number];
    drift: [number, number];
    runup: [number, number];
    hold: number;
    cost_bps: number;
    entry_offset: number;
  };
  funnel: { step: string; count: number; note: string }[];
  rejected: { name: string; float_bn: number; assets_bn: number | null; reason: string }[];
  unpriced: string[];
  timing: { sessions: Record<string, number>; naive_early: number; events: number };
  tone_index: { quarter: string; tone: number; n: number }[];
  tone_agreement: { pearson: number | null; n: number; lexicon_silent: number; mean_words: number };
  confirmatory: { h1: Samples; h2: Samples };
  exploratory: ({ label: string } & Samples)[];
  car_paths: { offsets: number[]; groups: PathGroup[] };
  portfolio: {
    primary: Periods;
    naive: Periods;
    costs: { bps: number; sharpe: number; ann_return: number; alpha_ann: number; alpha_t: number | null }[];
    variants: { signal: string; hold: number; sharpe: number; ann_return: number; turnover: number }[];
    deflated: { trials: number; benchmark_annualized: number; probability: number | null };
    equity: { dates: string[]; holdout_start: string; honest: number[]; naive: number[] };
  };
  decision: {
    criteria: { label: string; value: number | number[] | null; passed: boolean }[];
    worth_more_work: boolean;
  };
  provenance: {
    commit: string;
    downloaded_at: string;
    requests: { sec: number; other: number };
    python: string;
    numpy: string;
    runtime_seconds: number;
    preregistration: string;
    sources: { name: string; use: string; terms: string }[];
  };
}

export const earnings = data as unknown as EarningsStudy;

const REPO = "https://github.com/info-gallary/AlphaDots/blob/main";
export const EARNINGS_REPORT_URL = `${REPO}/docs/research/earnings-tone-study.md`;
export const PREREGISTRATION_URL = `${REPO}/docs/research/earnings-tone-preregistration.md`;

export const SIGNAL_LABEL: Record<string, string> = {
  lm_surprise: "LM tone surprise",
  lm_level: "LM tone level",
  lexicon_surprise: "Pipeline lexicon surprise",
};

/** Basis points with a true minus sign: 0.0123 -> "+123". */
export function bps(value: number, digits = 0): string {
  const scaled = Number((value * 10_000).toFixed(digits));
  if (scaled === 0) return (0).toFixed(digits);
  return `${scaled > 0 ? "+" : "−"}${Math.abs(scaled).toFixed(digits)}`;
}
