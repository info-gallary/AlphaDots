/** Simulation-study results written by `scripts/run_research.py` (see docs/research). */

import data from "@/data/research.json";

export type StrategyKey = "sentiment" | "lookahead" | "momentum" | "shuffled" | "lagged";
export type WorldKey = "planted" | "null";

export interface Summary {
  days: number;
  days_traded: number;
  ann_return: number;
  ann_vol: number;
  sharpe: number;
  sharpe_ci_low: number;
  sharpe_ci_high: number;
  gross_sharpe: number;
  max_drawdown: number;
  turnover: number;
  mean_ic: number | null;
  ic_tstat: number | null;
  ic_hit_rate: number | null;
}

export interface Strategy {
  label: string;
  oos: Summary;
  holdout: Summary;
  /** Compounded net return since the first out-of-sample day, one value per date. */
  equity: number[];
}

export interface Fold {
  index: number;
  train_start: string;
  train_end: string;
  test_start: string;
  test_end: string;
  tau_days: number;
  train_ic: number | null;
  test_ic: number | null;
}

export interface World {
  drift: number;
  folds: Fold[];
  holdout_tau_days: number;
  dates: string[];
  holdout_start_index: number;
  strategies: Record<StrategyKey, Strategy>;
  quintiles_bps: number[];
  cost_sensitivity: { bps: number; sharpe: number }[];
  deflated_sharpe: { trials: number; benchmark_annualized: number; probability: number | null };
}

export interface Study {
  kind: "simulation";
  config: {
    cost_bps: number;
    tau_grid_days: number[];
    lag_days: number;
    momentum_days: number;
    first_train_days: number;
    test_days: number;
    holdout_days: number;
    purge_days: number;
    embargo_days: number;
    min_names: number;
    folds: number;
    market: { n_assets: number; n_days: number; start: string; drift: number; news_rate: number; seed: number };
  };
  news: { items: number; readings: number; abstained: number };
  pit_guard: { offered_before_available: number; readings: number; lookahead_offered_before_available: number };
  worlds: Record<WorldKey, World>;
}

export const study = data as unknown as Study;

/**
 * Series order is the colour slot order (validated adjacent pairs): the two strategies
 * the study is about come first, so they get the two strongest hues.
 */
export const STRATEGY_ORDER: StrategyKey[] = ["sentiment", "lookahead", "momentum", "shuffled", "lagged"];

export const SHORT_LABEL: Record<StrategyKey, string> = {
  sentiment: "News sentiment",
  lookahead: "Look-ahead bug",
  momentum: "Momentum",
  shuffled: "Shuffled",
  lagged: "5 days late",
};

export const REPORT_URL =
  "https://github.com/info-gallary/AlphaDots/blob/main/docs/research/simulation-study.md";
