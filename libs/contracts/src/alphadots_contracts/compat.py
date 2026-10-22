"""JSON Schema compatibility check for additive-only evolution within a major version.

A change is *breaking* if it could make a message written under one schema unreadable
under the other:

* a field was removed, or its type or constraints changed;
* a new field is required, or an existing field changed between required and optional;
* an enum lost a value.

Adding an optional field is allowed. Adding an enum value is allowed too, with a rollout
rule that this check cannot enforce: deploy consumers before producers, because an old
consumer rejects a value it has never seen.
"""

from __future__ import annotations

from typing import Any

_ANNOTATION_KEYS = frozenset({"description", "title", "default", "examples"})


def _shape(node: Any) -> Any:
    """``node`` with documentation-only keys removed, for structural comparison."""
    if isinstance(node, dict):
        return {key: _shape(value) for key, value in node.items() if key not in _ANNOTATION_KEYS}
    if isinstance(node, list):
        return [_shape(item) for item in node]
    return node


def _compare(path: str, old: dict[str, Any], new: dict[str, Any], problems: list[str]) -> None:
    old_properties: dict[str, Any] = old.get("properties", {})
    new_properties: dict[str, Any] = new.get("properties", {})
    for name, old_property in old_properties.items():
        if name not in new_properties:
            problems.append(f"{path}.{name}: field removed")
        elif _shape(old_property) != _shape(new_properties[name]):
            problems.append(f"{path}.{name}: type or constraints changed")

    old_required = set(old.get("required", []))
    new_required = set(new.get("required", []))
    for name in sorted(new_required - old_required):
        kind = "optional field became required" if name in old_properties else "new required field"
        problems.append(f"{path}.{name}: {kind}")
    for name in sorted(old_required - new_required):
        if name in new_properties:
            problems.append(f"{path}.{name}: required field became optional")

    if "enum" in old:
        removed = [value for value in old["enum"] if value not in new.get("enum", [])]
        if removed:
            problems.append(f"{path}: enum values removed {removed}")


def breaking_changes(old: dict[str, Any], new: dict[str, Any]) -> list[str]:
    """Return human-readable breaking changes from ``old`` to ``new``; empty means safe."""
    problems: list[str] = []
    _compare("$", old, new, problems)
    new_definitions: dict[str, Any] = new.get("$defs", {})
    for name, old_definition in old.get("$defs", {}).items():
        if name not in new_definitions:
            problems.append(f"$defs.{name}: definition removed")
        else:
            _compare(f"$defs.{name}", old_definition, new_definitions[name], problems)
    return problems
