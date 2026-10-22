"""Prometheus metrics shared across services.

Label values are bounded by construction: sources, services, stages, reasons and route
templates. Tickers, article IDs and user IDs must never become label values.

Not yet instrumented (each lands with the milestone that owns it):
``alphadots_kafka_consumer_lag`` (M1), ``alphadots_agent_tool_calls_total`` and
``alphadots_agent_cost_usd_total`` (M4).
"""

from __future__ import annotations

from prometheus_client import Counter, Gauge, Histogram, start_http_server

_LATENCY_BUCKETS = (0.001, 0.0025, 0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

INGEST_EVENTS = Counter(
    "alphadots_ingest_events_total",
    "Source records handled by ingestion.",
    ["source", "status"],
)
INGEST_LAG_SECONDS = Gauge(
    "alphadots_ingest_lag_seconds",
    "Last observed first_seen_at minus published_at, in event time.",
    ["source"],
)
DLQ_EVENTS = Counter(
    "alphadots_events_dlq_total",
    "Messages quarantined to the dead-letter topic.",
    ["service", "reason"],
)
PIPELINE_LATENCY_SECONDS = Histogram(
    "alphadots_pipeline_latency_seconds",
    "Wall-clock time to process one record in a stage (validate, handle, publish, commit).",
    ["stage"],
    buckets=_LATENCY_BUCKETS,
)
MODEL_INFERENCE_SECONDS = Histogram(
    "alphadots_model_inference_seconds",
    "Wall-clock time of one model scoring call.",
    ["model_version"],
    buckets=_LATENCY_BUCKETS,
)
MODEL_ABSTENTIONS = Counter(
    "alphadots_model_abstentions_total",
    "Model or signal abstentions.",
    ["reason"],
)
API_REQUESTS = Counter(
    "alphadots_api_requests_total",
    "API requests by route template and status code.",
    ["route", "status"],
)


def start_metrics_server(port: int) -> None:
    """Expose ``/metrics`` on ``port``. A port of 0 disables the endpoint."""
    if port:
        start_http_server(port)
