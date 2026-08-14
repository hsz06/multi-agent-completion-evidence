"""Invalidation engine + change classifier tests."""
from agents.coordinator import CoordinatorAgent
from core.change_analysis import classify_api_change, classify_code_change
from core.invalidation import Strategy
from core.models import (
    ChangeType, ClaimStatus,
    ID_API_SCHEMA, ID_BACKEND_CODE, ID_FRONTEND_COMPLETION, ID_TESTING_COMPLETION,
)
from agents.backend import (
    SCHEMA_V1, SCHEMA_V2_BREAKING, SCHEMA_V2_COMPATIBLE,
    BACKEND_CODE_V1, BACKEND_CODE_V2_LOG,
)
from scenarios.shared import build_world, CHANGE_CASES, ALL_CLAIM_SLOTS


# --- change classification (deterministic ground truth) ---------------------

def test_classify_breaking_rename():
    assert classify_api_change(SCHEMA_V1, SCHEMA_V2_BREAKING) == ChangeType.BREAKING


def test_classify_optional_field_addition():
    assert classify_api_change(SCHEMA_V1, SCHEMA_V2_COMPATIBLE) \
        == ChangeType.BACKWARD_COMPATIBLE


def test_classify_log_only_change():
    assert classify_code_change(BACKEND_CODE_V1, BACKEND_CODE_V2_LOG) \
        == ChangeType.NON_SEMANTIC


# --- strategies on the 3 cases ----------------------------------------------

def _run(strategy, case):
    world = build_world(strategy=strategy)
    event = CoordinatorAgent.apply_change(
        world, case["artifact"], case["new_content"])
    return set(event["invalidated"]) & ALL_CLAIM_SLOTS


def test_all_downstream_over_invalidates():
    # every case invalidates both downstream claims, even harmless ones
    for case in CHANGE_CASES:
        assert _run(Strategy.ALL_DOWNSTREAM, case) == ALL_CLAIM_SLOTS


def test_static_invalidates_on_any_schema_change_but_not_code_change():
    got = {case["case_id"]: _run(Strategy.STATIC, case) for case in CHANGE_CASES}
    assert got["caseA_harmless_log"] == set()
    assert got["caseB_breaking_rename"] == ALL_CLAIM_SLOTS
    assert got["caseC_optional_field"] == ALL_CLAIM_SLOTS  # false invalidation


def test_change_aware_matches_ground_truth():
    for case in CHANGE_CASES:
        assert _run(Strategy.CHANGE_AWARE, case) == case["gt_invalidated"], \
            case["case_id"]


def test_change_aware_reduces_revalidation_vs_all_downstream():
    def total(strategy):
        count = 0
        for case in CHANGE_CASES:
            world = build_world(strategy=strategy)
            event = CoordinatorAgent.apply_change(
                world, case["artifact"], case["new_content"])
            count += len(event["invalidated"])
        return count

    assert total(Strategy.CHANGE_AWARE) < total(Strategy.ALL_DOWNSTREAM)


def test_claims_transition_verified_to_stale():
    world = build_world(strategy=Strategy.CHANGE_AWARE)
    world.apply_change(ID_API_SCHEMA, SCHEMA_V2_BREAKING, ChangeType.BREAKING)
    assert world.claim(ID_FRONTEND_COMPLETION).status == ClaimStatus.STALE.value
    assert world.claim(ID_TESTING_COMPLETION).status == ClaimStatus.STALE.value


def test_backend_code_change_never_touches_downstream_via_graph():
    world = build_world(strategy=Strategy.CHANGE_AWARE)
    event = world.apply_change(ID_BACKEND_CODE, BACKEND_CODE_V2_LOG,
                               ChangeType.NON_SEMANTIC)
    assert event["invalidated"] == []
