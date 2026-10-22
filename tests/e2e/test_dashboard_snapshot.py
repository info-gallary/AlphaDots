"""The dashboard's bundled snapshot must be exactly what the pipeline produces today.

The dashboard serves ``web/src/data/snapshot.json`` when no API is attached (for example
on Vercel), and ``web/test/parity.test.ts`` checks its TypeScript read model against
``web/test/parity.golden.json``. Both files are derived from the pipeline, so a change to
any stage that alters output must be followed by ``make snapshot``. This test is what
notices when that step was skipped.
"""

from __future__ import annotations

import json

from scripts.export_snapshot import GOLDEN_FILE, SNAPSHOT_FILE, build


async def test_committed_snapshot_and_goldens_match_the_pipeline() -> None:
    snapshot_text, golden_text = await build()
    assert SNAPSHOT_FILE.read_text(encoding="utf-8") == snapshot_text, "run `make snapshot`"
    assert GOLDEN_FILE.read_text(encoding="utf-8") == golden_text, "run `make snapshot`"


def test_snapshot_is_labelled_simulated_and_carries_its_lineage() -> None:
    snapshot = json.loads(SNAPSHOT_FILE.read_text(encoding="utf-8"))
    assert snapshot["feed_mode"] == "simulated"
    assert "research only" in snapshot["description"]
    assert set(snapshot["fixtures"]) == {
        "instruments.json",
        "news/seed.jsonl",
        "news/replay.jsonl",
    }
    assert all(len(digest) == 64 for digest in snapshot["fixtures"].values())
    assert snapshot["models"]["signal"].startswith("sentiment-decay-heuristic@")
    for signal in snapshot["signals"]:
        assert signal["validation_status"] == "unvalidated_heuristic"
        assert signal["research_only"] is True
