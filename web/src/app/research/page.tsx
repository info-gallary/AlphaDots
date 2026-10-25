"use client";

import Link from "next/link";
import { useEffect, useMemo, useRef, useState } from "react";

import { useAppState } from "@/components/AppState";
import { EventDrawer } from "@/components/EventDrawer";
import { Dot, Loading, StatusTag } from "@/components/primitives";
import { formatMinute, formatNumber, humanize } from "@/lib/format";
import { MARKET_LABEL, marketOf, type Market } from "@/lib/market";
import type {
  ApiErrorBody,
  EventDetailResponse,
  EventItem,
  OverviewResponse,
  ResearchBriefResponse,
  ResearchClaim,
  ResearchJob,
  ResearchJobResponse,
  ResearchReport,
} from "@/lib/types";
import { buildUrl, useApi } from "@/lib/useApi";

const POLL_MS = 900;
const POLL_LIMIT = 80;

/** The research graph's real nodes, in order (services/research_agent/research_graph.py). */
const GRAPH_STEPS = [
  { node: "retrieve", label: "Retrieve", text: "Point-in-time events and signals for the issuer, read-only tools only." },
  { node: "analyze", label: "Analyze", text: "Facts computed in Python: tone, counts, the decayed score." },
  { node: "synthesize", label: "Synthesize", text: "The model drafts thesis and counter-thesis from those facts." },
  { node: "verify", label: "Verify", text: "Every claim must cite stored evidence; trade language is dropped." },
] as const;

const UNCERTAINTY = ["low", "medium", "high"] as const;

type RunState = { key: string } & (
  | { phase: "idle" }
  | { phase: "running"; job: ResearchJob | null }
  | { phase: "done"; job: ResearchJob }
  | { phase: "error"; message: string }
);

async function readError(response: Response): Promise<string> {
  try {
    const body = (await response.json()) as ApiErrorBody;
    return body.error.message;
  } catch {
    return `Request failed with status ${response.status}.`;
  }
}

function sleep(ms: number, signal: AbortSignal) {
  return new Promise<void>((resolve, reject) => {
    const timer = setTimeout(resolve, ms);
    signal.addEventListener("abort", () => {
      clearTimeout(timer);
      reject(new DOMException("aborted", "AbortError"));
    });
  });
}

export default function ResearchPage() {
  const { asOf, source } = useAppState();
  const overview = useApi<OverviewResponse>("v1/overview", { as_of: asOf });
  const [chosen, setSymbol] = useState<string | null>(null);
  const [stored, setRun] = useState<RunState>({ key: "", phase: "idle" });
  const [evidenceId, setEvidenceId] = useState<string | null>(null);
  const controller = useRef<AbortController | null>(null);
  const isSnapshot = source?.source === "snapshot";

  const groups = useMemo(() => {
    const result: Record<Market, { symbol: string; name: string; events: number }[]> = { us: [], nse: [], replay: [] };
    for (const { instrument, event_count } of overview.data?.instruments ?? []) {
      result[marketOf(instrument.instrument_id)].push({ symbol: instrument.symbol, name: instrument.name, events: event_count });
    }
    return result;
  }, [overview.data]);
  // Until the user picks one, show the first issuer of whatever dataset is loaded.
  const symbol = chosen ?? (Object.values(groups).flat()[0]?.symbol ?? "");

  // A run belongs to one issuer and decision time; changing either shows that pair's state.
  const runKey = `${symbol}|${asOf ?? "latest"}`;
  const run: RunState = stored.key === runKey ? stored : { key: runKey, phase: "idle" };

  const latest = useApi<ResearchBriefResponse>(symbol ? `v1/research/${encodeURIComponent(symbol)}` : null, { as_of: asOf });
  const evidence = useApi<EventDetailResponse>(evidenceId ? `v1/events/${evidenceId}` : null, { as_of: asOf });
  const evidenceEvent: EventItem | null = evidenceId ? (evidence.data?.data ?? null) : null;

  useEffect(() => () => controller.current?.abort(), []);
  useEffect(() => () => controller.current?.abort(), [runKey]);

  async function generate() {
    controller.current?.abort();
    const abort = new AbortController();
    controller.current = abort;
    const key = runKey;
    setRun({ key, phase: "running", job: null });
    try {
      const created = await fetch(buildUrl("v1/research/jobs"), {
        method: "POST",
        headers: { "content-type": "application/json" },
        body: JSON.stringify({ symbol, as_of: asOf }),
        signal: abort.signal,
      });
      if (!created.ok) throw new Error(await readError(created));
      let job = ((await created.json()) as ResearchJobResponse).data;
      for (let attempt = 0; attempt < POLL_LIMIT && (job.status === "pending" || job.status === "running"); attempt += 1) {
        setRun({ key, phase: "running", job });
        await sleep(POLL_MS, abort.signal);
        const polled = await fetch(buildUrl(`v1/research/jobs/${encodeURIComponent(job.job_id)}`), {
          cache: "no-store",
          signal: abort.signal,
        });
        if (!polled.ok) throw new Error(await readError(polled));
        job = ((await polled.json()) as ResearchJobResponse).data;
      }
      if (job.status === "pending" || job.status === "running") {
        throw new Error("The agent is still working. Check back in a moment.");
      }
      setRun({ key, phase: "done", job });
    } catch (failure) {
      if (abort.signal.aborted) return;
      setRun({ key, phase: "error", message: failure instanceof Error ? failure.message : "The request failed." });
    }
  }

  const report: ResearchReport | null =
    run.phase === "done" ? run.job.report : run.phase === "idle" ? (latest.data?.data ?? null) : null;
  const instrument = overview.data?.instruments.find((item) => item.instrument.symbol === symbol)?.instrument;

  return (
    <>
      <div className="page-head">
        <div>
          <h1 className="page-title">Research notebook</h1>
          <p className="page-sub">
            A bounded LangGraph agent writes an issuer brief from point-in-time evidence. Every
            claim cites a stored item, and when the evidence is too thin it abstains instead of
            guessing.
          </p>
        </div>
      </div>

      <div className="card research-bar">
        <label className="field" htmlFor="research-symbol">
          Issuer
          <select id="research-symbol" value={symbol} onChange={(change) => setSymbol(change.target.value)}>
            {(Object.keys(groups) as Market[]).map((market) =>
              groups[market].length ? (
                <optgroup key={market} label={MARKET_LABEL[market]}>
                  {groups[market].map((item) => (
                    <option key={item.symbol} value={item.symbol}>
                      {item.symbol} · {item.name} ({item.events} items)
                    </option>
                  ))}
                </optgroup>
              ) : null,
            )}
          </select>
        </label>
        <span className="card-note">
          Decision time: <span className="mono num">{asOf ? `${formatMinute(asOf)} UTC` : "latest"}</span>
        </span>
        {isSnapshot ? (
          <span className="card-note research-note">Briefs recorded at export. New runs need a live API.</span>
        ) : null}
        <button
          type="button"
          className="btn btn-accent research-go"
          onClick={generate}
          disabled={run.phase === "running" || isSnapshot}
          title={isSnapshot ? "Point ALPHADOTS_API_URL at a running API with the agents profile to generate new briefs." : undefined}
        >
          {run.phase === "running" ? "Agent working…" : "Generate brief"}
        </button>
      </div>

      <div className="research-grid">
        <div className="stack">
          {run.phase === "error" ? (
            <div className="callout">
              <StatusTag tone="critical">Brief failed</StatusTag>
              <span>{run.message}</span>
            </div>
          ) : null}
          {run.phase === "running" ? (
            <Loading label={`Agent ${run.job?.status ?? "starting"} for ${symbol}`} />
          ) : report ? (
            <Brief report={report} name={instrument?.name ?? report.instrument.symbol} onEvidence={setEvidenceId} />
          ) : run.phase === "done" && run.job.status === "failed" ? (
            <div className="callout">
              <StatusTag tone="critical">Run failed</StatusTag>
              <span>{run.job.error ?? "The agent run broke before producing a brief."}</span>
            </div>
          ) : !latest.isLoading ? (
            <div className="card empty">
              No brief for <span className="mono">{symbol}</span> yet. Select <strong>Generate brief</strong>.
            </div>
          ) : null}
        </div>

        <aside className="stack">
          <section className="card">
            <div className="card-head">
              <h2 className="section-title">Agent graph</h2>
              <span className="card-note">research_graph.py</span>
            </div>
            <div className="card-body">
              <ol className="graph-steps">
                {GRAPH_STEPS.map((step, index) => {
                  const state =
                    run.phase === "running"
                      ? run.job?.status === "running"
                        ? "active"
                        : "waiting"
                      : report
                        ? "done"
                        : "waiting";
                  return (
                    <li key={step.node} className="graph-step" data-state={state}>
                      <span className="graph-node mono">{index + 1}</span>
                      <span>
                        <strong>{step.label}</strong> <span className="mono muted">{step.node}</span>
                        <span className="graph-text">{step.text}</span>
                      </span>
                    </li>
                  );
                })}
              </ol>
            </div>
          </section>
          {report ? <Telemetry report={report} /> : null}
        </aside>
      </div>

      <EventDrawer event={evidenceEvent} onClose={() => setEvidenceId(null)} />
    </>
  );
}

function Brief({
  report,
  name,
  onEvidence,
}: {
  report: ResearchReport;
  name: string;
  onEvidence: (eventId: string) => void;
}) {
  const level = UNCERTAINTY.indexOf(report.uncertainty_rating);
  return (
    <article className="card brief">
      <header className="brief-head">
        <div>
          <p className="card-note">
            Brief as of <span className="mono num">{formatMinute(report.as_of)} UTC</span>
          </p>
          <h2 className="brief-title">
            <Link href={`/issuers/${report.instrument.symbol}`} className="mono">
              {report.instrument.symbol}
            </Link>{" "}
            · {name}
          </h2>
        </div>
        <div className="uncertainty" aria-label={`Uncertainty ${report.uncertainty_rating}`}>
          <span className="card-note">Uncertainty</span>
          <span className="uncertainty-steps" aria-hidden="true">
            {UNCERTAINTY.map((step, index) => (
              <span key={step} data-on={index <= level} />
            ))}
          </span>
          <span className="uncertainty-label">{report.uncertainty_rating}</span>
        </div>
      </header>

      {report.abstained ? (
        <div className="callout brief-abstain">
          <StatusTag tone="warning">Abstained</StatusTag>
          <span>
            <strong>{humanize(report.abstain_reason ?? "insufficient_evidence")}.</strong> The agent
            declined to write a thesis rather than produce claims it could not cite.
          </span>
        </div>
      ) : null}

      <p className="brief-snapshot">{report.business_snapshot}</p>

      {!report.abstained ? (
        <div className="cases">
          <Case title="Bull case" sign="pos" claims={report.bull_case} onEvidence={onEvidence} />
          <Case title="Bear case" sign="neg" claims={report.bear_case} onEvidence={onEvidence} />
        </div>
      ) : null}

      <div className="brief-quant">
        <span className="card-note">Quant view</span>
        <p>{report.quant_summary}</p>
      </div>

      {report.missing_information.length ? (
        <div className="brief-missing">
          <span className="card-note">Not covered by this brief</span>
          <ul>
            {report.missing_information.map((item) => (
              <li key={item}>{item}</li>
            ))}
          </ul>
        </div>
      ) : null}
    </article>
  );
}

function Case({
  title,
  sign,
  claims,
  onEvidence,
}: {
  title: string;
  sign: "pos" | "neg";
  claims: ResearchClaim[];
  onEvidence: (eventId: string) => void;
}) {
  return (
    <section className="case" data-sign={sign}>
      <h3 className="case-title">
        <Dot sign={sign} /> {title} <span className="muted num">{claims.length}</span>
      </h3>
      {claims.length === 0 ? (
        <p className="muted">No cited claims survived verification.</p>
      ) : (
        <ol className="claims">
          {claims.map((claim) => (
            <li key={`${claim.evidence_id}-${claim.claim}`}>
              <p>{claim.claim}</p>
              {claim.evidence_kind === "event" ? (
                <button type="button" className="chip chip-link evidence" onClick={() => onEvidence(claim.evidence_id)}>
                  Evidence · <span className="mono">{claim.evidence_id.slice(0, 13)}</span>
                </button>
              ) : (
                <span className="chip">Signal · <span className="mono">{claim.evidence_id.slice(0, 13)}</span></span>
              )}
            </li>
          ))}
        </ol>
      )}
    </section>
  );
}

function Telemetry({ report }: { report: ResearchReport }) {
  const rows: [string, string][] = [
    ["Model", `${report.model_id} · ${report.model_version}`],
    ["Prompt", report.prompt_version],
    ["Tool calls", formatNumber(report.tool_calls)],
    ["Tokens in / out", `${formatNumber(report.input_tokens)} / ${formatNumber(report.output_tokens)}`],
    ["Latency", report.latency_ms === null ? "not recorded" : `${formatNumber(Math.round(report.latency_ms))} ms`],
    ["Cost", report.cost_usd === null ? "not estimated" : `$${report.cost_usd.toFixed(4)}`],
    ["Status", humanize(report.validation_status)],
  ];
  return (
    <section className="card">
      <div className="card-head">
        <h2 className="section-title">Run telemetry</h2>
        <span className="card-note mono">{report.job_id.slice(0, 18)}</span>
      </div>
      <div className="card-body">
        <dl className="kv">
          {rows.map(([label, value]) => (
            <div key={label} style={{ display: "contents" }}>
              <dt>{label}</dt>
              <dd className="num">{value}</dd>
            </div>
          ))}
        </dl>
      </div>
    </section>
  );
}
