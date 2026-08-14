"""Run all 4 PoC experiments and dump structured JSON logs to logs/.

Usage: python run_experiments.py   (from the poc/ directory)
"""
from __future__ import annotations

import json
import random
from pathlib import Path

from agents.backend import SCHEMA_V2_BREAKING
from agents.coordinator import CoordinatorAgent
from badcase.candidate_generator import generate_candidates
from badcase.model import Badcase
from badcase.replay import replay, evaluate_regressions
from core.completion_gate import (
    global_completion, hidden_integration_check, is_false_completion,
)
from core.invalidation import Strategy, ALL_STRATEGIES
from core.models import (
    ChangeType, ID_API_SCHEMA, ID_FRONTEND_COMPLETION, ID_TESTING_COMPLETION,
    to_jsonable,
)
from scenarios.shared import (
    CHANGE_CASES, ALL_CLAIM_SLOTS, build_world, broken_graph,
)

LOG_DIR = Path(__file__).parent / "logs"
LOG_DIR.mkdir(exist_ok=True)
SEED = 42


def header(title):
    print("=" * 48)
    print(title)
    print("=" * 48)


def dump(name, payload):
    (LOG_DIR / name).write_text(
        json.dumps(to_jsonable(payload), indent=2, ensure_ascii=False))


# ---------------------------------------------------------------------------
# Experiment 1: baseline global false completion (no invalidation at all)
# ---------------------------------------------------------------------------

def experiment_1():
    header("Experiment 1: Global False Completion")
    world = build_world(strategy=Strategy.NONE)

    print(f"[Baseline] before change: Global = {global_completion(world)}")
    CoordinatorAgent.apply_change(world, ID_API_SCHEMA, SCHEMA_V2_BREAKING)

    gate = global_completion(world)
    hidden = hidden_integration_check(world)
    false_completion = is_false_completion(world)

    print("[Baseline]")
    print(f"Backend: {world.claim('BACKEND_COMPLETION').status}")
    print(f"Frontend: {world.claim(ID_FRONTEND_COMPLETION).status}")
    print(f"Testing: {world.claim(ID_TESTING_COMPLETION).status}")
    print(f"Global Completion: {gate}")
    print(f"Hidden Integration Test: {hidden['result']} "
          f"(missing fields: {hidden['missing_fields']})")
    print(f"Global False Completion: {str(false_completion).upper()}")
    print()
    print("=> Local Completion aggregation != actual Global Completion")
    print()

    result = {
        "backend": world.claim("BACKEND_COMPLETION").status,
        "frontend": world.claim(ID_FRONTEND_COMPLETION).status,
        "testing": world.claim(ID_TESTING_COMPLETION).status,
        "global_completion": gate,
        "hidden_integration": hidden,
        "global_false_completion": false_completion,
    }
    dump("experiment_1.json", result)
    return result


# ---------------------------------------------------------------------------
# Experiment 2: evidence invalidation prevents the false completion
# ---------------------------------------------------------------------------

def experiment_2():
    header("Experiment 2: Evidence Invalidation")
    world = build_world(strategy=Strategy.CHANGE_AWARE)

    before_f = world.claim(ID_FRONTEND_COMPLETION).status
    before_t = world.claim(ID_TESTING_COMPLETION).status
    # apply the change WITHOUT auto-revalidation to expose the STALE state first
    event = world.apply_change(ID_API_SCHEMA, SCHEMA_V2_BREAKING,
                               ChangeType.BREAKING)
    gate_after = global_completion(world)
    after_f = world.claim(ID_FRONTEND_COMPLETION).status
    after_t = world.claim(ID_TESTING_COMPLETION).status
    frontend_status = world.revalidators[ID_FRONTEND_COMPLETION](world)

    print("[Invalidation]")
    print(f"API schema changed: v{event['old_version']} -> v{event['new_version']} "
          f"({event['change_type']})")
    print(f"Frontend claim: {before_f} -> {after_f}")
    print(f"Testing claim: {before_t} -> {after_t}")
    print(f"Global Completion: {gate_after}")
    print(f"Frontend revalidation: {frontend_status}")

    false_completion = is_false_completion(world)
    prevented = gate_after == "NOT_READY" and frontend_status == "FAILED"
    print(f"False Completion Prevented: {str(prevented and not false_completion).upper()}")
    print()

    result = {
        "event": event,
        "global_completion_after_change": gate_after,
        "frontend_revalidation": frontend_status,
        "global_false_completion": false_completion,
        "false_completion_prevented": prevented and not false_completion,
    }
    dump("experiment_2.json", result)
    return result


# ---------------------------------------------------------------------------
# Experiment 3: all-downstream vs static vs change-aware invalidation
# ---------------------------------------------------------------------------

def experiment_3():
    header("Experiment 3: Invalidation Precision")
    table = []
    for strategy in ALL_STRATEGIES:
        tp = fp = fn = tn = revalidations = 0
        for case in CHANGE_CASES:
            world = build_world(strategy=strategy)
            event = CoordinatorAgent.apply_change(
                world, case["artifact"], case["new_content"])
            assert event["change_type"] == case["expected_change_type"].value, \
                f"classifier wrong on {case['case_id']}: {event['change_type']}"
            got = set(event["invalidated"]) & ALL_CLAIM_SLOTS
            gt = case["gt_invalidated"]
            tp += len(got & gt)
            fp += len(got - gt)
            fn += len(gt - got)
            tn += len(ALL_CLAIM_SLOTS - got - gt)
            revalidations += len(event["invalidated"])
        precision = tp / (tp + fp) if tp + fp else 1.0
        recall = tp / (tp + fn) if tp + fn else 1.0
        table.append({
            "strategy": strategy,
            "missed_invalidations": fn,
            "false_invalidations": fp,
            "revalidation_count": revalidations,
            "precision": round(precision, 2),
            "recall": round(recall, 2),
        })

    print(f"{'Strategy':<21} {'Missed':>6} {'FalseInv':>8} {'Reverify':>8} "
          f"{'Precision':>9} {'Recall':>6}")
    for row in table:
        print(f"{row['strategy']:<21} {row['missed_invalidations']:>6} "
              f"{row['false_invalidations']:>8} {row['revalidation_count']:>8} "
              f"{row['precision']:>9.2f} {row['recall']:>6.2f}")
    print()

    best = next(r for r in table if r["strategy"] == Strategy.CHANGE_AWARE)
    baseline = next(r for r in table if r["strategy"] == Strategy.ALL_DOWNSTREAM)
    print(f"=> Change-aware avoids {baseline['revalidation_count'] - best['revalidation_count']} "
          f"unnecessary revalidations vs all-downstream "
          f"({baseline['revalidation_count']} -> {best['revalidation_count']}), "
          f"with no missed invalidations.")
    print()

    dump("experiment_3.json", table)
    return table


# ---------------------------------------------------------------------------
# Experiment 4: missing dependency -> badcase -> candidate -> replay -> accept
# ---------------------------------------------------------------------------

def experiment_4():
    header("Experiment 4: Badcase Dependency Recovery")

    # --- produce the badcase: replay the breaking change without the edge ---
    world = build_world(strategy=Strategy.CHANGE_AWARE, graph=broken_graph())
    claims_before = Badcase.claims_view(world)
    snapshot = world.snapshot()
    old_version = world.artifact(ID_API_SCHEMA).version

    event = CoordinatorAgent.apply_change(world, ID_API_SCHEMA, SCHEMA_V2_BREAKING)
    # snapshot()['dependencies'] comes from the world itself
    badcase = Badcase(
        run_id="run-missing-frontend-dep-0001",
        changed_artifact=ID_API_SCHEMA,
        old_version=old_version,
        new_version=event["new_version"],
        change_type=event["change_type"],
        new_content=SCHEMA_V2_BREAKING,
        completion_claims_before_change=claims_before,
        completion_claims_after_change=Badcase.claims_view(world),
        hidden_test_result=hidden_integration_check(world)["result"],
        global_completion=global_completion(world),
        global_false_completion=is_false_completion(world),
        current_dependencies=snapshot["dependencies"],
        strategy=Strategy.CHANGE_AWARE,
        world_snapshot=snapshot,
    )
    print("Missing dependency:")
    print(f"{ID_API_SCHEMA} -> {ID_FRONTEND_COMPLETION}")
    print()
    print(f"Testing: {claims_before[ID_TESTING_COMPLETION]['status']} -> STALE "
          f"-> revalidated {world.claim(ID_TESTING_COMPLETION).status}")
    print(f"Frontend claim stays: {world.claim(ID_FRONTEND_COMPLETION).status}")
    print(f"Global Completion: {badcase.global_completion}")
    print(f"Hidden Integration Test: {badcase.hidden_test_result}")
    print(f"Badcase recorded: global_false_completion = "
          f"{str(badcase.global_false_completion).upper()}")
    print()

    # --- candidate generation (deterministic, rule + trace based) ---
    candidates = generate_candidates(badcase)
    print("Candidate generated:")
    for c in candidates:
        print(f"{c.source} -> {c.target}  (scope: {', '.join(c.scope)})")
        print(f"Reason: {c.reason}")
    print()

    # --- counterfactual replay ---
    print("[Counterfactual Replay]")
    replay_a = replay(badcase)
    print("\nWithout patch:")
    print(f"Global False Completion = {str(replay_a.global_false_completion).upper()}")
    replay_b = replay(badcase, [c.to_dependency() for c in candidates])
    print("\nWith candidate dependency:")
    print(f"Frontend VERIFIED -> STALE: "
          f"{str(ID_FRONTEND_COMPLETION in replay_b.invalidated).upper()}")
    print(f"Global Completion -> {replay_b.global_completion}")
    print(f"Frontend revalidation: "
          f"{replay_b.revalidated.get(ID_FRONTEND_COMPLETION, 'not run')}")
    print(f"Global False Completion = {str(replay_b.global_false_completion).upper()}")
    prevented = (replay_a.global_false_completion
                 and not replay_b.global_false_completion)
    print(f"\nCandidate prevented failure = {str(prevented).upper()}")
    print()

    # --- regression gate ---
    regression = evaluate_regressions([c for c in candidates])
    accepted = prevented and regression["passes"]
    print("Regression suite (patched graph):")
    for case in regression["cases"]:
        print(f"  {case['case_id']:<24} change={case['change_type']:<20} "
              f"invalidated={case['invalidated']} gt={case['ground_truth']} "
              f"correct={case['correct']}")
    print(f"invalidation_precision = {regression['invalidation_precision']}")
    print(f"invalidation_recall    = {regression['invalidation_recall']}")
    print(f"false_invalidation_rate = {regression['false_invalidation_rate']} "
          f"(threshold {regression['fir_threshold']})")
    print()
    print("Patch decision:")
    print("ACCEPTED" if accepted else "REJECTED")
    print()

    result = {
        "badcase": badcase.to_dict(),
        "candidates": [c.to_dict() for c in candidates],
        "replay_without_patch": replay_a.to_dict(),
        "replay_with_patch": replay_b.to_dict(),
        "candidate_prevented_failure": prevented,
        "regression": regression,
        "patch_decision": "ACCEPTED" if accepted else "REJECTED",
    }
    dump("experiment_4.json", result)
    return result


def main():
    random.seed(SEED)
    r1 = experiment_1()
    r2 = experiment_2()
    r3 = experiment_3()
    r4 = experiment_4()

    header("Summary")
    print(f"1. Global False Completion reproduced: "
          f"{str(r1['global_false_completion']).upper()}")
    print(f"2. Invalidation prevented it:          "
          f"{str(r2['false_completion_prevented']).upper()}")
    aware = next(r for r in r3 if r['strategy'] == Strategy.CHANGE_AWARE)
    baseline3 = next(r for r in r3 if r['strategy'] == Strategy.ALL_DOWNSTREAM)
    print(f"3. Change-aware cuts revalidations:    "
          f"{str(aware['revalidation_count'] < baseline3['revalidation_count']).upper()} "
          f"({baseline3['revalidation_count']} -> {aware['revalidation_count']}, "
          f"missed={aware['missed_invalidations']})")
    print(f"4. Candidate patch:                    {r4['patch_decision']}")


if __name__ == "__main__":
    main()
