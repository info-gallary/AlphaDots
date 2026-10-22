# ADR 0003: A bounded LangGraph research agent and pipeline monitor ahead of M3

- Status: accepted
- Date: 2026-10-22

## Context

The user asked directly for "agentic monitoring and helper in langgraph," ahead of the
milestone order (M4 depends on M3, not yet built). CLAUDE.md permits reordering "unless
there is a recorded reason" — this is that record, following ADR 0001's pattern.

## Decision

Two bounded LangGraph graphs, both narrating facts a deterministic Python step computes
first, never deciding a number or a severity themselves:

- **`research_graph`** (the "helper"): retrieve → analyze → synthesize → verify. Produces
  an evidence-backed brief for one ticker. Every citation traces to a real event or signal
  ID the verifier checked; a claim that reads as a trade recommendation is dropped before
  it reaches the output, regardless of source. No filings/market data exist yet (M1/M3),
  so the brief says so explicitly rather than fabricating a fundamentals section.
- **`monitor_graph`** (the "agentic monitoring"): collect → analyze → narrate. Summarises
  pipeline health (dead letters, abstention rates) in plain language. Unlike the brief, it
  never abstains into silence — Python decides `status`; the model only writes the
  sentence, and falls back to a fixed one if it is unreachable or invalid.

Both run through one bounded call path (`runner.complete_structured`): a fixed token and
time bound per call, a bounded number of correction retries on invalid JSON, and a
reactive cost bound (abstain before the next call once spend crosses it — costs cannot be
known before a provider computes tokens, so the bound is enforced after each call, not
predicted before the first).

**LLM adapter** (`libs/llm`): a `MockAdapter` that deterministically renders the same
pre-computed facts a real model would be asked to narrate (no network, byte-stable,
exercises the exact same prompt contract) and a `GroqAdapter` (raw `httpx`, not a vendor
SDK). `ALPHADOTS_LLM_PROVIDER=auto` picks Groq once `ALPHADOTS_GROQ_API_KEY` is set, mock
otherwise — the system works fully, and every test passes, with no key at all.

**Service boundary**: `research_agent` is its own service, reaching the AlphaDots API only
through its public, already point-in-time-correct REST endpoints (the same allowlisted
`get_events`/`get_signal`/`get_overview` shape the specification names) — never direct
database access. The API resolves and validates a ticker before ever calling
`research_agent`, so the agent trusts the instrument_id/symbol/name it is given and does
not load its own copy of the instrument master. `research_agent` publishes a completed
brief to `reports.generated.v1` (implementing that previously-reserved topic); the API
projects it for `GET /v1/research/{symbol}`, consistent with every other read.

**Job execution** is in-process only (an `asyncio` semaphore, not a queue), with job rows
in `research_agent`'s own Postgres table for idempotent creation (same ticker + resolved
`as_of` reaches the same job). A job stuck "running" past a crash is not resumed — a
documented limit of this simplification, matching ADR 0001's style, not an oversight.

**Prompt injection**: a news item's full body text is never sent to the model at all —
only a headline, quoted as a string inside a fenced JSON fact block the system prompt
names as inert data. This is stricter than relying on the model to recognise an
instruction embedded in evidence, and the recommendation-language filter is a second,
independent check applied to the model's own output regardless of where it came from. A
fixture headline (`news/seed.jsonl` line 15) carries exactly this attack in its body; see
`tests/e2e/test_agent_pipeline.py::test_the_prompt_injection_fixture_never_produces_recommendation_language`.

## Consequences

- `make agents` (compose profile `agents`, not started by `make up`) or `make dev-agent`
  (against the no-Docker stack) bring it up; `make agent-smoke` verifies it end to end.
- Metrics `alphadots_agent_tool_calls_total` and `alphadots_agent_cost_usd_total`, named
  by the specification and previously unimplemented, now exist, plus
  `alphadots_agent_run_seconds` and `alphadots_agent_abstentions_total`.
- Not built in this pass: the dashboard's "Research notebook" and "Monitoring assistant"
  panels (M4's UI half). The API is fully usable without them (`POST /v1/research/jobs`,
  `GET /v1/research/jobs/{id}`, `GET /v1/research/{symbol}`, `GET /v1/monitor`); wiring
  them into `web/` is the next incremental step.
- `get_filings`, `get_market_snapshot`, `get_features`, `get_peer_comparison` remain
  unimplemented tools (M1/M3); calling one raises `ToolUnavailable` rather than returning
  empty or fabricated data.
