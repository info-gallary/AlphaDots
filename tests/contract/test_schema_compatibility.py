"""Schema snapshots are the committed baseline; a breaking change must fail here."""

from __future__ import annotations

import copy
import json
from typing import Any

import pytest

from alphadots_contracts import TOPIC_MODELS
from alphadots_contracts.compat import breaking_changes
from scripts.export_schemas import current_schemas, snapshot_path

TOPICS = [topic.value for topic in TOPIC_MODELS]


def _baseline(topic: str) -> dict[str, Any]:
    path = snapshot_path(topic)
    assert path.exists(), f"missing schema snapshot for {topic}; run `make schemas`"
    loaded: dict[str, Any] = json.loads(path.read_text(encoding="utf-8"))
    return loaded


@pytest.mark.parametrize("topic", TOPICS)
def test_current_schema_is_compatible_with_the_committed_snapshot(topic: str) -> None:
    assert breaking_changes(_baseline(topic), current_schemas()[topic]) == []


@pytest.mark.parametrize("topic", TOPICS)
def test_snapshot_is_up_to_date(topic: str) -> None:
    assert _baseline(topic) == current_schemas()[topic], (
        f"{topic} changed additively; run `make schemas` and commit the snapshot"
    )


# The checker itself must catch each kind of break, or the tests above prove nothing.


@pytest.fixture
def schema() -> dict[str, Any]:
    return copy.deepcopy(current_schemas()["sentiment.scored.v1"])


def test_checker_allows_a_new_optional_field(schema: dict[str, Any]) -> None:
    evolved = copy.deepcopy(schema)
    evolved["properties"]["embedding_ref"] = {"type": "string", "default": ""}
    assert breaking_changes(schema, evolved) == []


def test_checker_ignores_documentation_changes(schema: dict[str, Any]) -> None:
    evolved = copy.deepcopy(schema)
    evolved["properties"]["entropy"]["description"] = "reworded"
    assert breaking_changes(schema, evolved) == []


def test_checker_flags_a_removed_field(schema: dict[str, Any]) -> None:
    evolved = copy.deepcopy(schema)
    del evolved["properties"]["entropy"]
    evolved["required"].remove("entropy")
    assert breaking_changes(schema, evolved) == ["$.entropy: field removed"]


def test_checker_flags_a_type_change(schema: dict[str, Any]) -> None:
    evolved = copy.deepcopy(schema)
    evolved["properties"]["model_id"]["type"] = "integer"
    assert breaking_changes(schema, evolved) == ["$.model_id: type or constraints changed"]


def test_checker_flags_a_new_required_field(schema: dict[str, Any]) -> None:
    evolved = copy.deepcopy(schema)
    evolved["properties"]["must_have"] = {"type": "string"}
    evolved["required"].append("must_have")
    assert breaking_changes(schema, evolved) == ["$.must_have: new required field"]


def test_checker_flags_required_becoming_optional(schema: dict[str, Any]) -> None:
    evolved = copy.deepcopy(schema)
    evolved["required"].remove("model_id")
    assert breaking_changes(schema, evolved) == ["$.model_id: required field became optional"]


def test_checker_flags_a_removed_enum_value_but_allows_an_added_one(
    schema: dict[str, Any],
) -> None:
    removed = copy.deepcopy(schema)
    removed["$defs"]["EventType"]["enum"].remove("macro")
    assert breaking_changes(schema, removed) == ["$defs.EventType: enum values removed ['macro']"]

    added = copy.deepcopy(schema)
    added["$defs"]["EventType"]["enum"].append("dividend")
    assert breaking_changes(schema, added) == []


def test_checker_flags_a_change_inside_a_nested_definition(schema: dict[str, Any]) -> None:
    evolved = copy.deepcopy(schema)
    del evolved["$defs"]["InstrumentRef"]["properties"]["confidence"]
    evolved["$defs"]["InstrumentRef"]["required"].remove("confidence")
    assert breaking_changes(schema, evolved) == ["$defs.InstrumentRef.confidence: field removed"]
