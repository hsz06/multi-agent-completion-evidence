"""Cross-agent dependency graph: Artifact -> Artifact / Evidence / CompletionClaim / Task."""
from __future__ import annotations

from .models import Dependency


class DependencyGraph:
    def __init__(self, dependencies=None):
        self.dependencies = list(dependencies or [])

    # -- mutation ---------------------------------------------------------
    def add(self, dep: Dependency):
        if not self.has_edge(dep.source, dep.target, dep.relation_type):
            self.dependencies.append(dep)

    def remove(self, source, target, relation_type=None):
        self.dependencies = [
            d for d in self.dependencies
            if not (d.source == source and d.target == target
                    and (relation_type is None or d.relation_type == relation_type))
        ]

    def has_edge(self, source, target, relation_type=None):
        return any(
            d.source == source and d.target == target
            and (relation_type is None or d.relation_type == relation_type)
            for d in self.dependencies
        )

    # -- queries ----------------------------------------------------------
    def downstream_claims(self, artifact_id, change_type=None, change_aware=False):
        """Claims that must be invalidated when artifact_id changes.

        change_aware=True filters edges by their scope set.
        """
        out = []
        for d in self.dependencies:
            if d.relation_type != "artifact->claim" or d.source != artifact_id:
                continue
            if change_aware and change_type not in d.scope:
                continue
            out.append(d.target)
        return out

    # -- serialization ----------------------------------------------------
    def to_list(self):
        return [d.to_dict() for d in self.dependencies]

    @classmethod
    def from_list(cls, items):
        return cls([Dependency.from_dict(d) for d in items])
