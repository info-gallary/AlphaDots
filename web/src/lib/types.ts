/** Shapes returned by the AlphaDots API (`services/api/src/alphadots_api/schemas.py`). */

export type FeedMode = "simulated" | "delayed" | "live";

export interface Meta {
  schema_version: string;
  generated_at: string;
  as_of: string | null;
  feed_mode: FeedMode;
  research_only: true;
  disclaimer: string;
}

export interface Instrument {
  instrument_id: string;
  symbol: string;
  name: string;
  sector: string;
  all_symbols: string[];
}

export interface InstrumentRef {
  symbol: string;
  instrument_id: string;
  confidence: number;
}

export interface Sentiment {
  scored_event_id: string;
  model_id: string;
  model_version: string;
  calibration_version: string;
  scores: { negative: number; neutral: number; positive: number };
  signed_sentiment: number;
  entropy: number;
  abstained: boolean;
  abstain_reason: string | null;
  event_type: string;
  materiality: number;
  novelty: number;
  rationale_terms: string[];
  available_at: string;
}

export interface EventItem {
  event_id: string;
  raw_event_id: string;
  source: string;
  source_event_id: string;
  source_url: string;
  source_license: string;
  payload_ref: string;
  content_hash: string;
  cluster_id: string;
  duplicate_of: string | null;
  headline: string;
  body: string | null;
  language: string;
  published_at: string;
  first_seen_at: string;
  ingested_at: string;
  available_at: string;
  source_latency_seconds: number;
  instruments: InstrumentRef[];
  resolution_flags: string[];
  normalizer_version: string;
  trace_id: string;
  sentiment: Sentiment | null;
}

export interface EventsResponse {
  data: EventItem[];
  page: { limit: number; next_cursor: string | null };
  meta: Meta;
}

export interface ClusterMember {
  event_id: string;
  source: string;
  source_event_id: string;
  first_seen_at: string;
  available_at: string;
  is_canonical: boolean;
}

export interface EventDetailResponse {
  data: EventItem;
  cluster: ClusterMember[];
  meta: Meta;
}

export interface SentimentPoint {
  event_id: string;
  headline: string;
  source: string;
  published_at: string;
  available_at: string;
  signed_sentiment: number;
  entropy: number;
  abstained: boolean;
  abstain_reason: string | null;
  is_duplicate: boolean;
  event_type: string;
  materiality: number;
  relevance: number;
}

export interface SentimentResponse {
  instrument: Instrument;
  window: string;
  window_start: string;
  summary: {
    events: number;
    used: number;
    abstained: number;
    duplicates: number;
    positive: number;
    negative: number;
    neutral: number;
    mean_signed_sentiment: number | null;
    mean_entropy: number | null;
  };
  points: SentimentPoint[];
  points_truncated: boolean;
  meta: Meta;
}

export type Stance = "positive" | "negative" | "neutral";

export interface SignalPoint {
  signal_id: string;
  as_of: string;
  available_at: string;
  score: number;
  score_normalized: number;
  stance: Stance;
  abstained: boolean;
}

export interface SignalContribution {
  scored_event_id: string;
  normalized_event_id: string;
  available_at: string;
  age_seconds: number;
  weight: number;
  signed_sentiment: number;
  contribution: number;
}

export interface Signal {
  event_id: string;
  trace_id: string;
  available_at: string;
  instrument: InstrumentRef;
  as_of: string;
  horizon: string;
  signal_kind: string;
  model_id: string;
  model_version: string;
  validation_status: "unvalidated_heuristic" | "out_of_sample_evaluated";
  research_only: true;
  score: number;
  score_normalized: number;
  stance: Stance;
  abstained: boolean;
  abstain_reason: string | null;
  params: {
    tau_seconds: number;
    lookback_seconds: number;
    tau_source: string;
    stance_threshold: number;
    min_effective_weight: number;
  };
  coverage: {
    events_considered: number;
    events_used: number;
    events_abstained: number;
    duplicates_excluded: number;
    effective_weight: number;
  };
  contributions: SignalContribution[];
  contributions_truncated: boolean;
  risk_flags: string[];
  triggering_event_id: string;
}

export interface SignalsResponse {
  instrument: Instrument;
  horizon: string;
  latest: Signal | null;
  history: SignalPoint[];
  meta: Meta;
}

export interface PipelineCounts {
  events: number;
  duplicates: number;
  unresolved: number;
  scored: number;
  sentiment_abstained: number;
  signals: number;
  signals_abstained: number;
  dead_letters: number;
}

export interface Feed {
  mode: FeedMode;
  earliest_available_at: string | null;
  latest_available_at: string | null;
}

export interface InstrumentOverview {
  instrument: Instrument;
  event_count: number;
  latest_signal: SignalPoint | null;
  score_history: SignalPoint[];
}

export interface OverviewResponse {
  feed: Feed;
  pipeline: PipelineCounts;
  instruments: InstrumentOverview[];
  meta: Meta;
}

export interface DeadLetter {
  dlq_id: string;
  service: string;
  reason: string;
  original_topic: string;
  original_key: string | null;
  error_detail: string;
  failed_at: string;
  trace_id: string;
}

export interface DeadLettersResponse {
  data: DeadLetter[];
  meta: Meta;
}

export interface ApiErrorBody {
  error: { code: string; message: string; trace_id: string; details: unknown };
}

/* ------------------------------------------------------------ research agent */

export interface ResearchClaim {
  claim: string;
  evidence_id: string;
  evidence_kind: "event" | "signal";
}

export interface ResearchReport {
  event_id: string;
  trace_id: string;
  available_at: string;
  instrument: InstrumentRef;
  as_of: string;
  job_id: string;
  model_id: string;
  model_version: string;
  prompt_version: string;
  validation_status: string;
  research_only: true;
  abstained: boolean;
  abstain_reason: string | null;
  business_snapshot: string;
  bull_case: ResearchClaim[];
  bear_case: ResearchClaim[];
  quant_summary: string;
  missing_information: string[];
  uncertainty_rating: "low" | "medium" | "high";
  tool_calls: number;
  input_tokens: number;
  output_tokens: number;
  /** Null when no provider rate is configured: "not estimated", not "free". */
  cost_usd: number | null;
  /** Null for briefs recorded into a static snapshot, where no timing is kept. */
  latency_ms: number | null;
}

export type JobStatus = "pending" | "running" | "succeeded" | "failed";

export interface ResearchJob {
  job_id: string;
  symbol: string;
  status: JobStatus;
  as_of: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  report: ResearchReport | null;
  error: string | null;
}

export interface ResearchJobResponse {
  data: ResearchJob;
  meta: Meta;
}

export interface ResearchBriefResponse {
  data: ResearchReport | null;
  meta: Meta;
}

export type Severity = "ok" | "warning" | "critical";

export interface MonitorFinding {
  severity: Severity;
  title: string;
  detail: string;
  metric: string;
  value: number;
}

export interface MonitorSummary {
  generated_at: string;
  status: Severity;
  headline: string;
  summary: string;
  findings: MonitorFinding[];
  model_id: string;
  model_version: string;
  narration_degraded: boolean;
  tool_calls: number;
  cost_usd: number | null;
  /** Null when recorded into a static snapshot. */
  latency_ms: number | null;
}

export interface MonitorResponse {
  data: MonitorSummary;
  cached: boolean;
  meta: Meta;
}
