"""Publishes a completed report to ``reports.generated.v1``.

A best-effort step after the job is already marked ``succeeded`` in the job store: the
job's own status is the source of truth for whoever is polling it, and a bus outage here
must not turn a completed brief into a failed job (see ``jobs.py``'s ``JobRunner``).
"""

from __future__ import annotations

from alphadots_common.bus.base import EventBus
from alphadots_common.worker import Outbound, publish
from alphadots_contracts import ReportGeneratedV1, Topic


async def publish_report(bus: EventBus, report: ReportGeneratedV1) -> None:
    await publish(bus, Outbound(Topic.REPORTS_GENERATED, report.instrument.instrument_id, report))
