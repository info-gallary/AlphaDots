"use client";

import { useAppState } from "@/components/AppState";
import { ArrowGlyph } from "@/components/icons";
import { ErrorState, Loading, StatusTag } from "@/components/primitives";
import { formatNumber, formatTime, humanize } from "@/lib/format";
import type { DeadLettersResponse, OverviewResponse } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const ROADMAP = [
  { id: "M0", state: "built", text: "Contracts, fixture replay, dedup and issuer linking, lexicon sentiment, decay heuristic, API, this dashboard." },
  { id: "M1", state: "next", text: "SEC-compatible adapter, watermark policy for late data, effective-dated reference topic, consumer-lag metrics." },
  { id: "M2", state: "planned", text: "FinBERT baseline with calibration and a labelled evaluation sample." },
  { id: "M3", state: "planned", text: "Market data, purged walk-forward validation, cost-aware backtests, untouched holdout." },
  { id: "M4", state: "planned", text: "Bounded LangGraph research agent with claim-level citations and abstention." },
  { id: "M5", state: "planned", text: "k3d and Helm, Terraform plan for AWS, CI/CD gates, image scanning, SBOM." },
  { id: "M6", state: "planned", text: "Measured latency and throughput benchmarks; no performance numbers are claimed before then." },
] as const;

export default function SystemPage() {
  const { asOf, refreshMs, source } = useAppState();
  const overview = useApi<OverviewResponse>("v1/overview", { as_of: asOf }, { refreshMs });
  const dead = useApi<DeadLettersResponse>("v1/dead-letters", { limit: 50 }, { refreshMs });

  const counts = overview.data?.pipeline;
  const stages = counts
    ? [
        { name: "Ingestion", topic: "news.raw.v1", value: null, sides: [["Quarantined", counts.dead_letters]] },
        {
          name: "Preprocessor",
          topic: "events.normalized.v1",
          value: counts.events,
          sides: [
            ["Exact duplicates", counts.duplicates],
            ["Unresolved issuer", counts.unresolved],
          ],
        },
        { name: "Sentiment", topic: "sentiment.scored.v1", value: counts.scored, sides: [["Abstained", counts.sentiment_abstained]] },
        { name: "Signal engine", topic: "signals.generated.v1", value: counts.signals, sides: [["Abstained", counts.signals_abstained]] },
        { name: "API read model", topic: "REST · /v1", value: counts.events, sides: [["Signals held", counts.signals]] },
      ]
    : [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Pipeline</h1>
          <p className="page-sub">
            What reached each stage, as visible from the API&apos;s read model. Every stage
            publishes a versioned topic; delivery is at-least-once and every effect is idempotent.
          </p>
        </div>
      </div>

      {source?.source === "snapshot" ? (
        <div className="callout" style={{ marginBottom: 20 }}>
          <StatusTag tone="warning">Static snapshot</StatusTag>
          <span>
            This deployment has no live pipeline attached. The data is the real pipeline&apos;s
            output over synthetic fixtures ({source.snapshot?.events ?? 0} events,{" "}
            {source.snapshot?.signals ?? 0} signals), exported by{" "}
            <code className="kbd">{source.snapshot?.generated_by}</code>. Models:{" "}
            {Object.values(source.snapshot?.models ?? {}).join(", ")}. Set{" "}
            <code className="kbd">ALPHADOTS_API_URL</code> to attach a running API.
          </span>
        </div>
      ) : null}

      {overview.error && !overview.data ? (
        <ErrorState message={overview.error.message} />
      ) : !counts ? (
        <Loading label="Loading pipeline" />
      ) : (
        <section className={`card${overview.isStale ? " is-stale" : ""}`} style={{ marginBottom: 20 }}>
          <div className="flow">
            {stages.map((stage, index) => (
              <div key={stage.name} className="flow-stage">
                <span className="flow-name">{stage.name}</span>
                <span className="flow-topic mono">{stage.topic}</span>
                <span className="flow-count num">{stage.value === null ? "—" : formatNumber(stage.value)}</span>
                <div className="flow-sides">
                  {stage.sides.map(([label, value]) => (
                    <div key={label}>
                      <span>{label}</span>
                      <span className="num">{formatNumber(Number(value))}</span>
                    </div>
                  ))}
                </div>
                {index < stages.length - 1 ? <ArrowGlyph className="flow-arrow" /> : null}
              </div>
            ))}
          </div>
        </section>
      )}

      <div className="issuer-grid" style={{ gridTemplateColumns: "minmax(0, 1fr) minmax(0, 1fr)" }}>
        <section className="card">
          <div className="card-head">
            <h2 className="section-title">Dead-letter queue</h2>
            <span className="card-note">not time-filtered: operational records</span>
          </div>
          <div className="card-body">
            {dead.data?.data.length ? (
              <dl className="kv" style={{ gridTemplateColumns: "minmax(0, 1fr)" }}>
                {dead.data.data.map((item) => (
                  <div key={item.dlq_id} style={{ display: "flex", flexDirection: "column", gap: 4, paddingBottom: 10, borderBottom: "1px solid var(--hairline)" }}>
                    <StatusTag tone="warning">
                      {humanize(item.reason)} · {item.service}
                    </StatusTag>
                    <span className="mono" style={{ fontSize: 12.5 }}>{item.original_key ?? item.original_topic}</span>
                    <span className="muted" style={{ fontSize: 12.5, overflowWrap: "anywhere" }}>{item.error_detail}</span>
                    <span className="muted" style={{ fontSize: 12 }}>{formatTime(item.failed_at)} UTC</span>
                  </div>
                ))}
              </dl>
            ) : (
              <p className="muted">Nothing quarantined.</p>
            )}
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <h2 className="section-title">Milestones</h2>
            <span className="card-note">from the engineering specification</span>
          </div>
          <div className="card-body">
            <div className="roadmap">
              {ROADMAP.map((item) => (
                <div key={item.id} style={{ display: "contents" }}>
                  <span className="chip chip-solid mono">{item.id}</span>
                  <span className="roadmap-desc">{item.text}</span>
                  <StatusTag tone={item.state === "built" ? "good" : "neutral"}>{item.state}</StatusTag>
                </div>
              ))}
            </div>
          </div>
        </section>
      </div>
    </>
  );
}
