"""World: the shared state all agents read/write, plus snapshot/restore for replay."""
from __future__ import annotations

from dataclasses import asdict

from .dependency_graph import DependencyGraph
from .models import Artifact, CompletionClaim, Evidence, Task, ClaimStatus


class World:
    def __init__(self, graph: DependencyGraph, strategy: str):
        self.graph = graph
        self.strategy = strategy
        self.artifacts: dict[str, Artifact] = {}
        self.claims: dict[str, CompletionClaim] = {}
        self.evidences: dict[str, Evidence] = {}
        self.tasks: dict[str, Task] = {}
        self.events: list[dict] = []          # structured event log
        # claim_id -> callable(world) -> new status; wired by the coordinator
        self.revalidators: dict[str, object] = {}

    # -- registration ------------------------------------------------------
    def add_task(self, task: Task):
        self.tasks[task.task_id] = task

    def add_artifact(self, artifact: Artifact):
        self.artifacts[artifact.artifact_id] = artifact

    def add_claim(self, claim: CompletionClaim):
        self.claims[claim.claim_id] = claim

    def add_evidence(self, evidence: Evidence):
        self.evidences[evidence.evidence_id] = evidence

    def artifact(self, artifact_id) -> Artifact:
        return self.artifacts[artifact_id]

    def claim(self, claim_id) -> CompletionClaim:
        return self.claims[claim_id]

    # -- change propagation -------------------------------------------------
    def apply_change(self, artifact_id, new_content, change_type, metadata=None):
        """Bump an artifact version and invalidate claims per the active strategy.

        Returns a structured event describing exactly which claims went STALE.
        """
        from .invalidation import select_targets  # local import, avoids cycle

        art = self.artifacts[artifact_id]
        old_version = art.version
        art.bump(new_content, metadata)

        targets = select_targets(self, art, change_type)
        invalidated = []
        for cid in targets:
            claim = self.claims[cid]
            if claim.status == ClaimStatus.VERIFIED.value:
                claim.status = ClaimStatus.STALE.value
                self.tasks[claim.task_id].status = ClaimStatus.STALE.value
                invalidated.append(cid)

        event = {
            "event": "artifact_changed",
            "artifact": artifact_id,
            "old_version": old_version,
            "new_version": art.version,
            "change_type": change_type.value,
            "strategy": self.strategy,
            "invalidated": invalidated,
        }
        self.events.append(event)
        return event

    def revalidate_stale(self, claim_ids):
        """Run the owning agent's revalidator for each STALE claim."""
        results = {}
        for cid in claim_ids:
            claim = self.claims[cid]
            if claim.status != ClaimStatus.STALE.value:
                continue
            if cid not in self.revalidators:
                # no revalidation procedure registered (e.g. producer claims)
                continue
            results[cid] = self.revalidators[cid](self)
        return results

    # -- snapshot / restore (deterministic counterfactual replay) -----------
    def snapshot(self):
        return {
            "strategy": self.strategy,
            "artifacts": {k: asdict(v) for k, v in self.artifacts.items()},
            "claims": {k: asdict(v) for k, v in self.claims.items()},
            "evidences": {k: asdict(v) for k, v in self.evidences.items()},
            "tasks": {k: asdict(v) for k, v in self.tasks.items()},
            "dependencies": self.graph.to_list(),
        }

    @classmethod
    def restore(cls, snap, graph: DependencyGraph, strategy: str):
        world = cls(graph, strategy)
        for k, v in snap["artifacts"].items():
            world.artifacts[k] = Artifact(**v)
        for k, v in snap["claims"].items():
            world.claims[k] = CompletionClaim(**v)
        for k, v in snap["evidences"].items():
            world.evidences[k] = Evidence(**v)
        for k, v in snap["tasks"].items():
            world.tasks[k] = Task(**v)
        return world
