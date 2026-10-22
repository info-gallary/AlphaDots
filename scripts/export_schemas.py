"""Write JSON Schema snapshots for every topic that has a message model.

    uv run python -m scripts.export_schemas           # refresh snapshots (additive changes only)
    uv run python -m scripts.export_schemas --check   # fail if a snapshot is stale or broken

The snapshots under ``libs/contracts/schemas`` are the committed baseline that contract
tests compare against. This script refuses to overwrite a snapshot with a breaking change:
a breaking change needs a new topic (``.v2``), not an edited ``.v1``.
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

from alphadots_contracts import TOPIC_MODELS
from alphadots_contracts.compat import breaking_changes

SCHEMA_DIR = Path(__file__).resolve().parent.parent / "libs" / "contracts" / "schemas"


def snapshot_path(topic: str) -> Path:
    return SCHEMA_DIR / f"{topic}.schema.json"


def current_schemas() -> dict[str, dict[str, Any]]:
    return {topic.value: model.model_json_schema() for topic, model in TOPIC_MODELS.items()}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--check", action="store_true", help="verify instead of writing")
    args = parser.parse_args()

    failed = False
    for topic, schema in current_schemas().items():
        path = snapshot_path(topic)
        baseline = json.loads(path.read_text(encoding="utf-8")) if path.exists() else None
        if baseline is not None:
            problems = breaking_changes(baseline, schema)
            if problems:
                failed = True
                print(f"BREAKING {topic}:", file=sys.stderr)
                for problem in problems:
                    print(f"  - {problem}", file=sys.stderr)
                continue
        if args.check:
            if baseline != schema:
                failed = True
                print(f"STALE {topic}: run `make schemas`", file=sys.stderr)
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(
            json.dumps(schema, indent=2, sort_keys=True) + "\n", encoding="utf-8", newline="\n"
        )
        print(f"wrote {path.relative_to(SCHEMA_DIR.parent.parent.parent)}")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
