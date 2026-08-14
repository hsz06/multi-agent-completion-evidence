"""Deterministic change classification.

Ground truth in this PoC is never decided by an LLM: schema and code diffs
are classified by fixed rules.

API schema format:
    {"fields": {"name": "string", ...}, "required": ["name", ...]}
"""
from __future__ import annotations

from .models import ChangeType


def classify_api_change(old_schema: dict, new_schema: dict) -> ChangeType:
    of, nf = old_schema["fields"], new_schema["fields"]
    removed = set(of) - set(nf)
    added = set(nf) - set(of)
    type_changed = any(of[k] != nf[k] for k in set(of) & set(nf))
    newly_required = set(new_schema.get("required", [])) - set(old_schema.get("required", []))
    no_longer_required = set(old_schema.get("required", [])) - set(new_schema.get("required", []))

    if removed or type_changed or newly_required:
        return ChangeType.BREAKING
    if added or no_longer_required:
        return ChangeType.BACKWARD_COMPATIBLE
    return ChangeType.NON_SEMANTIC


def classify_code_change(old_code: str, new_code: str) -> ChangeType:
    old_lines = set(old_code.strip().splitlines())
    new_lines = set(new_code.strip().splitlines())
    changed = (old_lines ^ new_lines)
    if not changed:
        return ChangeType.NON_SEMANTIC
    if all("logger." in line for line in changed):
        return ChangeType.NON_SEMANTIC
    # Unknown semantic code change: conservative default.
    return ChangeType.BREAKING


def classify_change(artifact_type: str, old_content, new_content) -> ChangeType:
    if artifact_type == "api_schema":
        return classify_api_change(old_content, new_content)
    if artifact_type in ("backend_code", "frontend_code", "test_suite"):
        return classify_code_change(old_content, new_content)
    raise ValueError(f"no classifier for artifact type {artifact_type!r}")
