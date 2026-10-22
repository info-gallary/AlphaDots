"use client";

import { useAppState } from "@/components/AppState";
import { Funnel, type FunnelStage } from "@/components/charts";
import { ErrorState, Loading, StatusTag } from "@/components/primitives";
import { formatNumber, formatTime, humanize } from "@/lib/format";
import type { DeadLettersResponse, MonitorResponse, OverviewResponse, Severity } from "@/lib/types";
import { useApi } from "@/lib/useApi";

const MONITOR_REFRESH_MS = 20_000;

const ROADMAP = [
  { id: "M0", state: "built", text: "Contracts, fixture replay, dedup and issuer linking, lexicon sentiment, decay heuristic, API, dashboard." },
  { id: "M1", state: "next", text: "SEC-compatible adapter, watermark policy for late data, consumer-lag metrics." },
  { id: "M2", state: "planned", text: "FinBERT baseline with calibration and a labelled evaluation sample." },
  { id: "M3", state: "planned", text: "Market data, purged walk-forward validation, cost-aware backtests, untouched holdout." },
  { id: "M4", state: "built", text: "Bounded LangGraph research agent and pipeline monitor, claim-level citations, abstention." },
  { id: "M5", state: "built", text: "Docker, Helm chart, Terraform for AWS (validated, not applied), CI/CD gates, six scanners, SBOM, Prometheus and Grafana." },
  { id: "M6", state: "planned", text: "Measured latency and throughput benchmarks on recorded hardware." },
] as const;

const SEVERITY_TONE: Record<Severity, "good" | "warning" | "critical"> = {
  ok: "good",
  warning: "warning",
  critical: "critical",
};

export default function SystemPage() {
  const { asOf, refreshMs, source } = useAppState();
  const isSnapshot = source?.source === "snapshot";
  const overview = useApi<OverviewResponse>("v1/overview", { as_of: asOf }, { refreshMs });
  const dead = useApi<DeadLettersResponse>("v1/dead-letters", { limit: 50 }, { refreshMs });
  const monitor = useApi<MonitorResponse>("v1/monitor", {}, { refreshMs: isSnapshot ? 0 : MONITOR_REFRESH_MS });

  const counts = overview.data?.pipeline;
  const stages: FunnelStage[] = counts
    ? [
        { name: "Source records", topic: "news.raw.v1", value: counts.events + counts.dead_letters, drop: { label: "quarantined", value: counts.dead_letters } },
        { name: "Normalized", topic: "events.normalized.v1", value: counts.events, drop: { label: "exact duplicates", value: counts.duplicates } },
        { name: "Linked to an issuer", topic: "entity resolution", value: counts.events - counts.unresolved, drop: { label: "unresolved", value: counts.unresolved } },
        { name: "Scored", topic: "sentiment.scored.v1", value: counts.scored, drop: { label: "abstained", value: counts.sentiment_abstained } },
        { name: "Signals", topic: "signals.generated.v1", value: counts.signals, drop: { label: "abstained", value: counts.signals_abstained } },
      ]
    : [];

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Pipeline</h1>
          <p className="page-sub">
            What reached each stage, read from the API&apos;s read model. Every stage publishes a
            versioned topic; delivery is at-least-once and every effect is idempotent.
          </p>
        </div>
        {isSnapshot && source?.snapshot ? (
          <span className="chip" title={source.snapshot.description}>
            Snapshot · {source.snapshot.events} events · {source.snapshot.signals} signals
          </span>
        ) : null}
      </div>

      {overview.error && !overview.data ? (
        <ErrorState message={overview.error.message} />
      ) : !counts ? (
        <Loading label="Loading pipeline" />
      ) : (
        <section className={`card${overview.isStale ? " is-stale" : ""}`} style={{ marginBottom: 16 }}>
          <div className="card-head">
            <h2 className="section-title">Stage throughput</h2>
            <span className="card-note">records reaching each stage · signals are per issuer per item</span>
          </div>
          <div className="card-body">
            <Funnel stages={stages} />
          </div>
        </section>
      )}

      <div className="system-grid">
        <section className="card">
          <div className="card-head">
            <h2 className="section-title">Pipeline monitor</h2>
            <span className="card-note">LangGraph · collect → analyze → narrate</span>
          </div>
          <div className="card-body">
            {monitor.error && !monitor.data ? (
              <p className="muted">{monitor.error.message}</p>
            ) : !monitor.data ? (
              <p className="muted">Asking the monitor…</p>
            ) : (
              <div className="monitor">
                <div className="monitor-head">
                  <StatusTag tone={SEVERITY_TONE[monitor.data.data.status]} strong>
                    {monitor.data.data.status.toUpperCase()}
                  </StatusTag>
                  <strong>{monitor.data.data.headline}</strong>
                </div>
                <p className="monitor-summary">{monitor.data.data.summary}</p>
                <ul className="findings">
                  {monitor.data.data.findings.map((finding) => (
                    <li key={finding.metric}>
                      <StatusTag tone={SEVERITY_TONE[finding.severity]}>{finding.title}</StatusTag>
                      <span className="muted">{finding.detail}</span>
                      <span className="mono num finding-value">
                        {humanize(finding.metric)} = {formatNumber(finding.value)}
                      </span>
                    </li>
                  ))}
                </ul>
                <p className="card-note">
                  {monitor.data.data.model_id}
                  {monitor.data.data.latency_ms === null ? "" : ` · ${Math.round(monitor.data.data.latency_ms)} ms`}
                  {" · "}
                  {isSnapshot ? "recorded at export" : monitor.data.cached ? "cached" : "fresh"} ·{" "}
                  {formatTime(monitor.data.data.generated_at)} UTC. Status and findings are computed in
                  Python; the model only words the summary.
                </p>
              </div>
            )}
          </div>
        </section>

        <section className="card">
          <div className="card-head">
            <h2 className="section-title">Dead-letter queue</h2>
            <span className="card-note">operational records, not time-filtered</span>
          </div>
          <div className="card-body">
            {dead.data?.data.length ? (
              <ul className="findings">
                {dead.data.data.map((item) => (
                  <li key={item.dlq_id}>
                    <StatusTag tone="warning">
                      {humanize(item.reason)} · {item.service}
                    </StatusTag>
                    <span className="mono finding-key">{item.original_key ?? item.original_topic}</span>
                    <span className="muted finding-detail">{item.error_detail}</span>
                    <span className="muted num">{formatTime(item.failed_at)} UTC</span>
                  </li>
                ))}
              </ul>
            ) : (
              <p className="muted">Nothing quarantined.</p>
            )}
          </div>
        </section>

        <section className="card system-roadmap">
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
