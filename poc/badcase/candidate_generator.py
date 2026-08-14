"""Candidate dependency generator.

Deliberately simple and deterministic (rule + trace-based, no ML, no LLM):
every uninvalidated dependent that lacks a dependency edge yields one
candidate edge, scoped to the change type observed in the badcase.
"""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from core.models import Dependency, ChangeType
from .analyzer import find_uninvalidated_dependents
from .model import Badcase


@dataclass
class CandidateDependency:
    source: str
    target: str
    relation_type: str
    scope: list           # change-type strings this edge should fire on
    reason: str

    def to_dependency(self) -> Dependency:
        return Dependency(self.source, self.target, self.relation_type,
                          frozenset(ChangeType(s) for s in self.scope))

    def to_dict(self):
        return asdict(self)


def _human_name(claim_id: str) -> str:
    return {"FRONTEND_COMPLETION": "Frontend completion",
            "TESTING_COMPLETION": "Testing completion"}.get(claim_id, claim_id)


def generate_candidates(badcase: Badcase) -> list[CandidateDependency]:
    if not badcase.global_false_completion:
        return []
    candidates = []
    for finding in find_uninvalidated_dependents(badcase):
        if finding["dependency_edge_exists"]:
            continue  # edge exists but didn't fire — a scope bug, not a missing edge
        source = finding["changed_artifact"]
        target = finding["claim_id"]
        reason = (
            f"{_human_name(target)} was VERIFIED against {source} "
            f"v{finding['based_on_version']} and consumes it, but {source} "
            f"changed to v{finding['new_version']} ({badcase.change_type}) "
            f"without invalidating the claim — no dependency edge covers it."
        )
        candidates.append(CandidateDependency(
            source=source,
            target=target,
            relation_type="artifact->claim",
            scope=[badcase.change_type],
            reason=reason,
        ))
    return candidates
