"""Deterministic unit tests for the RealRepo-PoC v2 core.

No network, no LLM. These exercise the classifier, static extractor, G*
builder, the engine gate/invalidation, and the badcase candidate/rank logic
against the already-calibrated oracle artifacts.
"""
import json
from pathlib import Path

import pytest

from common.classifier import ChangeClassifier, classify_schema_change, extract_signatures
from common.models import ChangeType, Status, RelType, Dependency, Provenance
from common.extract_static import StaticDependencyExtractor
from experiment_engine import (
    make_world, replay_case, global_status, ground_truth_invalidation,
    load_oracle, find_oracle_case, GLOBAL_OK, GLOBAL_NOT_READY, GLOBAL_FAILED,
)
from change_cases import CASES, REPO_VERIFY_SETS


# --- classifier -------------------------------------------------------------

def test_classifier_no_change():
    c = ChangeClassifier()
    assert c.classify("def f(a): return a\n", "def f(a): return a\n") \
        == ChangeType.NON_SEMANTIC


def test_classifier_add_required_param_is_breaking():
    c = ChangeClassifier()
    s1 = "def insert(document): ...\n"
    s2 = "def insert(document, *, tag): ...\n"
    assert c.classify(s1, s2) == ChangeType.BREAKING


def test_classifier_add_optional_param_is_compatible():
    c = ChangeClassifier()
    s1 = "def insert(document): ...\n"
    s2 = "def insert(document, tag=None): ...\n"
    assert c.classify(s1, s2) == ChangeType.COMPATIBLE


def test_classifier_symbol_removed_is_breaking():
    c = ChangeClassifier()
    s1 = "def all(self): ...\ndef get(self): ...\n"
    s2 = "def all(self): ...\n"
    assert c.classify(s1, s2) == ChangeType.BREAKING


def test_classifier_body_only_is_potentially_breaking():
    c = ChangeClassifier()
    s1 = "def all(self):\n    return list(iter(self))\n"
    s2 = "def all(self):\n    return {d.doc_id: d for d in iter(self)}\n"
    assert c.classify(s1, s2) == ChangeType.POTENTIALLY_BREAKING


def test_classifier_tracks_dunder_init():
    s1 = "class D:\n    def __init__(self, v, i):\n        pass\n"
    s2 = "class D:\n    def __init__(self, v, i, c):\n        pass\n"
    # added required param `c` on a dunder -> BREAKING
    assert ChangeClassifier().classify(s1, s2) == ChangeType.BREAKING


def test_schema_diff_breaking_rename():
    old = {"fields": {"name": "str"}, "required": ["name"]}
    new = {"fields": {"username": "str"}, "required": ["username"]}
    assert classify_schema_change(old, new) == ChangeType.BREAKING


def test_schema_diff_optional_addition_compatible():
    old = {"fields": {"name": "str"}, "required": ["name"]}
    new = {"fields": {"name": "str", "avatar": "str|null"}, "required": ["name"]}
    assert classify_schema_change(old, new) == ChangeType.COMPATIBLE


# --- static extractor -------------------------------------------------------

def test_static_extractor_finds_tinydb_import_edges():
    ext = StaticDependencyExtractor(Path("repos/tinydb"), "tinydb")
    edges = ext.extract()
    pairs = {(e.source, e.target) for e in edges}
    assert ("tinydb/database.py", "tinydb/table.py") in pairs
    assert ("tinydb/table.py", "tinydb/queries.py") in pairs


def test_static_extractor_finds_cerberus_import_edges():
    ext = StaticDependencyExtractor(Path("repos/cerberus"), "cerberus")
    pairs = {(e.source, e.target) for e in ext.extract()}
    assert ("cerberus/validator.py", "cerberus/schema.py") in pairs
    assert ("cerberus/schema.py", "cerberus/utils.py") in pairs


# --- engine: gate & invalidation -------------------------------------------

def _gstar(repo, config="extended"):
    p = Path(f"ground_truth/{config}/{repo}/dependencies.json")
    return [Dependency.from_dict(e) for e in json.load(open(p))["edges"]]


def test_global_gate_all_verified():
    world = make_world("tinydb", _gstar("tinydb"), strategy="change_aware")
    assert global_status(world) == GLOBAL_OK


def test_replay_breaking_with_complete_graph_blocks_gfc():
    # tinydb/T3 under EXTENDED: complete graph invalidates testing -> reval FAILS
    g = _gstar("tinydb")
    world = make_world("tinydb", g, strategy="change_aware")
    oracle = load_oracle(extended=True)
    case = next(c for c in CASES if c["case_id"] == "T3_insert_sig")
    oc = find_oracle_case(oracle, "tinydb", "T3_insert_sig")
    trace = replay_case(world, case, oc)
    # with complete graph, testing is invalidated and revalidated FAILED
    assert "testing_completion" in trace["invalidated"]
    assert trace["global_after"] == GLOBAL_FAILED
    assert trace["global_false_completion"] is False


def test_missing_edge_produces_gfc_on_extended():
    # delete the only A->C edge; now testing stays VERIFIED -> GFC
    from badcase_model import load_gstar, delete_edges
    g = load_gstar("tinydb")
    g_hat, deleted = delete_edges(g, 1.0, 42)
    assert deleted  # something was deleted
    world = make_world("tinydb", g_hat, strategy="change_aware")
    oracle = load_oracle(extended=True)
    case = next(c for c in CASES if c["case_id"] == "T3_insert_sig")
    oc = find_oracle_case(oracle, "tinydb", "T3_insert_sig")
    trace = replay_case(world, case, oc)
    assert trace["global_false_completion"] is True


def test_ground_truth_invalidation_excludes_producer():
    oracle = load_oracle(extended=True)
    case = next(c for c in CASES if c["case_id"] == "T6_doc_required_attr")
    oc = find_oracle_case(oracle, "tinydb", "T6_doc_required_attr")
    gt = ground_truth_invalidation(case, oc)
    assert "agent_a_completion" not in gt   # producer self-heals


# --- badcase candidate generation ------------------------------------------

def test_candidate_generators_produce_completion_edges():
    from badcase_model import (
        load_gstar, delete_edges, make_badcases, gen_trace_heuristic,
        gen_static_only, gen_dynamic_only, gen_combined,
    )
    g = load_gstar("tinydb")
    g_hat, _ = delete_edges(g, 1.0, 42)
    oracle = load_oracle(extended=True)
    bcs = make_badcases(g_hat, "tinydb", oracle, 42, 1.0)
    assert bcs, "expected at least one badcase after deleting the single edge"
    b = bcs[0]
    # trace heuristic proposes completion-type candidates only
    for c in gen_trace_heuristic(b, g_hat):
        assert c.relation_type == "ARTIFACT->COMPLETION"
    # combined ranks a completion candidate at top
    comb = gen_combined(b, g_hat)
    assert comb[0].relation_type == "ARTIFACT->COMPLETION"


def test_counterfactual_replay_prevents_and_rejects(tmp_env=None):
    from badcase_model import (
        load_gstar, delete_edges, make_badcases, gen_combined,
        counterfactual_replay,
    )
    g = load_gstar("tinydb")
    g_hat, _ = delete_edges(g, 1.0, 42)
    oracle = load_oracle(extended=True)
    bcs = make_badcases(g_hat, "tinydb", oracle, 42, 1.0)
    b = next(x for x in bcs if x["case_id"] == "T3_insert_sig")
    comb = gen_combined(b, g_hat)
    # the testing_completion candidate should prevent failure
    testing_cand = next(c for c in comb
                        if c.target == "testing_completion"
                        and c.relation_type == "ARTIFACT->COMPLETION")
    cf = counterfactual_replay(b, testing_cand, g_hat)
    assert cf["prevent_failure"] is True
    # the agent_b candidate (verify-set insensitive) should NOT prevent
    agentb_cand = next(c for c in comb
                       if c.target == "agent_b_completion"
                       and c.relation_type == "ARTIFACT->COMPLETION")
    cf2 = counterfactual_replay(b, agentb_cand, g_hat)
    assert cf2["prevent_failure"] is False


# --- artifact invariants ----------------------------------------------------

def test_claim_model_does_not_carry_dependency_answer():
    # spec section 4: CompletionClaim must not carry based_on_artifact_versions
    from common.models import CompletionClaim
    fields = CompletionClaim.__dataclass_fields__
    assert "based_on_artifact_versions" not in fields
    assert {"claim_id", "task_id", "agent_id", "status",
            "produced_artifacts", "evidence_ids", "created_at"} <= set(fields)


def test_evidence_records_execution_facts():
    from common.models import Evidence
    fields = Evidence.__dataclass_fields__
    assert {"command", "files_observed", "files_modified", "test_targets",
            "result", "artifact_hashes"} <= set(fields)
    # only a subset are required by the dataclass; the rest are optional


def test_repos_present():
    for r in ("tinydb", "cerberus", "boltons"):
        assert Path(f"repos/{r}").is_dir()
    assert Path("oracle_calibrated.json").exists()
    assert Path("oracle_calibrated_extended.json").exists()