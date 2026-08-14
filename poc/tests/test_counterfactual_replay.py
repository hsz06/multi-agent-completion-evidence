"""Badcase -> candidate generation -> counterfactual replay -> regression gate."""
import pytest

from agents.coordinator import CoordinatorAgent
from agents.backend import SCHEMA_V2_BREAKING
from badcase.candidate_generator import generate_candidates
from badcase.model import Badcase
from badcase.replay import replay, replay_with_patch, evaluate_regressions
from core.completion_gate import global_completion, hidden_integration_check, is_false_completion
from core.invalidation import Strategy
from core.models import (
    ID_API_SCHEMA, ID_FRONTEND_COMPLETION, ID_TESTING_COMPLETION,
)
from scenarios.shared import build_world, broken_graph


@pytest.fixture
def badcase():
    world = build_world(strategy=Strategy.CHANGE_AWARE, graph=broken_graph())
    before = Badcase.claims_view(world)
    snapshot = world.snapshot()
    event = CoordinatorAgent.apply_change(world, ID_API_SCHEMA, SCHEMA_V2_BREAKING)
    bc = Badcase(
        run_id="test-missing-dep",
        changed_artifact=ID_API_SCHEMA,
        old_version=1,
        new_version=event["new_version"],
        change_type=event["change_type"],
        new_content=SCHEMA_V2_BREAKING,
        completion_claims_before_change=before,
        completion_claims_after_change=Badcase.claims_view(world),
        hidden_test_result=hidden_integration_check(world)["result"],
        global_completion=global_completion(world),
        global_false_completion=is_false_completion(world),
        current_dependencies=snapshot["dependencies"],
        strategy=Strategy.CHANGE_AWARE,
        world_snapshot=snapshot,
    )
    return bc


def test_missing_dependency_produces_badcase(badcase):
    # testing adapts and re-passes; frontend's stale claim survives
    assert badcase.completion_claims_after_change[ID_FRONTEND_COMPLETION]["status"] == "VERIFIED"
    assert badcase.completion_claims_after_change[ID_TESTING_COMPLETION]["status"] == "VERIFIED"
    assert badcase.global_false_completion is True


def test_candidate_generation_finds_exact_missing_edge(badcase):
    candidates = generate_candidates(badcase)
    assert len(candidates) == 1
    c = candidates[0]
    assert c.source == ID_API_SCHEMA
    assert c.target == ID_FRONTEND_COMPLETION
    assert c.relation_type == "artifact->claim"
    assert "BREAKING" in c.scope
    assert "without invalidating" in c.reason


def test_replay_without_patch_reproduces_failure(badcase):
    result = replay(badcase)
    assert result.global_false_completion is True


def test_replay_with_candidate_prevents_failure(badcase):
    candidates = generate_candidates(badcase)
    result = replay(badcase, [c.to_dependency() for c in candidates])
    assert ID_FRONTEND_COMPLETION in result.invalidated
    assert result.global_false_completion is False
    assert replay_with_patch(badcase, candidates[0].to_dependency()) is True


def test_regression_gate_accepts_candidate(badcase):
    candidates = generate_candidates(badcase)
    regression = evaluate_regressions(candidates)
    assert regression["false_invalidation_rate"] <= regression["fir_threshold"]
    assert regression["invalidation_recall"] == 1.0
    accepted = (replay_with_patch(badcase, candidates[0].to_dependency())
                and regression["passes"])
    assert accepted is True


def test_replay_is_deterministic(badcase):
    candidates = generate_candidates(badcase)
    r1 = replay(badcase, [c.to_dependency() for c in candidates]).to_dict()
    r2 = replay(badcase, [c.to_dependency() for c in candidates]).to_dict()
    assert r1 == r2
