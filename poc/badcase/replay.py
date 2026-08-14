"""Counterfactual replay + regression gate for candidate dependencies."""
from __future__ import annotations

from dataclasses import dataclass, field, asdict

from agents.coordinator import CoordinatorAgent
from core.completion_gate import (
    global_completion, hidden_integration_check, is_false_completion,
)
from core.dependency_graph import DependencyGraph
from core.models import ChangeType, Dependency
from core.world import World
from scenarios.shared import (
    CHANGE_CASES, ALL_CLAIM_SLOTS, build_world, attach_revalidators,
)
from .model import Badcase


@dataclass
class ReplayResult:
    patched: bool
    invalidated: list
    revalidated: dict
    global_completion: str
    hidden_test_result: str
    global_false_completion: bool

    def to_dict(self):
        return asdict(self)


def replay(badcase: Badcase, extra_dependencies=()) -> ReplayResult:
    """Deterministically re-run the badcase change from its pre-change state.

    extra_dependencies: candidate edges grafted onto the badcase's graph.
    Only the graph differs; world state, change, and agents are identical.
    """
    graph = DependencyGraph.from_list(badcase.current_dependencies)
    for dep in extra_dependencies:
        graph.add(dep if isinstance(dep, Dependency) else Dependency.from_dict(dep))

    world = World.restore(badcase.world_snapshot, graph, badcase.strategy)
    attach_revalidators(world)

    event = CoordinatorAgent.apply_change(
        world, badcase.changed_artifact, badcase.new_content,
        change_type=ChangeType(badcase.change_type))

    return ReplayResult(
        patched=bool(extra_dependencies),
        invalidated=event["invalidated"],
        revalidated=event["revalidated"],
        global_completion=global_completion(world),
        hidden_test_result=hidden_integration_check(world)["result"],
        global_false_completion=is_false_completion(world),
    )


def replay_with_patch(badcase: Badcase, candidate_dependency) -> bool:
    """True iff replaying with the candidate edge prevents the false completion."""
    return not replay(badcase, [candidate_dependency]).global_false_completion


# ---------------------------------------------------------------------------
# Regression evaluation: the patch must not over-invalidate on other changes
# ---------------------------------------------------------------------------

def evaluate_regressions(extra_dependencies=(), fir_threshold=0.2) -> dict:
    """Run the 3 regression change cases with the patched graph.

    Ground truth is fixed in CHANGE_CASES — no LLM involved.
    """
    tp = fp = fn = tn = 0
    details = []
    for case in CHANGE_CASES:
        world = build_world(
            strategy="change_aware",
            graph=DependencyGraph.from_list(
                full_graph_list() + [d.to_dict() for d in extra_dependencies]),
        )
        event = CoordinatorAgent.apply_change(
            world, case["artifact"], case["new_content"])
        got = set(event["invalidated"]) & ALL_CLAIM_SLOTS
        gt = case["gt_invalidated"]
        tp += len(got & gt)
        fp += len(got - gt)
        fn += len(gt - got)
        tn += len(ALL_CLAIM_SLOTS - got - gt)
        details.append({
            "case_id": case["case_id"],
            "change_type": event["change_type"],
            "invalidated": sorted(got),
            "ground_truth": sorted(gt),
            "correct": got == gt,
        })

    precision = tp / (tp + fp) if tp + fp else 1.0
    recall = tp / (tp + fn) if tp + fn else 1.0
    fir = fp / (fp + tn) if fp + tn else 0.0
    return {
        "cases": details,
        "tp": tp, "fp": fp, "fn": fn, "tn": tn,
        "invalidation_precision": round(precision, 4),
        "invalidation_recall": round(recall, 4),
        "false_invalidation_rate": round(fir, 4),
        "fir_threshold": fir_threshold,
        "passes": fir <= fir_threshold,
    }


def full_graph_list():
    from scenarios.shared import full_graph
    return full_graph().to_list()
