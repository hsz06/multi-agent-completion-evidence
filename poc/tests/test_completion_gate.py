"""Completion gate + hidden integration oracle tests."""
from agents.coordinator import CoordinatorAgent
from agents.backend import SCHEMA_V2_BREAKING
from core.completion_gate import (
    global_completion, hidden_integration_check, is_false_completion,
    VERIFIED, NOT_READY, FAILED,
)
from core.invalidation import Strategy
from core.models import ID_API_SCHEMA
from scenarios.shared import build_world


def test_initial_global_verified_and_hidden_passes():
    world = build_world(strategy=Strategy.NONE)
    assert global_completion(world) == VERIFIED
    assert hidden_integration_check(world)["result"] == "PASSED"
    assert not is_false_completion(world)


def test_baseline_reproduces_global_false_completion():
    # no invalidation: all claims stay VERIFIED over a broken contract
    world = build_world(strategy=Strategy.NONE)
    CoordinatorAgent.apply_change(world, ID_API_SCHEMA, SCHEMA_V2_BREAKING)
    assert global_completion(world) == VERIFIED
    hidden = hidden_integration_check(world)
    assert hidden["result"] == "FAILED"
    assert hidden["missing_fields"] == ["name"]
    assert is_false_completion(world)


def test_invalidation_blocks_gate_with_not_ready():
    world = build_world(strategy=Strategy.CHANGE_AWARE)
    from core.models import ChangeType
    world.apply_change(ID_API_SCHEMA, SCHEMA_V2_BREAKING, ChangeType.BREAKING)
    assert global_completion(world) == NOT_READY
    assert not is_false_completion(world)


def test_gate_failed_when_any_claim_failed():
    world = build_world(strategy=Strategy.CHANGE_AWARE)
    # full cycle: stale -> frontend revalidation fails (still reads user.name)
    CoordinatorAgent.apply_change(world, ID_API_SCHEMA, SCHEMA_V2_BREAKING)
    assert global_completion(world) == FAILED
    assert not is_false_completion(world)  # failure is surfaced, not hidden
