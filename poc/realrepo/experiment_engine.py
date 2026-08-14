"""Phase 2 core engine: invalidation strategies + completion gate + replay.

A "world" for v2 is lightweight: a fixed set of three CompletionClaims
(agent_a_completion, agent_b_completion, testing_completion), a dependency
graph, and a changed producer. Invalidation marks claims STALE per strategy;
revalidation replays the real calibrated oracle result for that claim's
verify-set under the changed tree. The final global gate aggregates local
claims; the hidden oracle is the calibrated oracle-set result.

This reuses the calibrated oracle rather than re-running pytest on every
replay (deterministic + fast), EXCEPT in counterfactual candidate replay
where we DO run real pytest to confirm the candidate actually prevents the
breakthrough — see badcase/replay.py.
"""
from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

from common.models import Dependency, RelType, ChangeType, Status
from common.classifier import ChangeClassifier


GLOBAL_OK = "VERIFIED"
GLOBAL_NOT_READY = "NOT_READY"
GLOBAL_FAILED = "FAILED"


# ---------------------------------------------------------------------------
# World
# ---------------------------------------------------------------------------

@dataclass
class ClaimSlot:
    claim_id: str
    status: str = Status.VERIFIED.value     # one of agent_a/b/testing completion
    verify_key: str = ""                    # which oracle set drives it


@dataclass
class World:
    repo: str
    claims: dict = field(default_factory=dict)   # claim_id -> ClaimSlot
    graph: list = field(default_factory=list)    # [Dependency]
    strategy: str = "change_aware"
    changed: dict | None = None                 # {producer, ct, case_id}


def make_world(repo: str, graph: list, strategy="change_aware") -> World:
    slots = {
        "agent_a_completion": ClaimSlot("agent_a_completion", Status.VERIFIED.value, "agent_a"),
        "agent_b_completion": ClaimSlot("agent_b_completion", Status.VERIFIED.value, "agent_b"),
        "testing_completion": ClaimSlot("testing_completion", Status.VERIFIED.value, "testing"),
    }
    return World(repo=repo, claims=slots, graph=graph, strategy=strategy)


# ---------------------------------------------------------------------------
# Invalidation strategies
# ---------------------------------------------------------------------------

def select_targets(world: World, changed_producer: str, ct: ChangeType) -> list[str]:
    strat = world.strategy
    if strat == "all_downstream":
        return [c for c in world.claims if c != "agent_a_completion"]
    if strat == "static":
        return _graph_targets(world, changed_producer, change_aware=False, ct=None)
    if strat == "freshness":
        return _graph_targets(world, changed_producer, change_aware=False, ct=None)
    if strat == "change_aware":
        return _graph_targets(world, changed_producer, change_aware=True, ct=ct)
    raise ValueError(strat)


def _graph_targets(world, producer, change_aware, ct):
    out = []
    for e in world.graph:
        if e.source != producer or e.relation_type != RelType.ARTIFACT_TO_COMPLETION:
            continue
        if change_aware and ct not in e.scope:
            continue
        out.append(e.target)
    return list(dict.fromkeys(out))


# ---------------------------------------------------------------------------
# Gate + replay against calibrated oracle
# ---------------------------------------------------------------------------

def global_status(world: World) -> str:
    statuses = [c.status for c in world.claims.values()]
    if any(s == Status.FAILED.value for s in statuses):
        return GLOBAL_FAILED
    if any(s in (Status.STALE.value, Status.PENDING.value) for s in statuses):
        return GLOBAL_NOT_READY
    return GLOBAL_OK


def replay_case(world: World, case: dict, oracle_case: dict) -> dict:
    """Apply a change to the world under its strategy, then revalidate using
    the calibrated per-set PASS/FAIL as the revalidation oracle.

    Returns structured trace including the hidden (oracle-set) result and
    whether a Global False Completion occurred.
    """
    claim_to_set = {"agent_a_completion": "agent_a",
                    "agent_b_completion": "agent_b",
                    "testing_completion": "testing"}
    ct = ChangeType(oracle_case["classified_ct"])
    targets = select_targets(world, case["producer"], ct)

    # mark STALE
    invalidated = []
    for cid in targets:
        if world.claims[cid].status == Status.VERIFIED.value:
            world.claims[cid].status = Status.STALE.value
            invalidated.append(cid)

    # revalidate STALE claims using calibrated oracle: a claim that was marked
    # STALE then re-ran its verify-set; its new status = oracle result for that set
    revalidated = {}
    for cid in invalidated:
        set_key = claim_to_set[cid]
        res = oracle_case["results"].get(set_key, {}).get("result", "FAIL")
        new = Status.VERIFIED.value if res == "PASS" else Status.FAILED.value
        world.claims[cid].status = new
        revalidated[cid] = new

    gate_after = global_status(world)
    oracle_result = oracle_case["results"]["oracle"]["result"]
    # Global False Completion: gate says OK but oracle FAILS
    gfc = (gate_after == GLOBAL_OK and oracle_result == "FAIL")

    return {
        "case_id": case["case_id"],
        "repo": case["repo"],
        "producer": case["producer"],
        "change_type": ct.value,
        "strategy": world.strategy,
        "invalidated": invalidated,
        "revalidated": revalidated,
        "claims_after": {cid: c.status for cid, c in world.claims.items()},
        "global_after": gate_after,
        "oracle_result": oracle_result,
        "global_false_completion": gfc,
    }


# ---------------------------------------------------------------------------
# Ground-truth invalidation set (programmatic, per case)
# ---------------------------------------------------------------------------

def ground_truth_invalidation(case: dict, oracle_case: dict) -> set:
    """A completion slot SHOULD be invalidated iff its verify-set FAILED under
    this change (programmatic ground truth from the calibrated oracle)."""
    claim_to_set = {"agent_a_completion": "agent_a",
                    "agent_b_completion": "agent_b",
                    "testing_completion": "testing"}
    gt = set()
    # agent_a is the producer; it self-heals — not counted as a downstream miss
    for cid, set_key in claim_to_set.items():
        if cid == "agent_a_completion":
            continue
        res = oracle_case["results"].get(set_key, {}).get("result")
        if res == "FAIL":
            gt.add(cid)
    # when the whole suite fails to even collect (collection error) count all
    return gt


def load_oracle(extended: bool = False):
    name = "oracle_calibrated_extended.json" if extended else "oracle_calibrated.json"
    return json.load(open(Path(__file__).parent / name))


def find_oracle_case(oracle: dict, repo: str, case_id: str) -> dict:
    for c in oracle["cases"]:
        if c.get("repo") == repo and c.get("case") == case_id:
            return c
    raise KeyError(f"{repo}/{case_id}")


def verify_sets_for(oracle: dict, repo: str) -> dict:
    return oracle["repos"][repo]["verify_sets"]